from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from zoro_rag.ingestion.metadata import normalize_identifier
from zoro_rag.schemas import AlarmEvent, NormalizedAlarm


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def measurement_stale(measured_at: datetime | None, reference: datetime, limit: int) -> bool | None:
    if measured_at is None:
        return None
    age = (as_utc(reference) - as_utc(measured_at)).total_seconds()
    return age > limit if age >= 0 else None


class AlarmNormalizer:
    def __init__(self, knowledge_root: Path, stale_after_seconds: int = 300):
        self.stale_after_seconds = stale_after_seconds
        self.variables = self._load_variables(knowledge_root)
        catalog_path = knowledge_root / "05_Disturbance_knowledge" / "idv_catalog.json"
        self.disturbances = {f"IDV_{item['id']}": item for item in json.loads(catalog_path.read_text(encoding="utf-8-sig"))} if catalog_path.exists() else {}

    @staticmethod
    def _load_variables(root: Path) -> dict[str, str]:
        variables: dict[str, str] = {}
        for path in root.rglob("*.md"):
            for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                match = re.search(r"`?(XMEAS|XMV)\((\d+)\)`?\s*\|\s*([^|]+)", line, re.I)
                if match:
                    variables[f"{match.group(1).upper()}_{int(match.group(2))}"] = match.group(3).strip()
        return variables

    def normalize(self, event: AlarmEvent) -> list[NormalizedAlarm]:
        now = event.timestamp or datetime.now(timezone.utc)
        result = []
        for alarm in event.alarms:
            tag = normalize_identifier(alarm.tag)
            measured_at = alarm.measured_at or alarm.timestamp
            stale = measurement_stale(measured_at, now, self.stale_after_seconds)
            facts = [f"Observed alarm {alarm.alarm_id} with severity {alarm.severity}."]
            if alarm.current_value is not None:
                facts.append(f"Observed {alarm.tag or 'value'} = {alarm.current_value}{' ' + alarm.unit if alarm.unit else ''}.")
            result.append(NormalizedAlarm(
                alarm_id=alarm.alarm_id,
                normalized_tag=tag,
                variable_definition=self.variables.get(tag or ""),
                severity=alarm.severity,
                current_value=alarm.current_value,
                unit=alarm.unit,
                equipment=alarm.equipment,
                stale=stale,
                timestamp=alarm.timestamp,
                threshold=alarm.threshold,
                direction=alarm.direction,
                measured_at=measured_at,
                quality=alarm.quality,
                sample_id=alarm.sample_id,
                activated_at=alarm.activated_at,
                deadband_clear_value=alarm.deadband_clear_value,
                facts=facts,
            ))
        return result

