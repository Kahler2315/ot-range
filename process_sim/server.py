"""Modbus TCP slave for Cedar Hollow Pump Station.

Default mode combines the point-map-driven pymodbus datastore with the
physics simulation (process_sim.plant.Plant) and an interim control loop
written as three independent rungs — the PLC and field device combined,
as they were before M1.5. Writing the rungs this way (auto level control
/ protective interlock / annunciation) is a direct translation target for
IEC 61131-3 ladder logic, now implemented for real in
plc/logic/cedar_hollow.st.

--field-only mode drops the control logic entirely and exposes just the
field I/O (plc/modbus-map-field.yml): actuator coils are raw commands
from whatever external master is polling (OpenPLC), sensor points are
read-only ground truth. This is the honest field-device half of the split
described in docs/openplc-integration.md.

Binds to loopback by default. Do not bind to 0.0.0.0 without understanding
that Modbus TCP has no authentication — anyone who can reach the port can
read and write every point.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

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
FIELD_MAP_PATH = Path(__file__).resolve().parent.parent / "plc" / "modbus-map-field.yml"

FC_COIL = 1
FC_DISCRETE_INPUT = 2
FC_HOLDING_REGISTER = 3
FC_INPUT_REGISTER = 4

# S05 (Manipulation of View): an undocumented holding register, not part
# of plc/modbus-map.yml or any legitimate operator screen. See
# PlantRunner._lt101_spoof_override for why this exists and what it
# represents. Deliberately far from the real point map's addresses
# (1-5) so it reads as "found via recon," not "next to the real points."
LT101_SPOOF_HR_INDEX = 99


def _table_size(pm: PointMap, table: str) -> int:
    points = pm.table(table)
    return max((p.index for p in points), default=-1) + 1


def build_context(
    pm: PointMap, *, min_holding_registers: int = 0
) -> tuple[ModbusServerContext, ModbusSlaveContext]:
    """Build the pymodbus datastore, sized and defaulted from the point map.

    min_holding_registers reserves extra holding-register slots beyond
    what the point map itself declares — used by the default (combined)
    mode to make room for S05's undocumented spoof register.
    """

    def block(table: str, is_bool: bool, min_size: int = 0) -> ModbusSequentialDataBlock:
        size = max(_table_size(pm, table), min_size)
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
        hr=block("holding_registers", is_bool=False, min_size=min_holding_registers),
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
        field_only: bool = False,
    ) -> None:
        self.pm = pm
        self.slave = slave
        self.speed = speed
        self.tick_s = tick_s
        self.field_only = field_only
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

    def _lt101_spoof_override(self) -> int | None:
        """S05 (Manipulation of View): an undocumented holding register
        that, if written nonzero, overrides what LT_101 reports on the
        wire from that tick on. Not part of plc/modbus-map.yml or any
        legitimate operator screen — an attacker only learns it exists
        via deep recon (S02-style project file exfiltration), mirroring
        T0856 Spoof Reporting Message.

        LT_101 can't be attacked with a simple unauthorized write the
        way S03 attacks coils: it's an FC04 input register, which the
        Modbus protocol makes read-only over the wire — there's no write
        function code that targets input registers at all. The lie has
        to be injected at the reporting device, not the message.
        """
        if self.field_only:
            return None
        raw = self.slave.getValues(FC_HOLDING_REGISTER, LT101_SPOOF_HR_INDEX, 1)[0]
        return raw if raw != 0 else None

    def _field_scan(self) -> tuple[bool, bool, bool, float, float]:
        """Field-only mode: no logic at all. Every actuator coil is a raw
        command from whatever external master is polling this device
        (OpenPLC) — read it as-is and drive the physics. Speed and dose
        are analog output commands, read the same way.
        """
        pump_run = self._get_coil("P101_RUN")
        valve_open = self._get_coil("V201_OPEN")
        cl_run = self._get_coil("CL301_RUN")
        sp_speed = self._get_holding("SP_P101_SPD")
        sp_dose = self._get_holding("SP_CL_DOSE")
        return pump_run, valve_open, cl_run, sp_speed, sp_dose

    def tick(self) -> None:
        if self.field_only:
            pump_run, valve_open, cl_run, sp_speed, sp_dose = self._field_scan()
        else:
            pump_run, valve_open, cl_run, sp_speed, sp_dose = self._control_scan()

        state = self.plant.step(
            self.tick_s,
            pump_run=pump_run,
            valve_open=valve_open,
            cl_run=cl_run,
            pump_speed_pct=sp_speed,
            cl_dose_setpoint_mg_l=sp_dose,
        )

        lt101_p = self.pm["LT_101"]
        spoof_raw = self._lt101_spoof_override()
        lt101_raw = spoof_raw if spoof_raw is not None else lt101_p.encode(state.level_pct)
        self.slave.setValues(FC_INPUT_REGISTER, lt101_p.index, [lt101_raw])

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
    map_path = args.map or (FIELD_MAP_PATH if args.field_only else None)
    pm = load_pointmap(map_path) if map_path else load_pointmap()
    min_hr = 0 if args.field_only else LT101_SPOOF_HR_INDEX + 1
    context, slave = build_context(pm, min_holding_registers=min_hr)
    runner = PlantRunner(
        pm,
        slave,
        speed=args.speed,
        tick_s=args.tick,
        initial_level_pct=args.level,
        field_only=args.field_only,
    )

    server = ModbusTcpServer(context, address=(args.host, args.port))
    LOG.info(
        "Modbus TCP slave on %s:%d (speed=%sx, tick=%ss, field_only=%s, map=%s)",
        args.host,
        args.port,
        args.speed,
        args.tick,
        args.field_only,
        pm.path,
    )

    await asyncio.gather(server.serve_forever(), runner.run_forever())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--field-only",
        action="store_true",
        help="Expose only field I/O, no control logic — for use behind an external "
        "controller such as OpenPLC (defaults the point map to modbus-map-field.yml)",
    )
    parser.add_argument(
        "--map",
        default=None,
        help="Override the point map path (default depends on --field-only)",
    )
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
