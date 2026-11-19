"""경계 A-1: 사건·현재 관측값 (장하/영하 → 에이전트)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .cases import ActiveSetpoint
from .common import CheckStatus, DataQuality, Model


class Observation(Model):
    """실제 공정 측정값. 시뮬레이션 예측값은 절대 이 타입으로 만들지 않는다 (origin 고정)."""

    origin: Literal["plant_measurement"] = "plant_measurement"
    sensor_id: str
    value: float | None = Field(description="측정값. null = 값 없음(결측/측정 실패). 0으로 대체 금지.")
    unit: str = Field(description="단위 문자열 (예: kPa(g), degC, mol_frac, t/h)")
    measured_at_s: float | None = Field(
        description="측정(시료 채취) 시뮬레이션 시각 [s]. null = 시각 모름 → 신선도 unknown"
    )
    quality: DataQuality
    sample_id: str | None = Field(
        default=None, description="시료/측정 식별자. 같은 시료 재전달 판별용. null = 미제공"
    )


class Alarm(Model):
    alarm_id: str
    sensor_id: str
    condition: Literal["high", "high_high", "low", "low_low", "deviation", "rate", "other"]
    raised_at_s: float
    active: bool
    priority: Literal["low", "medium", "high", "critical"] | None = None
    message: str | None = None
    threshold: float | None = Field(default=None, description="경보 임계값. null = 미제공")
    unit: str | None = None
    raw_tag: str | None = Field(default=None, description="원본 계측 태그 (예: PIR100)")


class SafetyCheck(Model):
    """외부 규칙 엔진이 제공한 안전 검사 결과. 에이전트는 계산하지 않고 그대로 읽는다."""

    check_id: str
    description: str
    status: CheckStatus
    source: str
    evaluated_at_s: float | None = Field(default=None, description="null = 평가 시각 미제공/미평가")


class MissingInfo(Model):
    item: str
    reason: str
    sensor_id: str | None = Field(default=None, description="관련 센서. 새 측정이 반영되면 해소 처리")


class EventInput(Model):
    schema_version: Literal["event_input.v0"] = "event_input.v0"
    event_id: str
    state_version: int = Field(ge=0, description="백엔드가 부여한 상태 버전. 에이전트는 갱신마다 +1")
    sim_time_s: float = Field(description="현재 시뮬레이션 시각 [s]")
    time_basis: Literal["simulation_seconds"] = "simulation_seconds"
    snapshot_id: str = Field(description="대응 실험의 공통 비교 출발점")
    alarms: list[Alarm]
    observations: list[Observation] = Field(description="최근 관측 이력(센서별 여러 점 가능)")
    safety_checks: list[SafetyCheck] = []
    missing_info: list[MissingInfo] = []
    active_setpoints: list[ActiveSetpoint] = Field(
        default=[], description="현재 적용 중인 상위 목표. 비어 있으면 설정값 변경 case 는 보류")
