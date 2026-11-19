"""Minute-based scenarios (protopilot-scenario-1) that people write by hand.

A document lists operations by elapsed minute; `MinuteScenario.to_live()` turns it into the control-step
`protopilot-live-1` document that `scenario.live_scenario()` validates and `Plant` replays. Operation fields
(`op`, `id`, `value`, `low`) are the live-1 ones and are validated there, not here. Keys the simulator does not
use (`title`, `context`, `agent`, `assumptions`, ...) may stay in the document and are dropped on conversion.

    {"format": "protopilot-scenario-1", "duration_min": 300,
     "alarm_limits": {"XMEAS(7)": [null, 2725]},
     "events": [{"at_min": 120, "op": "offset", "id": "XMEAS(9)", "value": 32, "duration_min": 2}]}
"""

from dataclasses import dataclass, field
from typing import ClassVar

from .scenario import DEFAULT_FLAGS, DEFAULT_SEED, LIVE_FORMAT, STEP_HOURS, numeric

MINUTES_FORMAT = "protopilot-scenario-1"
MAX_MINUTES = 10**7


def to_steps(minutes):
    return round(minutes / 60 / STEP_HOURS)


def read_minutes(value, label):
    try:
        return numeric(value, 0, MAX_MINUTES)
    except ValueError as error:
        raise ValueError(f"{label}: {error}") from None


@dataclass(frozen=True)
class ScenarioEvent:
    """One operation at `at_min`; with `duration_min` it is undone that many minutes later."""

    META_KEYS: ClassVar[frozenset] = frozenset({"at_min", "duration_min", "note"})

    at_min: float
    change: dict  # a live-1 timeline entry without its step
    duration_min: float | None = None

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("시나리오 event 형식을 확인하세요.")
        if "step" in raw:
            raise ValueError("분 단위 시나리오의 조작 시각은 at_min으로 입력하세요.")
        duration = read_minutes(raw["duration_min"], "duration_min") if "duration_min" in raw else None
        event = cls(read_minutes(raw.get("at_min"), "at_min"),
                    {key: value for key, value in raw.items() if key not in cls.META_KEYS}, duration)
        if duration is not None:
            event.undo()  # reject operations that cannot be undone before anything is replayed
        return event

    def undo(self):
        op, tag = self.change.get("op"), self.change.get("id")
        if op in ("disturbance", "offset"):
            return {"op": op, "id": tag, "value": 0}
        if op in ("setpoint", "mv"):
            return {"op": op, "id": tag, "value": None}  # back to the base setpoint / automatic control
        if op == "limit":
            return {"op": op, "id": tag, "low": None, "value": None}  # back to the default operating limit
        raise ValueError(f"{op} 조작에는 duration_min을 쓸 수 없습니다.")

    def timeline(self):
        start = to_steps(self.at_min)
        entries = [{"step": start, **self.change}]
        if self.duration_min is not None:
            # Even a sub-step duration keeps the operation for one step so the undo never precedes it.
            entries.append({"step": start + max(1, to_steps(self.duration_min)), **self.undo()})
        return entries


@dataclass(frozen=True)
class AlarmLimit:
    """A scenario-specific alarm limit, applied at step 0 like an operator's limit change."""

    tag: str
    low: float | None
    high: float | None

    @classmethod
    def from_item(cls, tag, bounds):
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError(f"{tag}의 alarm_limits는 [하한, 상한]이어야 합니다.")
        return cls(tag, *bounds)

    def timeline(self):
        return [{"step": 0, "op": "limit", "id": self.tag, "low": self.low, "value": self.high}]


@dataclass(frozen=True)
class MinuteScenario:
    duration_min: float
    events: tuple
    alarm_limits: tuple = ()
    seed: object = DEFAULT_SEED
    controller: dict = field(default_factory=dict)
    initial_speed: object = 960

    @staticmethod
    def accepts(document):
        return isinstance(document, dict) and document.get("format") == MINUTES_FORMAT

    @classmethod
    def from_document(cls, document):
        if not cls.accepts(document):
            raise ValueError(f"format이 {MINUTES_FORMAT}인 시나리오가 필요합니다.")
        events, limits = document.get("events"), document.get("alarm_limits", {})
        controller = document.get("controller", {})
        if not isinstance(events, list):
            raise ValueError("시나리오 events 목록이 필요합니다.")
        if not isinstance(limits, dict):
            raise ValueError("alarm_limits는 {태그: [하한, 상한]} 형식이어야 합니다.")
        if not isinstance(controller, dict):
            raise ValueError("시나리오의 제어 옵션을 확인하세요.")
        return cls(
            duration_min=read_minutes(document.get("duration_min"), "duration_min"),
            events=tuple(ScenarioEvent.from_dict(event) for event in events),
            alarm_limits=tuple(AlarmLimit.from_item(tag, bounds) for tag, bounds in limits.items()),
            seed=document.get("seed", DEFAULT_SEED), controller=controller,
            initial_speed=document.get("initial_speed", 960),
        )

    def timeline(self):
        entries = [entry for limit in self.alarm_limits for entry in limit.timeline()]
        entries += [entry for event in self.events for entry in event.timeline()]
        # sorted() is stable: operations at the same step keep the order they were written in.
        return sorted(entries, key=lambda entry: entry["step"])

    def to_live(self):
        return {
            "format": LIVE_FORMAT, "step_hours": STEP_HOURS, "seed": self.seed,
            "controller": {**DEFAULT_FLAGS, **self.controller}, "initial_speed": self.initial_speed,
            "through_step": to_steps(self.duration_min), "timeline": self.timeline(),
        }
