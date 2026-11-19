"""정책·한도 설정. 수치는 모두 예시이며 실제 공정에서 검증된 기준이 아니다."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .benchmark import BenchmarkPolicy
from .common import Model


class FreshnessPolicy(Model):
    default_max_age_s: float = Field(gt=0)
    per_sensor_max_age_s: dict[str, float] = {}

    def max_age(self, sensor_id: str) -> float:
        return self.per_sensor_max_age_s.get(sensor_id, self.default_max_age_s)


class RecommendationPolicy(Model):
    """최종 권고 옵션을 여는 기준과 필수 비교 대상."""

    baseline_action_id: str = Field(description="필수 기준(무조치) 실험 행동")
    min_candidate_runs: int = Field(ge=1, description="기준 외 성공·비교 가능한 후보 실험 최소 수")
    required_safety_checks: list[str] = Field(default=[], description="pass 여야 권고 가능")
    advisory_safety_checks: list[str] = Field(default=[], description="권고를 막지 않고 불확실성으로 보고")
    required_fresh_sensors: list[str] = []
    require_alarm_recovery: bool = True
    block_if_limit_exceedance: bool = True
    require_rag_evidence: bool = True


class Budget(Model):
    max_decisions: int = Field(ge=1, description="Zoro 호출 최대 횟수")
    max_tool_calls: int = Field(ge=0)
    max_simulations: int = Field(ge=0)
    max_wall_time_s: float | None = Field(default=None, description="실행 벽시계 한도. null = 제한 없음")


class AgentPolicy(Model):
    schema_version: Literal["agent_policy.v0"] = "agent_policy.v0"
    label: str = Field(description="예: 'EXAMPLE — 검증된 기준 아님'")
    freshness: FreshnessPolicy
    recommendation: RecommendationPolicy
    budget: Budget
    sop_first: bool = False
    setpoint_variant_steps_pct: list[float] = Field(default_factory=lambda: [-0.5, 0.5, -1.0, 1.0])
    benchmark: BenchmarkPolicy | None = Field(
        default=None, description="있으면 심슨(hold) 대비 비용·정확도 비교와 case 탐색 종료 규칙으로 권고를 연다")
