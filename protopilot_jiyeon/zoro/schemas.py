"""Wire schemas for the Zoro-1 typed-decision API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChoiceQuestion(WireModel):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str] = Field(min_length=1)


class NoulQuestion(WireModel):
    type: Literal["noul"] = "noul"
    instructions: str


class ScoreQuestion(WireModel):
    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str] = Field(min_length=2)


class ZoroRequest(WireModel):
    """Zoro 에 실제로 전송되는 본문 (공식 형식 그대로)."""

    state: dict[str, Any]
    questions: dict[str, ChoiceQuestion | NoulQuestion | ScoreQuestion]


class ZoroRawResponse(WireModel):
    request_id: str
    client_kind: Literal["real", "fake"]
    model: str | None = None
    endpoint: str | None = None
    received_at: datetime
    latency_ms: float | None = None
    body: Any = Field(description="모델/서버가 돌려준 원본 JSON 그대로 (수정·보강 금지)")


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")  # 공식 응답에 필드가 추가돼도 깨지지 않게
    type: Literal["choice"]
    choice: str
    confidence: float = Field(ge=0, le=1)
    probabilities: dict[str, float]


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1)


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float = Field(ge=0, le=2, allow_inf_nan=False)
