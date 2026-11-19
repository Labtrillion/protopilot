from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvidenceType(str, Enum):
    PROCESS = "PROCESS"
    DISTURBANCE = "DISTURBANCE"
    MEASUREMENT = "MEASUREMENT"
    CONTROL = "CONTROL"
    SAFETY = "SAFETY"
    ECONOMIC = "ECONOMIC"
    ALARM = "ALARM"
    CASE_STUDY = "CASE_STUDY"


class DocumentBlock(BaseModel):
    block_type: Literal["paragraph", "heading", "table", "record"]
    text: str
    heading_path: list[str] = Field(default_factory=list)
    source_file: str
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    source_file: str
    source_type: str = "original_technical_reference"
    document_version: str | None = None  # 원본 파일 내용 해시 "sha256:<12>". 출처 판본 식별용
    procedure: dict[str, Any] = Field(default_factory=dict)
    source_url: str | None = None
    section: str | None = None
    page: int | None = None
    disturbance_ids: list[str] = Field(default_factory=list)
    variable_ids: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    evidence_types: list[EvidenceType] = Field(default_factory=list)
    inferred_metadata: list[str] = Field(default_factory=list)
    parsing_warnings: list[str] = Field(default_factory=list)
    token_count: int
    text: str


class Measurement(BaseModel):
    """One observation; its time is distinct from an alarm activation time."""
    model_config = ConfigDict(extra="forbid")
    value: float | str | None = None
    unit: str | None = None
    measured_at: datetime | None = None
    quality: Literal["good", "uncertain", "bad", "unknown"] = "unknown"
    sample_id: str | None = None

    @field_validator("quality", mode="before")
    @classmethod
    def normalize_quality(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class ActiveObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    direction: Literal["minimize", "maximize", "maintain"]
    target_value: float | str | None = None
    unit: str | None = None
    applied_at: datetime | None = None
    description: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


class AlarmSignal(BaseModel):
    model_config = ConfigDict(extra="allow")
    alarm_id: str
    tag: str | None = None
    severity: str = "unknown"
    current_value: float | str | None = None
    unit: str | None = None
    threshold: float | str | None = None
    direction: str | None = None
    equipment: str | None = None
    timestamp: datetime | None = None
    measured_at: datetime | None = None
    quality: Literal["good", "uncertain", "bad", "unknown"] = "unknown"
    sample_id: str | None = None
    activated_at: datetime | None = None
    deadband_clear_value: float | None = Field(default=None, allow_inf_nan=False)

    @field_validator("quality", mode="before")
    @classmethod
    def normalize_quality(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_measurement_time_and_deadband(self):
        if self.timestamp is not None and self.measured_at is not None:
            old = self.timestamp.replace(tzinfo=timezone.utc) if self.timestamp.tzinfo is None else self.timestamp
            new = self.measured_at.replace(tzinfo=timezone.utc) if self.measured_at.tzinfo is None else self.measured_at
            if old != new:
                raise ValueError("timestamp is the legacy measurement time; it must agree with measured_at.")
        if self.threshold is not None and self.deadband_clear_value is not None:
            try:
                threshold, clear = float(self.threshold), float(self.deadband_clear_value)
            except (TypeError, ValueError):
                return self
            if not math.isfinite(threshold):
                raise ValueError("A deadband comparison requires a finite numeric threshold.")
            direction = (self.direction or "").lower()
            if direction == "high" and clear > threshold:
                raise ValueError("A high alarm deadband_clear_value must be <= threshold.")
            if direction == "low" and clear < threshold:
                raise ValueError("A low alarm deadband_clear_value must be >= threshold.")
        return self


ALARM_LEVEL_DIRECTION = {"HI": "high", "HIHI": "high", "HH": "high", "LO": "low", "LOLO": "low", "LL": "low"}
SIMULATION_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def from_zoro_alarm_event_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """`zoro-alarm-event-v1`(canonical_id·alarm_level·simulation_time_seconds) → 이 패키지의 AlarmEvent 입력.

    시뮬레이션 초는 SIMULATION_EPOCH(1970-01-01 UTC) 기준 시각으로 옮긴다. 실제 벽시계 시각이 아니다.
    원본에 없는 측정 시각·품질·시료 ID 는 만들지 않는다(측정 시각은 이벤트 시각, 품질 unknown).
    simulation_context 는 그대로 넘기며, 정답 필드는 AlarmContextBuilder 가 제거한다.
    """
    t = raw.get("simulation_time_seconds")
    ts = SIMULATION_EPOCH + timedelta(seconds=float(t)) if t is not None else None
    alarms = []
    for a in raw.get("alarms", []):
        level = str(a.get("alarm_level") or "").upper()
        alarms.append({
            "alarm_id": a["alarm_id"], "tag": a.get("canonical_id") or a.get("raw_tag"),
            "severity": level or "unknown", "current_value": a.get("current_value"), "unit": a.get("unit"),
            "threshold": a.get("threshold"), "direction": ALARM_LEVEL_DIRECTION.get(level),
            "equipment": None, "measured_at": ts if a.get("current_value") is not None else None,
            "activated_at": ts if raw.get("event_type") == "alarm_activated" and a.get("status") == "active" else None,
            "raw_tag": a.get("raw_tag"), "alarm_name": a.get("name"), "alarm_status": a.get("status"),
        })
    out = {k: v for k, v in raw.items() if k not in ("schema_version", "alarms", "simulation_time_seconds")}
    out.update(event_id=raw["event_id"], timestamp=ts, alarms=alarms, source=raw.get("source", "unknown"),
               input_schema="zoro-alarm-event-v1", simulation_time_seconds=t, time_basis="simulation_seconds_since_epoch_utc")
    return out


class AlarmEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: str
    timestamp: datetime | None = None
    source: str = "unknown"
    alarms: list[AlarmSignal] = Field(min_length=1)
    process_state: dict[str, Measurement | float | str | None] = Field(default_factory=dict)
    recent_changes: list[dict[str, Any]] = Field(default_factory=list)
    simulation_context: dict[str, Any] = Field(default_factory=dict)
    snapshot_id: str | None = Field(default=None, min_length=1)
    active_objective: ActiveObjective | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_zoro_alarm_event_v1(cls, data):
        if isinstance(data, dict) and data.get("schema_version") == "zoro-alarm-event-v1":
            return from_zoro_alarm_event_v1(data)
        return data

    @model_validator(mode="after")
    def check_snapshot_reference(self):
        nested = self.simulation_context.get("snapshot_id")
        if nested is not None:
            if not isinstance(nested, str) or not nested.strip():
                raise ValueError("simulation_context.snapshot_id must be a nonempty string.")
            if self.snapshot_id is not None and self.snapshot_id != nested:
                raise ValueError("snapshot_id conflicts with simulation_context.snapshot_id.")
            self.snapshot_id = nested
        return self


class NormalizedAlarm(BaseModel):
    alarm_id: str
    normalized_tag: str | None = None
    variable_definition: str | None = None
    severity: str
    current_value: float | str | None = None
    unit: str | None = None
    equipment: str | None = None
    stale: bool | None = None
    timestamp: datetime | None = None
    threshold: float | str | None = None
    direction: str | None = None
    measured_at: datetime | None = None
    quality: Literal["good", "uncertain", "bad", "unknown"] = "unknown"
    sample_id: str | None = None
    activated_at: datetime | None = None
    deadband_clear_value: float | None = None
    facts: list[str] = Field(default_factory=list)


class NormalizedMeasurement(Measurement):
    stale: bool | None = None


class AlarmContext(BaseModel):
    active_alarms: list[NormalizedAlarm]
    observed_variables: dict[str, float | str | None]
    event_timestamp: datetime | None = None
    snapshot_id: str | None = None
    active_objective: ActiveObjective | None = None
    measurements: dict[str, NormalizedMeasurement] = Field(default_factory=dict)
    simulation_context: dict[str, Any] = Field(default_factory=dict)
    known_disturbances: list[str] = Field(default_factory=list)
    hypothesized_disturbances: list[str] = Field(default_factory=list)
    affected_equipment: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    retrieval_priorities: list[str] = Field(default_factory=list)
    recent_changes: list[dict[str, Any]] = Field(default_factory=list)
    withheld_ground_truth_fields: list[str] = Field(default_factory=list)  # 평가 정답이라 제거한 입력 필드


class RetrievalQuery(BaseModel):
    intent: str
    query: str
    alarm_ids: list[str] = Field(default_factory=list)
    variable_ids: list[str] = Field(default_factory=list)


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None
    reranker_score: float | None = None
    intents: list[str] = Field(default_factory=list)
    alarm_ids: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    evidence_id: str
    evidence_type: EvidenceType
    statement: str
    source_document: str
    source_section: str | None = None
    source_page: int | None = None
    source_chunk_id: str
    supporting_quote: str
    related_alarm_ids: list[str] = Field(default_factory=list)
    related_variable_ids: list[str] = Field(default_factory=list)
    source_url: str | None = None
    source_version: str | None = None
    verification_status: Literal["source_supported"] = "source_supported"
    provenance: dict[str, Any] = Field(default_factory=dict)


class AlarmResult(BaseModel):
    event_id: str
    status: Literal["evidence_retrieved", "insufficient_evidence", "error"]
    alarm_context: AlarmContext
    evidence: list[EvidenceItem] = Field(default_factory=list)
    candidate_actions: list[dict[str, Any]] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    simulation_required: Literal[True] = True
    control_action_authorized: Literal[False] = False
