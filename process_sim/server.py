"""Modbus TCP slave for Cedar Hollow Pump Station.

Combines the point-map-driven pymodbus datastore with the physics
simulation (process_sim.plant.Plant) and an interim control loop written
as three independent rungs. This is the PLC and field device combined for
now — the split into a real OpenPLC controller happens at M1.5. Writing
the rungs this way (auto level control / protective interlock /
annunciation) is a direct translation target for IEC 61131-3 ladder logic.

Binds to loopback by default. Do not bind to 0.0.0.0 without understanding
that Modbus TCP has no authentication — anyone who can reach the port can
read and write every point.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import ModbusTcpServer

from common.pointmap import PointMap
from common.pointmap import load as load_pointmap
from process_sim.plant import Plant, PlantState

LOG = logging.getLogger("ot_range.server")

DEFAULT_HOST = os.environ.get("MODBUS_BIND_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("MODBUS_BIND_PORT", "5502"))
DEFAULT_SPEED = 60.0  # sim-seconds per real second (1 sim day ~= 24 real minutes)
DEFAULT_TICK_S = 1.0  # sim-seconds advanced per control-loop scan

FC_COIL = 1
FC_DISCRETE_INPUT = 2
FC_HOLDING_REGISTER = 3
FC_INPUT_REGISTER = 4


def _table_size(pm: PointMap, table: str) -> int:
    points = pm.table(table)
    return max((p.index for p in points), default=-1) + 1


def build_context(pm: PointMap) -> tuple[ModbusServerContext, ModbusSlaveContext]:
    """Build the pymodbus datastore, sized and defaulted from the point map."""

    def block(table: str, is_bool: bool) -> ModbusSequentialDataBlock:
        size = _table_size(pm, table)
        values: list[bool | int] = [False if is_bool else 0] * size
        for p in pm.table(table):
            if p.default is None:
                continue
            values[p.index] = bool(p.default) if is_bool else p.encode(float(p.default))
        return ModbusSequentialDataBlock(0, values)

    slave = ModbusSlaveContext(
        co=block("coils", is_bool=True),
        di=block("discrete_inputs", is_bool=True),
        ir=block("input_registers", is_bool=False),
        hr=block("holding_registers", is_bool=False),
        zero_mode=True,
    )
    context = ModbusServerContext(slaves=slave, single=True)
    return context, slave


class PlantRunner:
    """Bridges the physics/control loop to the Modbus datastore."""

    def __init__(
        self,
        pm: PointMap,
        slave: ModbusSlaveContext,
        speed: float = DEFAULT_SPEED,
        tick_s: float = DEFAULT_TICK_S,
        initial_level_pct: float = 55.0,
    ) -> None:
        self.pm = pm
        self.slave = slave
        self.speed = speed
        self.tick_s = tick_s
        self.plant = Plant(PlantState(level_pct=initial_level_pct))

    def _get_coil(self, tag: str) -> bool:
        p = self.pm[tag]
        return bool(self.slave.getValues(FC_COIL, p.index, 1)[0])

    def _set_coil(self, tag: str, value: bool) -> None:
        p = self.pm[tag]
        self.slave.setValues(FC_COIL, p.index, [value])

    def _set_discrete_input(self, tag: str, value: bool) -> None:
        p = self.pm[tag]
        self.slave.setValues(FC_DISCRETE_INPUT, p.index, [value])

    def _get_holding(self, tag: str) -> float:
        p = self.pm[tag]
        raw = self.slave.getValues(FC_HOLDING_REGISTER, p.index, 1)[0]
        return p.decode(raw)

    def _set_input_register(self, tag: str, value: float) -> None:
        p = self.pm[tag]
        self.slave.setValues(FC_INPUT_REGISTER, p.index, [p.encode(value)])

    def _control_scan(self) -> tuple[bool, bool, bool, float, float]:
        """Three ladder-style rungs, evaluated against ground-truth physics.

        Rung 1 (level control): in auto mode, start the pump at the low
        setpoint and stop it at the high setpoint.
        Rung 2 (protective interlock): in auto mode, the hardwired
        high-high float unconditionally stops the pump. Manual mode
        deliberately bypasses this, mirroring real plants where an
        operator override can defeat an automatic safety action — this is
        what S06 removes permanently, even from auto mode.
        Rung 3 (annunciation): the alarm reflects ground truth regardless
        of mode — annunciation isn't a control action.
        """
        mode_auto = self._get_coil("MODE_AUTO")
        sp_lo = self._get_holding("SP_LVL_LO")
        sp_hi = self._get_holding("SP_LVL_HI")
        sp_alm_hh = self._get_holding("SP_ALM_HH")
        sp_speed = self._get_holding("SP_P101_SPD")
        sp_dose = self._get_holding("SP_CL_DOSE")

        pump_run = self._get_coil("P101_RUN")
        valve_open = self._get_coil("V201_OPEN")
        cl_run = self._get_coil("CL301_RUN")

        level_pct = self.plant.state.level_pct
        lshh = self.plant.state.lshh

        if mode_auto:
            if level_pct <= sp_lo:
                pump_run = True
            if level_pct >= sp_hi:
                pump_run = False
            if lshh:
                pump_run = False

        alarm = lshh or (level_pct >= sp_alm_hh)

        self._set_coil("P101_RUN", pump_run)
        self._set_coil("ALARM_HORN", alarm)

        return pump_run, valve_open, cl_run, sp_speed, sp_dose

    def tick(self) -> None:
        pump_run, valve_open, cl_run, sp_speed, sp_dose = self._control_scan()

        state = self.plant.step(
            self.tick_s,
            pump_run=pump_run,
            valve_open=valve_open,
            cl_run=cl_run,
            pump_speed_pct=sp_speed,
            cl_dose_setpoint_mg_l=sp_dose,
        )

        self._set_input_register("LT_101", state.level_pct)
        self._set_input_register("FT_201", state.flow_lpm)
        self._set_input_register("AIT_301", state.chlorine_mg_l)
        self._set_input_register("IT_101", state.pump_current_a)

        self._set_discrete_input("LSHH_101", state.lshh)
        self._set_discrete_input("LSLL_101", state.lsll)
        self._set_discrete_input("P101_FB", state.pump_fb)
        self._set_discrete_input("P101_FAULT", state.pump_fault)

    async def run_forever(self) -> None:
        real_interval_s = self.tick_s / self.speed
        while True:
            self.tick()
            await asyncio.sleep(real_interval_s)


async def main_async(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    pm = load_pointmap()
    context, slave = build_context(pm)
    runner = PlantRunner(
        pm, slave, speed=args.speed, tick_s=args.tick, initial_level_pct=args.level
    )

    server = ModbusTcpServer(context, address=(args.host, args.port))
    LOG.info(
        "Modbus TCP slave on %s:%d (speed=%sx, tick=%ss)",
        args.host,
        args.port,
        args.speed,
        args.tick,
    )

    await asyncio.gather(server.serve_forever(), runner.run_forever())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--speed",
        type=float,
        default=DEFAULT_SPEED,
        help="Sim-seconds advanced per real second (default: %(default)s)",
    )
    parser.add_argument(
        "--tick",
        type=float,
        default=DEFAULT_TICK_S,
        help="Sim-seconds advanced per control-loop scan (default: %(default)s)",
    )
    parser.add_argument(
        "--level",
        type=float,
        default=55.0,
        help="Initial tank level, percent (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
