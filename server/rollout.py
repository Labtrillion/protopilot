"""Rollout evaluation: the live run against hold and rule baselines started from the same instant.

`start` copies the live plant (model state, RNG, controller memory, manual moves, active
disturbances) into one shadow plant per baseline. From then on every ENVIRONMENT change the
live run receives -- disturbances, manual valve moves, sensor offsets, alarm limits, controller
options -- is replayed on each shadow at the same control step. Setpoint changes are the policy
being evaluated, so they are not replayed: `hold` never changes a setpoint and `rule` makes its
own local-rule decision every `RULE_INTERVAL_HOURS`. Live and shadows integrate the same meters
(operating cost, produced volume, alarm minutes on true values) from the anchor, so `compare`
reports them over the same elapsed time.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace

import numpy as np
from tep_studio import TennesseeEastmanProcess

from .agent_config import CONFIG
from .branch_metrics import step_cost
from .catalog import OPERATING_LIMITS
from .plant import HISTORY_SAMPLE_HOURS, HISTORY_WINDOW_HOURS, MEASUREMENT_TAGS, HistorySample, control_action
from .scenario import STEP_HOURS
from setpoint_gate.setpoint_decision import DecisionPoint, local_rule, monitor_alarms, setpoint_table

RULE_INTERVAL_HOURS = CONFIG["rule_interval_hours"]
ENVIRONMENT_OPS = {"disturbance", "mv", "offset", "limit", "pressure_test", "controller"}


@dataclass
class Meter:
    """Running totals from the anchor; one per trajectory (live or a baseline)."""

    steps: int = 0
    cost: float = 0.0
    produced_m3: float = 0.0
    violation_steps: int = 0
    protection_steps: int = 0
    peak_pressure: float = float("-inf")
    setpoint_changes: int = 0
    shutdown: dict | None = None

    def update(self, result, protected):
        meas = result.measurements
        self.steps += 1
        self.cost += step_cost(result)
        self.produced_m3 += float(meas[16]) * STEP_HOURS
        self.violation_steps += int(outside_table6(meas))
        self.protection_steps += int(protected)
        self.peak_pressure = max(self.peak_pressure, float(meas[6]))
        if result.shutdown_status["terminated"] and self.shutdown is None:
            self.shutdown = dict(result.shutdown_status)

    def summary(self, reference, elapsed_hours, alarm):
        """`alarm`: the trajectory's AlarmMonitor summary over the same span (true and observed minutes)."""
        minutes = STEP_HOURS * 60
        return {
            "elapsed_hours": round(elapsed_hours, 6), "operating_cost": self.cost, "produced_m3": self.produced_m3,
            "production_loss_m3": reference * elapsed_hours - self.produced_m3,
            "cost_per_m3": self.cost / self.produced_m3 if self.produced_m3 > 0 else None,
            **alarm, "violation_minutes": self.violation_steps * minutes,
            "protection_minutes": self.protection_steps * minutes,
            "peak_pressure": self.peak_pressure if np.isfinite(self.peak_pressure) else None,
            "setpoint_changes": self.setpoint_changes, "shutdown": self.shutdown,
        }


def outside_table6(meas):
    """Any Downs & Vogel Table 6 operating limit exceeded (the fixed `violation_minutes` definition)."""
    for tag, (low, high) in OPERATING_LIMITS.items():
        value = float(meas[int(tag[6:-1]) - 1])
        if (low is not None and value < low) or (high is not None and value > high):
            return True
    return False


@dataclass
class Shadow:
    """One baseline plant advanced in lock-step with the live run's control steps."""

    policy: str
    sim: TennesseeEastmanProcess
    controller: object
    measurements: np.ndarray
    manual: dict
    idv: np.ndarray
    offsets: dict
    limits: dict
    base_setpoints: object
    seed: int
    run_id: str
    step: int
    history: list = field(default_factory=list)
    own_changes: list = field(default_factory=list)
    protection: tuple = ()
    requested: tuple = ()
    meter: Meter = field(default_factory=Meter)
    monitor: object = None  # AlarmMonitor copied from the live plant at the anchor, counters at zero
    next_decision_hours: float = 0.0

    def apply(self, change):
        op, tag, value = change["op"], change.get("id"), change.get("value")
        if op == "disturbance":
            self.idv[int(tag[4:-1]) - 1] = value
        elif op == "mv":
            i = int(tag[4:-1]) - 1
            if value is None:
                self.manual.pop(i, None)
            else:
                self.manual[i] = value
        elif op == "offset":
            if value:
                self.offsets[tag] = value
            else:
                self.offsets.pop(tag, None)
        elif op == "pressure_test":
            # Same rule as the live plant: push the virtual transmitter past the 2,960 kPa warning.
            self.offsets["XMEAS(7)"] = float(max(200, 2960 - self.measurements[6]))
        elif op == "limit":
            low = change.get("low")
            self.limits[tag] = OPERATING_LIMITS.get(tag, (None, None)) if low is None and value is None else (low, value)
            self.monitor.set_limits(self.limits)
        elif op == "controller":
            setattr(self.controller, f"enable_{tag}", value)

    def observed(self):
        values = {tag: float(self.measurements[i]) for i, tag in enumerate(MEASUREMENT_TAGS)}
        for tag, offset in self.offsets.items():
            if tag in values:
                values[tag] += offset
        return values

    def record_history(self):
        now = float(self.sim.time)
        if self.history and now - self.history[-1].time_hours < HISTORY_SAMPLE_HOURS - 1e-9:
            return
        values = self.observed()
        self.history.append(HistorySample(round(now, 8), tuple(values[t] for t in MEASUREMENT_TAGS),
                                          tuple(self.requested), self.protection))
        while now - self.history[0].time_hours > HISTORY_WINDOW_HOURS + 1e-9:
            self.history.pop(0)

    def decide(self):
        """The rule baseline's own decision, from what an operator of THIS shadow would observe."""
        point = DecisionPoint(self.run_id, self.step, self.seed, float(self.sim.time), None, self.controller,
                              self.measurements, self.manual, self.idv, tuple(self.history), self.base_setpoints,
                              tuple(self.own_changes), monitor_alarms(self.monitor), self.limits, self.offsets,
                              self.monitor)
        for change in local_rule(point, setpoint_table(point)):
            self.controller.setpoints = replace(self.controller.setpoints, **{change["setpoint"]: change["to"]})
            self.own_changes.append({"step": self.step, "op": "setpoint", "id": change["setpoint"], "value": change["to"]})
            self.meter.setpoint_changes += 1

    def advance_to(self, step, changes):
        """Advance to live control step `step`, applying the live environment changes keyed by step."""
        while self.step < step and self.meter.shutdown is None:
            for change in changes.get(self.step, ()):
                self.apply(change)
            if self.policy == "rule" and self.sim.time >= self.next_decision_hours - 1e-9 and len(self.history) >= 2:
                self.decide()
                self.next_decision_hours = float(self.sim.time) + RULE_INTERVAL_HOURS
            action, control, _ = control_action(self.controller, self.measurements, self.sim.time, self.manual)
            self.protection = tuple(sorted(k for k, v in control.overrides_active.items() if v))
            result = self.sim.advance(action, control_interval=STEP_HOURS, disturbances=self.idv)
            self.measurements = result.measurements
            self.requested = tuple(float(v) for v in result.requested_action)
            self.meter.update(result, bool(self.protection))
            self.monitor.update(self.sim.time, self.measurements, self.offsets, self.controller.setpoints, STEP_HOURS)
            self.step += 1
            self.record_history()


