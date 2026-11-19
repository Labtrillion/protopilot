"""A single live Mode 1 simulator, advanced at the upstream control interval."""

import math
from collections import deque
from copy import copy, deepcopy
from uuid import UUID
from dataclasses import asdict, dataclass, field, replace

import numpy as np
from tep_studio import RickerMultiLoopController, TEP_SCHEMA, TennesseeEastmanProcess
from tep_studio.control.metrics import MetricsAccumulator
from tep_studio.simulation.native import NativeSnapshot

from .agent_config import CONFIG
from .alarm_rules import AlarmMonitor
from .catalog import (CHANNELS, CONTROL_FLAGS, DISTURBANCES, GROUPS, MEASUREMENT_TAGS, OPERATING_LIMITS,
                      PROTECTIONS, SETPOINTS, TRIPS)
from .minute_scenario import MinuteScenario
from .recording import Recording, timestamp
from .scenario import ADJUSTMENTS, DEFAULT_FLAGS, DEFAULT_SEED, KERNEL_REVISION, LIVE_FORMAT, SPEEDS, STEP_HOURS, adjustment, live_scenario

TICK_SECONDS = 0.6
REACTION_FEED_VALVES = frozenset((0, 1, 2, 3))
COOLING_TAGS = ("XMEAS(9)", "XMEASADD(5)", "XMEASADD(6)", "XMV(10)")
SOP_HISTORY_HOURS = 2.0
# Operator-visible history for setpoint decisions (default: one sample per 3 simulated minutes over 2 h).
HISTORY_SAMPLE_HOURS = CONFIG["history_sample_hours"]
HISTORY_WINDOW_HOURS = CONFIG["history_window_hours"]


@dataclass(frozen=True)
class HistorySample:
    time_hours: float
    values: tuple  # XMEAS(1..41) as the operator sees them (sensor offsets included)
    commands: tuple  # requested XMV(1..12) commands, % of range
    protection: tuple  # active protection keys


@dataclass(frozen=True)
class AlarmCheckpoint:
    run_id: str
    step: int
    seed: int
    alarm: dict
    native: NativeSnapshot
    controller: RickerMultiLoopController
    measurements: np.ndarray
    manual: dict
    idv: np.ndarray
    limits: dict
    observed: dict
    cooling_trend: tuple
    related_alarms: tuple
    # AlarmMonitor copy at the alarm (counters at zero) and the sensor offsets then active: the agent reads its
    # alarms from the monitor's observed channel and branches keep counting alarm minutes from here.
    monitor: object = None
    offsets: dict = field(default_factory=dict)
    history: tuple = ()  # (time_hours, observed) samples for SOP alignment, newest last
    decision_history: tuple = ()
    base_setpoints: object = None
    timeline: tuple = ()


def control_action(controller, measurements, time, manual):
    action, control = controller.compute_action(measurements, time=time)
    protection = control.overrides_active
    limited = set()
    for index, value in manual.items():
        restricted = (index in REACTION_FEED_VALVES and protection.get("high_pressure_to_production")) or (
            index == 4 and protection.get("high_level_to_recycle")
        )
        if restricted and value > action[index]:
            limited.add(f"XMV({index + 1})")
        else:
            action[index] = value
    return action, control, limited


def finite_metrics(metrics):
    return {
        key: finite_metrics(value) if isinstance(value, dict)
        else None if isinstance(value, float) and not math.isfinite(value)
        else value
        for key, value in metrics.items()
    }


