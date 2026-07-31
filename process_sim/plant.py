"""Physics model for Cedar Hollow Pump Station.

Pure Python, no networking, no I/O beyond the optional demand-curve clock.
Deterministic given a starting state and a fixed sequence of commands/dt —
there is no random-number use in the model, so the same command sequence
always produces the same trajectory.

This module owns physics only. Control logic (auto start/stop, the
protective interlock, alarm annunciation) lives one layer up, in
process_sim/server.py, so that it can move to OpenPLC ladder logic later
without touching the physics.

Deliberately inaccurate as a hydraulic model — tuned so behavior is legible
on a dashboard (a tank overflowing looks like a tank overflowing), not for
real engineering analysis. See docs/limitations.md.
"""

from __future__ import annotations

import dataclasses
import math

TANK_VOLUME_L = 500_000.0  # 500 m^3
PUMP_CAPACITY_LPM = 4_000.0  # inflow at 100% speed
NOMINAL_CURRENT_A = 31.5
DEADHEAD_CURRENT_A = 42.0
DEADHEAD_TRIP_S = 900.0  # time spent deadheaded before the overload latches out

CL_DOSE_TAU_S = 60.0  # first-order lag toward the chlorine dose target
CL_DECAY_K = 0.0006  # base decay rate, consumption grows with flow

DEMAND_BASE_LPM = 600.0
DEMAND_MORNING_PEAK_LPM = 1200.0
DEMAND_EVENING_PEAK_LPM = 1000.0
DEMAND_MORNING_CENTER = 0.29  # fraction of day, ~7am
DEMAND_MORNING_WIDTH = 0.03
DEMAND_EVENING_CENTER = 0.79  # fraction of day, ~7pm
DEMAND_EVENING_WIDTH = 0.04

SECONDS_PER_DAY = 24 * 60 * 60.0


def demand_lpm(day_fraction: float) -> float:
    """Diurnal discharge demand curve. `day_fraction` wraps in [0, 1)."""
    day_fraction = day_fraction % 1.0
    morning = math.exp(
        -((day_fraction - DEMAND_MORNING_CENTER) ** 2) / (2 * DEMAND_MORNING_WIDTH**2)
    )
    evening = math.exp(
        -((day_fraction - DEMAND_EVENING_CENTER) ** 2) / (2 * DEMAND_EVENING_WIDTH**2)
    )
    return DEMAND_BASE_LPM + DEMAND_MORNING_PEAK_LPM * morning + DEMAND_EVENING_PEAK_LPM * evening


@dataclasses.dataclass
class PlantState:
    sim_time_s: float = 0.0
    level_pct: float = 55.0
    chlorine_mg_l: float = 1.20
    pump_current_a: float = 0.0
    pump_fb: bool = False
    pump_fault: bool = False
    deadhead_s: float = 0.0
    flow_lpm: float = 0.0

    @property
    def lshh(self) -> bool:
        """High-high level float switch. Hardwired, independent of LT_101."""
        return self.level_pct >= 98.0

    @property
    def lsll(self) -> bool:
        """Low-low level float switch. Hardwired, independent of LT_101."""
        return self.level_pct <= 3.0


class Plant:
    """Cedar Hollow Pump Station physics.

    Call `step()` once per tick with the current commanded state; it
    mutates and returns `self.state`.
    """

    def __init__(self, state: PlantState | None = None) -> None:
        self.state = state or PlantState()

    def step(
        self,
        dt_s: float,
        *,
        pump_run: bool,
        valve_open: bool,
        cl_run: bool,
        pump_speed_pct: float,
        cl_dose_setpoint_mg_l: float,
    ) -> PlantState:
        s = self.state
        s.sim_time_s += dt_s

        s.flow_lpm = self._discharge_flow(s, valve_open)
        inflow_lpm = self._pump_inflow(pump_run, pump_speed_pct, s.pump_fault)

        net_lpm = inflow_lpm - s.flow_lpm
        net_liters = net_lpm * (dt_s / 60.0)
        unclamped_level = s.level_pct + (net_liters / TANK_VOLUME_L) * 100.0

        # Deadheading: the pump is running and trying to push more water in
        # than the (already-full) tank can hold. The tank itself can't
        # exceed 100% — the excess spills — but the pump keeps straining
        # against the closed system, which is what damages it.
        deadheading = pump_run and not s.pump_fault and unclamped_level > 100.0
        s.level_pct = min(100.0, max(0.0, unclamped_level))

        self._update_pump_electrical(s, pump_run, deadheading, dt_s)
        self._update_chlorine(s, cl_run, cl_dose_setpoint_mg_l, dt_s)

        return s

    @staticmethod
    def _discharge_flow(s: PlantState, valve_open: bool) -> float:
        if not valve_open:
            return 0.0
        return demand_lpm(s.sim_time_s / SECONDS_PER_DAY)

    @staticmethod
    def _pump_inflow(pump_run: bool, pump_speed_pct: float, pump_fault: bool) -> float:
        if not pump_run or pump_fault:
            return 0.0
        return PUMP_CAPACITY_LPM * (pump_speed_pct / 100.0)

    @staticmethod
    def _update_pump_electrical(
        s: PlantState, pump_run: bool, deadheading: bool, dt_s: float
    ) -> None:
        if s.pump_fault:
            s.pump_fb = False
            s.pump_current_a = 0.0
            return

        s.pump_fb = pump_run

        if not pump_run:
            s.pump_current_a = 0.0
            s.deadhead_s = 0.0
            return

        if deadheading:
            s.deadhead_s += dt_s
            fraction = min(1.0, s.deadhead_s / DEADHEAD_TRIP_S)
            s.pump_current_a = NOMINAL_CURRENT_A + fraction * (
                DEADHEAD_CURRENT_A - NOMINAL_CURRENT_A
            )
            if s.deadhead_s >= DEADHEAD_TRIP_S:
                s.pump_fault = True
                s.pump_fb = False
                s.pump_current_a = 0.0
        else:
            s.deadhead_s = 0.0
            s.pump_current_a = NOMINAL_CURRENT_A

    @staticmethod
    def _update_chlorine(
        s: PlantState, cl_run: bool, dose_setpoint_mg_l: float, dt_s: float
    ) -> None:
        target = dose_setpoint_mg_l if cl_run else 0.0
        alpha = 1.0 - math.exp(-dt_s / CL_DOSE_TAU_S)
        s.chlorine_mg_l += (target - s.chlorine_mg_l) * alpha

        decay_rate = CL_DECAY_K * (0.5 + s.flow_lpm / DEMAND_BASE_LPM)
        s.chlorine_mg_l -= s.chlorine_mg_l * decay_rate * dt_s
        s.chlorine_mg_l = max(0.0, s.chlorine_mg_l)
