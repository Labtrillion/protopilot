from __future__ import annotations

from datetime import datetime, timezone

from zoro_rag.alarm.normalizer import as_utc, measurement_stale
from zoro_rag.ingestion.metadata import normalize_identifier
from zoro_rag.schemas import AlarmContext, AlarmEvent, Measurement, NormalizedAlarm, NormalizedMeasurement


class AlarmContextBuilder:
    KEYWORDS = ("reactor", "separator", "stripper", "condenser", "compressor", "purge", "feed", "recycle")

    # 시뮬레이터가 주입한 외란·고장 정보. 평가 정답이므로 검색과 결과에 쓰지 않는다.
    GROUND_TRUTH_KEYS = ("disturbance", "fault", "idv", "scenario", "injected", "hours_since_onset")

    def __init__(self, stale_after_seconds: int = 300, use_simulation_disturbance_id: bool = False):
        self.stale_after_seconds = stale_after_seconds
        # True 는 평가·디버그 전용. 운영·벤치마크에서는 False 여야 한다.
        self.use_simulation_disturbance_id = use_simulation_disturbance_id

    @classmethod
    def is_ground_truth(cls, key: str) -> bool:
        return any(word in key.lower() for word in cls.GROUND_TRUTH_KEYS)

    @classmethod
    def strip_ground_truth(cls, context: dict, changes: list[dict]) -> tuple[dict, list[dict], list[str]]:
        removed = [k for k in context if cls.is_ground_truth(k)]
        clean_context = {k: v for k, v in context.items() if not cls.is_ground_truth(k)}
        clean_changes = []
        for item in changes:
            if any(cls.is_ground_truth(k) or cls.is_ground_truth(str(v)) for k, v in item.items() if k in ("kind", "disturbance_id", "fault_id")):
                removed.append("recent_changes[]")
                continue
            clean_changes.append({k: v for k, v in item.items() if not cls.is_ground_truth(k)})
        return clean_context, clean_changes, removed

    def build(self, event: AlarmEvent, alarms: list[NormalizedAlarm]) -> AlarmContext:
        reference = event.timestamp or datetime.now(timezone.utc)
        measurements: dict[str, NormalizedMeasurement] = {}
        for raw_tag, observation in event.process_state.items():
            tag = normalize_identifier(raw_tag) or raw_tag
            if tag in measurements:
                raise ValueError(f"Duplicate process_state tags normalize to {tag}; use one observation per tag.")
            measurement = observation if isinstance(observation, Measurement) else Measurement(value=observation)
            measurements[tag] = NormalizedMeasurement(
                **measurement.model_dump(),
                stale=measurement_stale(measurement.measured_at, reference, self.stale_after_seconds),
            )
        known = []
        raw_disturbance = event.simulation_context.get("disturbance_id")
        normalized = normalize_identifier(str(raw_disturbance)) if raw_disturbance else None
        if self.use_simulation_disturbance_id and normalized and normalized.startswith("IDV_"):
            known.append(normalized)
        sim_context, recent_changes, removed = (
            (event.simulation_context.copy(), event.recent_changes, []) if self.use_simulation_disturbance_id
            else self.strip_ground_truth(event.simulation_context, event.recent_changes))
        equipment = set()
        missing = []
        for alarm in alarms:
            tag = alarm.normalized_tag
            observation = measurements.get(tag)
            if alarm.current_value is not None and tag and observation is None:
                measurements[tag] = NormalizedMeasurement(
                    value=alarm.current_value, unit=alarm.unit, measured_at=alarm.measured_at,
                    quality=alarm.quality, sample_id=alarm.sample_id, stale=alarm.stale,
                )
            elif observation is not None and alarm.current_value is not None:
                if alarm.unit and observation.unit and alarm.unit != observation.unit:
                    missing.append(f"Unit mismatch for {tag}: alarm={alarm.unit}, process_state={observation.unit}; no conversion performed.")
                elif observation.value != alarm.current_value:
                    missing.append(f"Conflicting alarm and process_state measurements for {tag}; both observations retained.")
                if alarm.sample_id and observation.sample_id and alarm.sample_id != observation.sample_id:
                    missing.append(f"Different analysis sample IDs for {tag}; alarm and process_state are separate observations.")
            if alarm.current_value is None and (observation is None or observation.value is None):
                missing.append(f"No current measurement supplied for {alarm.alarm_id}.")
            if not alarm.variable_definition and tag:
                missing.append(f"No local variable definition found for {tag}.")
            if alarm.current_value is not None or alarm.quality != "unknown" or alarm.measured_at is not None:
                missing.extend(self._measurement_issues(alarm.alarm_id, alarm, reference))
            if alarm.activated_at and as_utc(alarm.activated_at) > as_utc(reference):
                missing.append(f"Activation time for {alarm.alarm_id} is later than the event timestamp.")
            haystack = f"{alarm.alarm_id} {alarm.variable_definition or ''} {alarm.equipment or ''}".lower()
            equipment.update(item for item in self.KEYWORDS if item in haystack)
        for tag, measurement in measurements.items():
            missing.extend(self._measurement_issues(tag, measurement, reference))
        if event.active_objective and event.active_objective.applied_at:
            if as_utc(event.active_objective.applied_at) > as_utc(reference):
                missing.append("The reported active objective takes effect after the event timestamp; confirm the current objective.")
        return AlarmContext(
            event_timestamp=event.timestamp,
            snapshot_id=event.snapshot_id,
            active_objective=event.active_objective,
            simulation_context=sim_context,
            active_alarms=alarms,
            observed_variables={tag: m.value for tag, m in measurements.items()},
            measurements=measurements,
            known_disturbances=known,
            hypothesized_disturbances=[],
            affected_equipment=sorted(equipment),
            missing_information=list(dict.fromkeys(missing)),
            retrieval_priorities=["alarm interpretation", "variable definitions", "possible causes", "process relationships", "operating constraints"],
            recent_changes=recent_changes,
            withheld_ground_truth_fields=sorted(set(removed)),
        )

    @staticmethod
    def _measurement_issues(label, measurement, reference):
        issues = []
        if measurement.quality != "good":
            issues.append(f"Measurement quality for {label} is {measurement.quality}; do not assume a reliable observation.")
        if measurement.unit is None:
            issues.append(f"No measurement unit supplied for {label}.")
        if measurement.measured_at is None:
            issues.append(f"No measurement time supplied for {label}; freshness is unknown.")
        elif as_utc(measurement.measured_at) > as_utc(reference):
            issues.append(f"Measurement time for {label} is later than the event timestamp; freshness is unknown.")
        elif measurement.stale:
            issues.append(f"Measurement for {label} is stale.")
        if isinstance(measurement, NormalizedMeasurement) and measurement.value is None:
            issues.append(f"No measurement value supplied for {label}.")
        return issues