class Plant:
    def __init__(self, root):
        self.root = root
        self.version = 0
        self.speed = 60
        self.recording_pages = deque(maxlen=128)
        self.reset(running=True)

    def reset(self, running, *, scenario=None):
        self.seed = scenario["seed"] if scenario else DEFAULT_SEED
        self.speed = scenario["initial_speed"] if scenario else self.speed
        self.initial_speed = self.speed
        self.initial_flags = dict(scenario["controller"] if scenario else DEFAULT_FLAGS)
        self.step = 0
        self.replay_end = scenario["through_step"] if scenario else None
        self.timeline = []
        self.pending = deque(scenario["timeline"] if scenario else ())
        self.sim = TennesseeEastmanProcess()
        self.controller = RickerMultiLoopController(**{f"enable_{key}": value for key, value in self.initial_flags.items()})
        self.measurements, info = self.sim.reset(mode="mode1", seed=self.seed)
        self.controller.reset(self.measurements, time=self.sim.time)
        self.base_setpoints = self.controller.setpoints
        self.metrics = MetricsAccumulator(setpoints=self.controller.setpoints)
        self.xmv = info["implemented_action"]
        self.idv = np.zeros(len(TEP_SCHEMA.disturbances))
        self.disturbance_onsets = {}
        self.manual = {}
        self.offsets = {}
        self.limits = dict(OPERATING_LIMITS)
        self.alarm_monitor = AlarmMonitor(self.limits)  # agent/scoring alarms; the console list is check_alarms
        self.active = {}
        self.events = deque(maxlen=80)
        self.alarm_checkpoints = {}
        self.cooling_trend = deque(maxlen=8)
        self.sop_history = deque()
        self.history = deque()  # sampled setpoint-decision history
        self.rollout = None  # server.rollout.Rollout while a baseline comparison runs
        self.samples = 0
        self.running = running
        self.error = None
        self.shutdown = info["shutdown_status"]
        self.protection = {}
        self.limited_manual = set()
        self.control = None
        self.requested = self.xmv
        self.recording = Recording(self.root)
        self.output = self.sim.kernel.outputs(self.sim.time, self.sim.state)
        self.apply_scheduled()
        self.capture()
        self.recording.save_scenario(self.scenario())

    def tick(self):
        self.apply_scheduled()
        for _ in range(SPEEDS[self.speed]):
            if self.step == self.replay_end:
                self.running = False
                break
            action, self.control, self.limited_manual = control_action(
                self.controller, self.measurements, self.sim.time, self.manual,
            )
            self.update_protection(self.control.overrides_active)
            result = self.sim.advance(action, control_interval=STEP_HOURS, disturbances=self.idv)
            self.metrics.update(result)
            self.alarm_monitor.update(self.sim.time, result.measurements, self.offsets, self.controller.setpoints,
                                      STEP_HOURS)
            if self.rollout is not None:
                self.rollout.live.update(result, any(self.control.overrides_active.values()))
            self.measurements = result.measurements
            self.xmv = result.implemented_action
            self.requested = result.requested_action
            self.shutdown = result.shutdown_status
            self.output = vars(result)
            self.step += 1
            self.apply_scheduled()
            if self.shutdown["terminated"]:
                self.running = False
                break
        if self.step == self.replay_end:
            self.running = False
        self.capture()
        if not self.running:
            self.recording.save_scenario(self.scenario())

    def apply_scheduled(self):
        while self.pending and self.pending[0]["step"] == self.step:
            item = self.pending[0]
            self.adjust(item["op"], item)
            self.timeline.append(self.pending.popleft())

    def update_protection(self, active):
        for key, (label, tag) in PROTECTIONS.items():
            enabled = bool(active.get(key))
            if enabled != bool(self.protection.get(key)):
                self.event("protection", f"{label} · {'개입' if enabled else '해제'}", "reactor", tag=tag,
                           value=float(self.measurements[int(tag[6:-1]) - 1]), active=enabled)
            self.protection[key] = enabled
        if not any(self.protection.values()):
            self.limited_manual.clear()

    def capture(self):
        groups = {**self.output, "manipulated_variables": self.xmv}
        self.native = {
            var.legacy_symbol: float(value) if math.isfinite(value) else None
            for group, _ in GROUPS
            for var, value in zip(getattr(TEP_SCHEMA, group), groups[group], strict=True)
        }
        native = self.native
        a, c = native["XMEAS(23)"], native["XMEAS(25)"]
        total = a + c if a is not None and c is not None else None
        values = dict(native)
        for tag, offset in self.offsets.items():
            if native[tag] is not None:
                values[tag] += offset
        # SOP predicates can use actual valve positions as well as observed signals.
        observed = {**values, **{f"XMV({i + 1}):actual": float(v) for i, v in enumerate(self.sim.state[38:50])}}
        now = round(self.sim.time, 8)
        if self.sop_history and self.sop_history[-1][0] == now:
            self.sop_history.pop()
        self.sop_history.append((now, observed))
        while now - self.sop_history[0][0] > SOP_HISTORY_HOURS:
            self.sop_history.popleft()
        cooling = {"time_hours": now,
                   **{tag: observed[tag] for tag in (*COOLING_TAGS, "XMV(10):actual")}}
        if self.cooling_trend and self.cooling_trend[-1]["time_hours"] == cooling["time_hours"]:
            self.cooling_trend.pop()
        self.cooling_trend.append(cooling)
        self.record_history(values)
        raised = self.check_alarms(values)
        if raised:
            self.record_alarm_checkpoints(raised, observed)
        self.samples += 1
        self.version += 1
        self.latest = {
            "time_utc": timestamp(), "time_hours": round(self.sim.time, 8), "sample": self.samples, "step": self.step,
            "values": values, "native_values": {tag: native[tag] for tag in self.offsets},
            "offsets": self.offsets.copy(), "disturbances": self.active_disturbances(),
            "setpoints": {name: getattr(self.controller.setpoints, name) for name in SETPOINTS},
            "actuators": {f"XMV({i + 1})": float(value) for i, value in enumerate(self.sim.state[38:50])},
            "state": {var.name: float(value) if math.isfinite(value) else None
                      for var, value in zip(TEP_SCHEMA.states, self.sim.state, strict=True)},
            "requested_action": {f"XMV({i + 1})": float(value) for i, value in enumerate(self.requested)},
            "implemented_action": {f"XMV({i + 1})": float(value) for i, value in enumerate(self.xmv)},
            "hours_since_onset": {tag: round(self.sim.time - start, 8) for tag, start in self.disturbance_onsets.items()},
            "constraint_margins": {key: float(value) if math.isfinite(value) else None
                                   for key, value in TEP_SCHEMA.constraint_margins(self.measurements).items()},
            "derived": {"YA": 100.0 * a / total if total else total, "YAC": total},
            "controller_flags": {key: getattr(self.controller, f"enable_{key}") for key in CONTROL_FLAGS},
            "control": asdict(self.control) if self.control is not None else None,
            "protection": self.protection.copy(), "limited_manual": sorted(self.limited_manual),
            "shutdown": dict(self.shutdown),
            "metrics": finite_metrics(self.metrics.finalize()),
        }
        self.recording.sample(self.latest)

    def record_history(self, values):
        now = self.sim.time
        if self.history and now - self.history[-1].time_hours < HISTORY_SAMPLE_HOURS - 1e-9:
            return
        self.history.append(HistorySample(
            round(now, 8), tuple(values[tag] for tag in MEASUREMENT_TAGS),
            tuple(float(value) for value in self.requested),
            tuple(sorted(key for key, active in self.protection.items() if active)),
        ))
        while now - self.history[0].time_hours > HISTORY_WINDOW_HOURS + 1e-9:
            self.history.popleft()

    def active_disturbances(self):
        return [f"IDV({index + 1})" for index in np.flatnonzero(self.idv)]

    def event(self, kind, label, area, **fields):
        event = {
            "id": self.version, "time_utc": timestamp(), "time_hours": round(self.sim.time, 8),
            "kind": kind, "label": label, "area": area, **fields,
        }
        self.version += 1
        self.events.appendleft(event)
        self.recording.event(event)
        return event

    def check_alarms(self, values):
        hits = {}
        for tag, (low, high) in self.limits.items():
            value = values[tag]
            if value is None:
                continue
            for side, bound in (("low", low), ("high", high)):
                if bound is None:
                    continue
                # Hysteresis keeps a noisy signal near the threshold from repeating alarms.
                key = f"{tag}/{side}"
                deadband = max(abs(bound) * 0.001, 0.00001) if key in self.active else 0
                if (side == "low" and value < bound + deadband) or (side == "high" and value > bound - deadband):
                    hits[key] = (tag, side, value, bound)
        for key in self.active.keys() - hits.keys() - {"trip"}:
            previous = self.active.pop(key)
            self.event("clear", f"{previous['label']} 복구", previous["area"], tag=previous["tag"])
        raised = []
        for key, (tag, side, value, bound) in hits.items():
            if key not in self.active:
                entry = CHANNELS[tag]
                label = entry["label"] + (" 상한 초과" if side == "high" else " 하한 미달")
                source = "센서 시험" if tag in self.offsets else "공정 계측"
                self.active[key] = self.event("warning", label, entry["area"], tag=tag, value=value,
                                              limit=bound, side=side, source=source)
                raised.append(self.active[key])
        if self.shutdown["terminated"] and "trip" not in self.active:
            tag = f"XMEAS({TRIPS.get(int(self.shutdown['code']), 7)})"
            self.active["trip"] = self.event(
                "trip", f"{CHANNELS[tag]['label']} · 모델 트립", CHANNELS[tag]["area"],
                tag=tag, value=values[tag], source="TEP 커널", detail=self.shutdown["message"],
            )
            raised.append(self.active["trip"])
        return raised

    def record_alarm_checkpoints(self, alarms, observed):
        snapshot = self.sim.snapshot()
        controller = deepcopy(self.controller)
        related = tuple(dict(alarm) for alarm in self.active.values())
        decision_history, timeline = tuple(self.history), tuple(self.timeline)
        for alarm in alarms:
            self.alarm_checkpoints[alarm["id"]] = AlarmCheckpoint(
                self.recording.id, self.step, self.seed, dict(alarm), snapshot, controller,
                self.measurements.copy(), self.manual.copy(), self.idv.copy(), self.limits.copy(),
                observed, tuple(self.cooling_trend), related,
                monitor=self.alarm_monitor.copy(), offsets=self.offsets.copy(), history=tuple(self.sop_history),
                decision_history=decision_history, base_setpoints=self.base_setpoints, timeline=timeline,
            )
        while len(self.alarm_checkpoints) > self.events.maxlen:
            active_ids = {alarm["id"] for alarm in self.active.values()}
            expired = next((key for key in self.alarm_checkpoints if key not in active_ids), None)
            if expired is None:
                break
            del self.alarm_checkpoints[expired]

    def status(self):
        return {
            **self.latest, "version": self.version, "run": self.recording.id, "speed": self.speed,
            "running": self.running, "shutdown": self.shutdown, "error": self.error,
            "alarms": list(self.active.values()), "events": list(self.events),
            "response_events": list(self.alarm_checkpoints),
            "manual": {f"XMV({i + 1})": value for i, value in self.manual.items()},
            "limits": self.limits, "disturbances": self.active_disturbances(),
            "replay_end": self.replay_end, "scheduled_changes": len(self.pending),
            "step_hours": STEP_HOURS,
        }

    def scenario(self):
        return {"format": LIVE_FORMAT, "kernel_revision": KERNEL_REVISION, "step_hours": STEP_HOURS, "seed": self.seed,
                "controller": self.initial_flags, "initial_speed": self.initial_speed,
                "through_step": max(self.step, self.replay_end or 0),
                "timeline": [*self.timeline, *self.pending]}

    def rotate_recording(self, page_id):
        if not isinstance(page_id, str):
            raise ValueError("화면 식별자가 필요합니다.")
        key = UUID(page_id).hex
        if key in self.recording_pages:
            return
        recording = Recording(self.root)
        self.recording = recording
        self.samples = 0
        self.events.clear()
        self.event("change", "화면 연결 · 새 로컬 기록", "plant")
        for alarm in self.active.values():
            self.events.appendleft(alarm)
            self.recording.event(alarm)
        self.capture()
        self.recording.save_scenario(self.scenario())
        self.recording_pages.append(key)

    def command(self, body):
        op = body.get("op")
        if not isinstance(op, str):
            raise ValueError("지원하지 않는 요청입니다.")
        if op == "reset":
            self.reset(running=False)
        elif op in {"replay_scenario", "cooling_demo"}:
            is_demo = op == "cooling_demo"
            document = body.get("scenario")
            # [ADDED] An uploaded minute-based scenario is converted first, then validated like a live-1 upload.
            if not is_demo and MinuteScenario.accepts(document):
                document = MinuteScenario.from_document(document).to_live()
            scenario = self.cooling_demo() if is_demo else live_scenario(document)
            candidate = copy(self)
            candidate.reset(running=is_demo, scenario=scenario)
            self.__dict__.update(candidate.__dict__)
        elif op == "new_recording":
            self.rotate_recording(body.get("page_id"))
        elif op == "run":
            if self.shutdown["terminated"] or self.error:
                raise ValueError("초기화한 뒤 다시 실행하세요.")
            if body.get("enabled") and self.step == self.replay_end:
                self.replay_end = None
            self.running = bool(body.get("enabled"))
            if not self.running:
                self.recording.save_scenario(self.scenario())
        elif op in ADJUSTMENTS:
            change = adjustment(body)
            self.adjust(op, change)
            self.timeline.append({"step": self.step, **change})
            self.recording.save_scenario(self.scenario())
        elif op == "ack":
            alarm = next((a for a in self.active.values() if a["id"] == body.get("id")), None)
            if alarm is None:
                raise ValueError("이미 해제된 알람입니다.")
            if not alarm.get("ack"):
                alarm["ack"] = True
                self.event("ack", "알람 확인", alarm["area"], tag=alarm["tag"], alarm=alarm["id"])
        else:
            raise ValueError("지원하지 않는 요청입니다.")
        self.version += 1

    def cooling_demo(self):
        # A narrower, demo-only indication makes the real model's temperature variation visible as an alarm.
        start = round(0.48 / STEP_HOURS)
        return live_scenario({
            "format": LIVE_FORMAT, "step_hours": STEP_HOURS, "seed": DEFAULT_SEED,
            "controller": DEFAULT_FLAGS, "initial_speed": 960, "through_step": round(0.8 / STEP_HOURS),
            "timeline": [
                {"step": start, "op": "disturbance", "id": "IDV(11)", "value": 1},
                {"step": start, "op": "disturbance", "id": "IDV(14)", "value": 1},
                {"step": start, "op": "limit", "id": "XMEAS(9)", "low": 119.9, "value": 120.9},
            ],
        })

    def adjust(self, op, body):
        tag = body.get("id")
        value = body.get("value")
        if op == "mv" and tag in CHANNELS and CHANNELS[tag]["group"] == "manipulated_variables":
            i = int(tag[4:-1]) - 1
            if value is None:
                self.manual.pop(i, None)
            else:
                self.manual[i] = value
            entry = CHANNELS[tag]
            self.event("change", entry["label"] + (" · 자동" if value is None else " · 수동"), entry["area"], tag=tag, value=value)
        elif op == "setpoint" and tag in SETPOINTS:
            label, _, area, _, _ = SETPOINTS[tag]
            previous = getattr(self.controller.setpoints, tag)
            target = getattr(self.base_setpoints, tag) if value is None else value
            self.controller.setpoints = replace(self.controller.setpoints, **{tag: target})
            self.metrics.setpoints = self.controller.setpoints
            fields = {"tag": tag, "value": target, "previous_value": previous}
            if tag == "reactor_pressure" and target != previous:
                # Preserve the native readings at this step even if a dashboard poll skips it.
                fields["separator_readings"] = finite_metrics({
                    "XMEAS(13)": float(self.measurements[12]),
                    "XMEAS(12)": float(self.measurements[11]),
                })
            self.event("change", label + " 설정값 변경", area, **fields)
            self.capture()
        elif op == "disturbance":
            i = int(tag[4:-1]) - 1
            if value:
                self.disturbance_onsets.setdefault(tag, self.sim.time)
            else:
                self.disturbance_onsets.pop(tag, None)
            self.idv[i] = value
            self.event("change", f"{tag} 외란 {'적용' if self.idv[i] else '해제'}", DISTURBANCES[i], tag=tag, value=int(self.idv[i]))
        elif op == "pressure_test":
            tag = "XMEAS(7)"
            # Force the virtual transmitter beyond the Table 6 warning threshold.
            native = self.measurements[6]
            if not math.isfinite(native):
                raise ValueError("반응기 압력 데이터가 필요합니다.")
            self.offset(tag, float(max(200, 2960 - native)))
        elif op == "offset":
            self.offset(tag, value)
        elif op == "limit":
            low, high = body["low"], value
            self.limits[tag] = OPERATING_LIMITS.get(tag, (None, None)) if low is None and high is None else (low, high)
            self.alarm_monitor.set_limits(self.limits)
            self.event("change", "알람 기준 변경", CHANNELS[tag]["area"], tag=tag, low=low, value=high)
            self.capture()
        elif op == "controller":
            setattr(self.controller, f"enable_{tag}", value)
            if tag == "overrides" and not value:
                self.update_protection({})
            self.event("change", f"{CONTROL_FLAGS[tag]} · {'켜짐' if value else '꺼짐'}", "reactor")
            self.capture()
        elif op == "speed":
            self.speed = value

    def offset(self, tag, value):
        if value:
            self.offsets[tag] = value
        else:
            self.offsets.pop(tag, None)
        entry = CHANNELS[tag]
        self.event("change", entry["label"] + (" · 센서 오프셋" if value else " · 센서 시험 해제"), entry["area"], tag=tag, value=value)
        self.capture()
