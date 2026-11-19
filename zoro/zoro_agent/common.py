"""공통 규약.

시간 기준
- 모든 `*_s` 필드는 시뮬레이션 시각(초)이다. `sim_time_s`·`measured_at_s`·`raised_at_s` 는
  시뮬레이션 시작 기준 절대 시각, 실험 결과 안의 `*_at_s`/`recovery_time_s` 는 해당 run 시작
  (= snapshot 시점) 기준 상대 시각이다.
- 벽시계 시각(UTC ISO-8601)은 로그의 `logged_at` 에만 쓴다.

null 의 의미
- 값이 "없다/모른다" 는 뜻이다. 0, false, pass 로 대체하지 않는다.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

TIME_BASIS = "simulation_seconds"


class Model(BaseModel):
    """모든 스키마의 기반. 정의되지 않은 필드는 거부한다(숨은 정답·오타 유입 방지)."""

    model_config = ConfigDict(extra="forbid")


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # 판단할 정보가 없음. 절대 pass 로 취급하지 않는다.


class DataQuality(StrEnum):
    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"


# 모델 입력에 절대 들어가면 안 되는 키 (고장 정답, 숨은 원인, 향후 고장 주입 일정)
FORBIDDEN_MODEL_INPUT_KEYS = frozenset(
    {
        "fault_id",
        "fault_ids",
        "hidden_cause",
        "true_cause",
        "root_cause_truth",
        "fault_schedule",
        "injected_faults",
        "ground_truth",
        "answer_key",
    }
)


def find_forbidden_keys(obj: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_MODEL_INPUT_KEYS:
                hits.append(f"{path}.{k}")
            hits.extend(find_forbidden_keys(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_forbidden_keys(v, f"{path}[{i}]"))
    return hits
