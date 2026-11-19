"""HTTP transport and typed-decision calls for Zoro-1."""

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from .schemas import NoulAnswer, ScoreAnswer, ZoroRawResponse, ZoroRequest


def _request(url, payload, *, timeout, api_key=None, request_id=None):
    headers = {"Content-Type": "application/json"}
    if key := api_key or os.environ.get("ZORO_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    if request_id:
        headers["X-Request-Id"] = request_id
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(512 * 1024))


def _zoro_endpoint():
    base = os.environ.get("ZORO_BASE_URL", "").rstrip("/")
    if not base:
        raise ValueError("ZORO_BASE_URL 설정 없음")
    endpoint = f"{base}/v1" if not base.endswith("/v1") else base
    return endpoint, os.environ.get("ZORO_MODEL", "trillionlabs/Zoro-1")


def _decision_request(endpoint, model, request, reasoning, **options):
    url = f"{endpoint}/systemone"
    try:
        return _request(url, {"model": model, **request, "reasoning": reasoning}, **options), url
    except urllib.error.HTTPError as error:
        if error.code not in (404, 405):
            raise
    url = f"{endpoint}/decide"
    return _request(url, {"request": request, "reasoning": reasoning}, **options), url


def zoro_decide(state, questions, *, timeout=5, request_id=None):
    """Return answers after validating their type and decision bounds."""
    endpoint, model = _zoro_endpoint()
    options = {"timeout": timeout}
    if request_id:
        options["request_id"] = request_id
    body, _ = _decision_request(endpoint, model, {"state": state, "questions": questions}, False, **options)
    answers = body["answers"]
    for name, question in questions.items():
        answer = answers[name]
        if answer.get("type", question["type"]) != question["type"]:
            raise ValueError(f"Zoro-1 응답 타입 오류: {name}")
        if question["type"] == "noul":
            NoulAnswer.model_validate({"type": "noul", **answer})
        elif question["type"] == "score":
            score = ScoreAnswer.model_validate(answer).score
            if score > len(question["criteria"]) - 1:
                raise ValueError(f"Zoro-1 점수 범위 오류: {name}")
        elif answer["choice"] not in question["criteria"]:
            raise ValueError(f"Zoro-1 선택 범위 오류: {name}")
    return answers


def chat_completion(payload, *, timeout):
    endpoint, model = _zoro_endpoint()
    return _request(f"{endpoint}/chat/completions", {"model": model, **payload}, timeout=timeout)


class ZoroClient(Protocol):
    kind: Literal["real", "fake"]

    def decide(self, request: ZoroRequest, request_id: str) -> ZoroRawResponse: ...


class ZoroClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ZoroHttpConfig:
    base_url: str
    api_key: str | None
    timeout_s: float
    reasoning: bool | str
    model_label: str

    @classmethod
    def from_env(cls) -> "ZoroHttpConfig":
        base = os.environ.get("ZORO_BASE_URL", "").strip()
        if not base:
            raise ZoroClientError("ZORO_BASE_URL 미설정 (ZORO_MODE=http 에 필요)")
        r = os.environ.get("ZORO_REASONING", "false").strip().lower()
        reasoning: bool | str = {"true": True, "false": False}.get(r, r)
        if reasoning not in (True, False, "auto"):
            raise ZoroClientError("ZORO_REASONING 은 false|true|auto")
        return cls(
            base_url=base.rstrip("/").removesuffix("/v1"),  # ProtoPilot 서버처럼 /v1 까지 적어도 같은 주소
            api_key=os.environ.get("ZORO_API_KEY") or None,
            timeout_s=float(os.environ.get("ZORO_TIMEOUT_S", "30")),
            reasoning=reasoning,
            model_label=os.environ.get("ZORO_MODEL_LABEL") or os.environ.get("ZORO_MODEL", "trillionlabs/Zoro-1"),
        )


class HttpZoroClient:
    """trida-stack decide 서버 호출: POST /v1/systemone, 이전 서버면 /v1/decide."""

    kind: Literal["real", "fake"] = "real"

    def __init__(self, config: ZoroHttpConfig):
        self.config = config

    def decide(self, request: ZoroRequest, request_id: str) -> ZoroRawResponse:
        config = self.config
        started = time.perf_counter()
        try:
            body, endpoint = _decision_request(
                f"{config.base_url}/v1", config.model_label, request.model_dump(mode="json"),
                config.reasoning, timeout=config.timeout_s, api_key=config.api_key, request_id=request_id,
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ZoroClientError(f"Zoro 호출 실패: {error}") from error
        return ZoroRawResponse(
            request_id=request_id, client_kind="real", model=config.model_label, endpoint=endpoint,
            received_at=datetime.now(timezone.utc), latency_ms=(time.perf_counter() - started) * 1000, body=body,
        )