class Rollout:
    """Anchor + baselines + live meter. The live meter is advanced by `Plant.tick`; baselines catch up on `compare`."""

    def __init__(self, plant, baselines=CONFIG["baselines"]):
        self.anchor_step = plant.step
        self.anchor_hours = float(plant.sim.time)
        self.timeline_start = len(plant.timeline)
        self.reference = float(plant.controller.setpoints.production_rate)
        self.live = Meter()
        snapshot = plant.sim.snapshot()
        self.shadows = {}
        for policy in baselines:
            sim = TennesseeEastmanProcess()
            sim.restore(snapshot)
            self.shadows[policy] = Shadow(
                policy, sim, deepcopy(plant.controller), plant.measurements.copy(), plant.manual.copy(),
                plant.idv.copy(), plant.offsets.copy(), dict(plant.limits), plant.base_setpoints, plant.seed,
                plant.recording.id, plant.step, history=list(plant.history), requested=tuple(plant.requested),
                protection=tuple(sorted(k for k, v in plant.protection.items() if v)),
                monitor=plant.alarm_monitor.copy(), next_decision_hours=float(plant.sim.time),
            )
        self.live_alarm_start = deepcopy((plant.alarm_monitor.alarm_minutes, plant.alarm_monitor.rule_minutes))

    def environment_changes(self, timeline):
        changes = {}
        for change in timeline[self.timeline_start:]:
            if change["op"] in ENVIRONMENT_OPS:
                changes.setdefault(change["step"], []).append(change)
        return changes

    def snapshot(self, plant):
        """What `compare` needs from the live plant, copied under the plant's lock."""
        live = replace(self.live)
        live.setpoint_changes = sum(1 for c in plant.timeline[self.timeline_start:] if c["op"] == "setpoint")
        return plant.step, list(plant.timeline), (live, self.live_alarms(plant.alarm_monitor))

    def live_alarms(self, monitor):
        """The live monitor's minutes since the anchor, in AlarmMonitor.summary() form."""
        minutes0, rules0 = self.live_alarm_start
        now = {name: monitor.alarm_minutes[name] - minutes0[name] for name in monitor.alarm_minutes}
        by_rule = {name: {rule: m - rules0[name].get(rule, 0.0) for rule, m in monitor.rule_minutes[name].items()
                          if m - rules0[name].get(rule, 0.0) > 0} for name in monitor.rule_minutes}
        return {"alarm_minutes": round(now["true"], 6), "observed_alarm_minutes": round(now["observed"], 6),
                "alarm_minutes_by_rule": {k: round(v, 4) for k, v in by_rule["true"].items()},
                "observed_alarm_minutes_by_rule": {k: round(v, 4) for k, v in by_rule["observed"].items()}}

    def compare(self, step, timeline, live_state):
        """Bring every baseline to live control step `step` (outside the plant lock), then report all
        trajectories over the same elapsed time."""
        live, live_alarm = live_state
        changes = self.environment_changes(timeline)
        for shadow in self.shadows.values():
            shadow.advance_to(step, changes)
        elapsed = (step - self.anchor_step) * STEP_HOURS
        return {
            "anchor": {"step": self.anchor_step, "time_hours": round(self.anchor_hours, 8),
                       "production_target": self.reference},
            "live": live.summary(self.reference, elapsed, live_alarm),
            "baselines": {name: s.meter.summary(self.reference, elapsed, s.monitor.summary())
                          for name, s in self.shadows.items()},
        }

    def close(self):
        for shadow in self.shadows.values():
            shadow.sim.kernel.close()
