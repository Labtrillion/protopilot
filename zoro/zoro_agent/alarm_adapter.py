"""경보 이벤트 원본(zoro-alarm-event-v1) → EventInput 어댑터.

원본에 없는 값은 만들지 않는다.
- snapshot_id 는 원본에 없으므로 호출자가 시뮬레이터에서 받아 넘긴다.
- 태그별 측정 시각이 없으므로 process_state 값의 측정 시각은 이벤트 시각으로 둔다(경고로 기록).
- 품질 정보가 없으므로 quality 는 호출자가 정한다(기본 uncertain → 신선도 unknown).
- simulation_context.disturbance_id 는 평가 정답이므로 모델 입력으로 넘기지 않는다.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from .common import DataQuality, Model
from .inputs import Alarm, EventInput, MissingInfo, Observation, SafetyCheck

_LEVEL = {"HI": "high", "HIHI": "high_high", "HH": "high_high", "LO": "low", "LOLO": "low_low", "LL": "low_low"}
_TAG = re.compile(r"^(XMEAS|XMV)\(?(\d+)\)?$")


class AlarmAdapterResult(Model):
    event: EventInput
    warnings: list[str]
    dropped_fields: list[str]


def canonical_tag(raw: str) -> str:
    """'XMEAS7' / 'XMEAS(7)' → 'XMEAS(7)'. 형식이 다르면 그대로 둔다."""
    m = _TAG.match(raw.strip())
    return f"{m[1]}({int(m[2])})" if m else raw


def adapt_alarm_event(raw: dict[str, Any], *, snapshot_id: str,
                      quality_if_missing: Literal["good", "uncertain"] = "uncertain",
                      units: dict[str, str] | None = None,
                      safety_checks: list[SafetyCheck] | None = None,
                      active_setpoints: list | None = None) -> AlarmAdapterResult:
    if raw.get("schema_version") != "zoro-alarm-event-v1":
        raise ValueError(f"지원하지 않는 schema_version: {raw.get('schema_version')}")
    t = float(raw["simulation_time_seconds"])
    warnings: list[str] = []
    dropped: list[str] = []
    ctx = raw.get("simulation_context") or {}
    if ctx:
        dropped.append("simulation_context")  # disturbance_id 등 평가 전용 정보
        if ctx.get("disturbance_id") is not None:
            warnings.append("simulation_context.disturbance_id 는 평가 정답이므로 제거함")
    if raw.get("recent_changes"):
        dropped.append("recent_changes")
        warnings.append("recent_changes 형식 미합의 — 반영하지 않음")

    units = dict(units or {})
    alarms = []
    for a in raw.get("alarms", []):
        tag = canonical_tag(a["canonical_id"])
        level = str(a.get("alarm_level", "")).upper()
        cond = _LEVEL.get(level, "other")
        if cond == "other":
            warnings.append(f"{a['alarm_id']}: 알 수 없는 alarm_level {level!r} → other")
        if a.get("unit"):
            units.setdefault(tag, a["unit"])
        alarms.append(Alarm(
            alarm_id=a["alarm_id"], sensor_id=tag, condition=cond,
            raised_at_s=t,  # 원본에 활성 시각이 없음: 이 이벤트 시각
            active=a.get("status") == "active", priority=None,
            message=a.get("name"), threshold=a.get("threshold"), unit=a.get("unit"), raw_tag=a.get("raw_tag")))
    if raw.get("event_type") != "alarm_activated":
        warnings.append(f"event_type={raw.get('event_type')}: 경보 활성 시각을 이벤트 시각으로 둠")

    q = DataQuality(quality_if_missing)
    obs, no_unit = [], []
    for k, v in (raw.get("process_state") or {}).items():
        tag = canonical_tag(k)
        if tag not in units:
            no_unit.append(tag)
            continue
        obs.append(Observation(sensor_id=tag, value=None if v is None else float(v), unit=units[tag],
                               measured_at_s=t, quality=q))
    if no_unit:
        warnings.append(f"단위 미제공으로 관측에서 제외: {no_unit}")
    warnings.append("태그별 측정 시각 없음 → 이벤트 시각을 측정 시각으로 사용")
    if quality_if_missing == "good":
        warnings.append("품질 정보 없음 → 호출자 지정으로 good 처리")
    missing = [MissingInfo(item=f"{tag} 단위", reason="원본 이벤트에 단위 없음", sensor_id=None) for tag in no_unit]
    ev = EventInput(event_id=raw["event_id"], state_version=0, sim_time_s=t, snapshot_id=snapshot_id,
                    alarms=alarms, observations=obs, safety_checks=list(safety_checks or []),
                    missing_info=missing, active_setpoints=list(active_setpoints or []))
    return AlarmAdapterResult(event=ev, warnings=warnings, dropped_fields=dropped)
