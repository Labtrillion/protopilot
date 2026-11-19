"""A single live Mode 1 simulator, advanced at the upstream control interval."""

import math
from collections import deque
from uuid import UUID
from dataclasses import replace

import numpy as np
from tep_studio import RickerMultiLoopController, TEP_SCHEMA, TennesseeEastmanProcess

from .catalog import CHANNELS, DISTURBANCES, GROUPS, OPERATING_LIMITS, SETPOINTS, TRIPS
from .recording import Recording, timestamp

STEP_HOURS = 0.0005
TICK_SECONDS = 0.6
SPEEDS = {15: 5, 60: 20, 240: 80, 960: 320}


def numeric(value, lower=-1e9, upper=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("유한한 숫자를 입력하세요.")
    if not lower <= value <= upper:
        raise ValueError(f"{lower:g} ~ {upper:g} 범위 값을 입력하세요.")
    return float(value)


class Plant:
    def __init__(self, root):
        self.root = root
        self.version = 0
        self.speed = 60
        self.recording_pages = deque(maxlen=128)
        self.reset(running=True)

    def reset(self, running):
        self.sim = TennesseeEastmanProcess()
        self.controller = RickerMultiLoopController()
        self.measurements, info = self.sim.reset(mode="mode1", seed=1431655765)
        self.controller.reset(self.measurements, time=self.sim.time)
        self.base_setpoints = self.controller.setpoints
        self.xmv = info["implemented_action"]
        self.idv = np.zeros(len(TEP_SCHEMA.disturbances))
        self.manual = {}
        self.offsets = {}
        self.limits = dict(OPERATING_LIMITS)
        self.active = {}
        self.events = deque(maxlen=80)
        self.samples = 0
        self.running = running
        self.error = None
        self.shutdown = info["shutdown_status"]
        self.recording = Recording(self.root)
        self.capture(self.sim.kernel.outputs(self.sim.time, self.sim.state))

    def tick(self):
        for _ in range(SPEEDS[self.speed]):
            action, _ = self.controller.compute_action(self.measurements, time=self.sim.time)
            for index, value in self.manual.items():
                action[index] = value
            result = self.sim.advance(action, control_interval=STEP_HOURS, disturbances=self.idv)
            self.measurements = result.measurements
            self.xmv = result.implemented_action
            self.shutdown = result.shutdown_status
            if self.shutdown["terminated"]:
                self.running = False
                break
        self.capture(vars(result))

    def capture(self, data=None):
        if data is not None:
            groups = {**data, "manipulated_variables": self.xmv}
            self.native = {
                var.legacy_symbol: float(value) if math.isfinite(value) else None
                for group, _ in GROUPS
                for var, value in zip(getattr(TEP_SCHEMA, group), groups[group], strict=True)
            }
        native = self.native
        values = dict(native)
        for tag, offset in self.offsets.items():
            if native[tag] is not None:
                values[tag] += offset
        self.check_alarms(values)
        self.samples += 1
        self.version += 1
        self.latest = {
            "time_utc": timestamp(), "time_hours": round(self.sim.time, 8), "sample": self.samples,
            "values": values, "native_values": {tag: native[tag] for tag in self.offsets},
            "offsets": self.offsets.copy(), "disturbances": self.active_disturbances(),
            "setpoints": {name: getattr(self.controller.setpoints, name) for name in SETPOINTS},
            "actuators": {f"XMV({i + 1})": float(value) for i, value in enumerate(self.sim.state[38:50])},
        }
        self.recording.sample(self.latest)

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
        for key, (tag, side, value, bound) in hits.items():
            if key not in self.active:
                entry = CHANNELS[tag]
                label = entry["label"] + (" 상한 초과" if side == "high" else " 하한 미달")
                source = "센서 시험" if tag in self.offsets else "공정 계측"
                self.active[key] = self.event("warning", label, entry["area"], tag=tag, value=value, limit=bound, source=source)
        if self.shutdown["terminated"] and "trip" not in self.active:
            tag = f"XMEAS({TRIPS.get(int(self.shutdown['code']), 7)})"
            self.active["trip"] = self.event(
                "trip", f"{CHANNELS[tag]['label']} · 모델 트립", CHANNELS[tag]["area"],
                tag=tag, value=values[tag], source="TEP 커널", detail=self.shutdown["message"],
            )

    def status(self):
        return {
            **self.latest, "version": self.version, "run": self.recording.id, "speed": self.speed,
            "running": self.running, "shutdown": self.shutdown, "error": self.error,
            "alarms": list(self.active.values()), "events": list(self.events),
            "manual": {f"XMV({i + 1})": value for i, value in self.manual.items()},
            "limits": self.limits, "disturbances": self.active_disturbances(),
        }

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
        self.recording_pages.append(key)

    def command(self, body):
        op = body.get("op")
        if op == "reset":
            self.reset(running=False)
        elif op == "new_recording":
            self.rotate_recording(body.get("page_id"))
        elif op == "run":
            if self.shutdown["terminated"] or self.error:
                raise ValueError("초기화한 뒤 다시 실행하세요.")
            self.running = bool(body.get("enabled"))
        elif op == "speed":
            speed = numeric(body.get("value"))
            if speed not in SPEEDS:
                raise ValueError("지원하는 배속을 선택하세요.")
            self.speed = int(speed)
        elif op in {"mv", "setpoint", "disturbance", "offset", "limit", "pressure_test"}:
            self.adjust(op, body)
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

    def adjust(self, op, body):
        tag = body.get("id")
        value = body.get("value")
        if op == "mv" and tag in CHANNELS and CHANNELS[tag]["group"] == "manipulated_variables":
            i = int(tag[4:-1]) - 1
            if value is None:
                self.manual.pop(i, None)
            else:
                self.manual[i] = numeric(value, 0, 100)
            entry = CHANNELS[tag]
            self.event("change", entry["label"] + (" · 자동" if value is None else " · 수동"), entry["area"], tag=tag, value=value)
        elif op == "setpoint" and tag in SETPOINTS:
            label, _, area, lo, hi = SETPOINTS[tag]
            target = getattr(self.base_setpoints, tag) if value is None else numeric(value, lo, hi)
            self.controller.setpoints = replace(self.controller.setpoints, **{tag: target})
            self.event("change", label + " 설정값 변경", area, tag=tag, value=target)
            self.capture()
        elif op == "disturbance" and tag in {v.legacy_symbol for v in TEP_SCHEMA.disturbances}:
            i = int(tag[4:-1]) - 1
            activation = numeric(value, 0, 1)
            if activation not in {0, 1}:
                raise ValueError("외란 상태는 0 또는 1이어야 합니다.")
            self.idv[i] = int(activation)
            self.event("change", f"{tag} 외란 {'적용' if self.idv[i] else '해제'}", DISTURBANCES[i], tag=tag, value=int(self.idv[i]))
        elif op == "pressure_test":
            tag = "XMEAS(7)"
            # Force the virtual transmitter beyond the Table 6 warning threshold.
            native = self.native[tag]
            if native is None:
                raise ValueError("반응기 압력 데이터가 필요합니다.")
            self.offset(tag, max(200, 2960 - native))
        elif op == "offset" and tag in CHANNELS and CHANNELS[tag]["group"] in {"measurements", "additional_measurements"}:
            self.offset(tag, 0 if value is None else numeric(value))
        elif op == "limit" and tag in CHANNELS:
            low = None if body.get("low") is None else numeric(body["low"])
            high = None if value is None else numeric(value)
            if low is not None and high is not None and low >= high:
                raise ValueError("하한은 상한보다 작아야 합니다.")
            self.limits[tag] = OPERATING_LIMITS.get(tag, (None, None)) if low is None and high is None else (low, high)
            self.event("change", "알람 기준 변경", CHANNELS[tag]["area"], tag=tag, low=low, value=high)
            self.capture()
        else:
            raise ValueError("대상 태그 또는 설정값을 확인하세요.")

    def offset(self, tag, value):
        if value:
            self.offsets[tag] = value
        else:
            self.offsets.pop(tag, None)
        entry = CHANNELS[tag]
        self.event("change", entry["label"] + (" · 센서 오프셋" if value else " · 센서 시험 해제"), entry["area"], tag=tag, value=value)
        self.capture()
