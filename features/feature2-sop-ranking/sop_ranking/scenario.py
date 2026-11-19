"""예시 시나리오 로더. evaluation_only.json(숨은 정답)은 읽지 않는다."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .inputs import EventInput
from .policy import AgentPolicy
from .rag import ExampleRagV0Adapter, JiyeonRagAdapter, NormalizedRagResult
from .registry import ActionCatalog


@dataclass
class Scenario:
    event: EventInput
    rag_raw: dict[str, Any]
    rag: NormalizedRagResult
    catalog: ActionCatalog
    policy: AgentPolicy
    fake_tools: dict[str, Any]
    fake_model_preference: list[str]


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def load_scenario(directory: str | Path, rag: str = "example") -> Scenario:
    """rag='example': rag_raw.json(임시 형식) / rag='jiyeon': rag_jiyeon_result.json(지연님 RAG 실제 출력)."""
    d = Path(directory)
    catalog = ActionCatalog.model_validate(_load(d / "action_catalog.json"))
    rag_raw = _load(d / ("rag_jiyeon_result.json" if rag == "jiyeon" else "rag_raw.json"))
    adapter = JiyeonRagAdapter() if rag == "jiyeon" else ExampleRagV0Adapter()
    return Scenario(
        event=EventInput.model_validate(_load(d / "event_input.json")),
        rag_raw=rag_raw,
        rag=adapter.normalize(rag_raw, catalog),
        catalog=catalog,
        policy=AgentPolicy.model_validate(_load(d / "policy.json")),
        fake_tools=_load(d / "fake_tools.json"),
        fake_model_preference=_load(d / "fake_model.json")["preference"],
    )
