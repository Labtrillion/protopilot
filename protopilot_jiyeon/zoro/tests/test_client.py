import json

import pytest

from zoro import client as zmod
from zoro.client import ZoroClientError, ZoroHttpConfig
from zoro.schemas import ChoiceQuestion, ZoroRequest


def test_http_client_config_requires_env(monkeypatch):
    monkeypatch.delenv("ZORO_BASE_URL", raising=False)
    with pytest.raises(ZoroClientError):
        ZoroHttpConfig.from_env()
    monkeypatch.setenv("ZORO_BASE_URL", "http://localhost:30001/")
    monkeypatch.setenv("ZORO_REASONING", "auto")
    cfg = ZoroHttpConfig.from_env()
    assert cfg.base_url == "http://localhost:30001" and cfg.reasoning == "auto"
    monkeypatch.setenv("ZORO_BASE_URL", "http://localhost:30001/v1/")
    assert ZoroHttpConfig.from_env().base_url == "http://localhost:30001"


# ------------------------------------------------------------------ 시뮬레이터 case / 설정값 명령


def test_http_client_uses_systemone_then_falls_back(monkeypatch):
    import io
    import urllib.error
    req = ZoroRequest(state={"sample": True}, questions={
        "next_action": ChoiceQuestion(instructions="Choose an option", criteria={"X": "Hold"}),
    })
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(r, timeout):
        seen.append((r.full_url, json.loads(r.data)))
        if r.full_url.endswith("/v1/systemone") and len(seen) == 1:
            raise urllib.error.HTTPError(r.full_url, 404, "nf", {}, None)
        return Resp(json.dumps({"answers": {"next_action": {"type": "choice", "choice": "X", "confidence": 1,
                                                          "probabilities": {"X": 1}}}}).encode())

    monkeypatch.setattr(zmod.urllib.request, "urlopen", fake_urlopen)
    client = zmod.HttpZoroClient(zmod.ZoroHttpConfig(base_url="http://h", api_key=None, timeout_s=1,
                                                     reasoning=False, model_label="zoro-1"))
    raw = client.decide(req, "R1")
    assert [u for u, _ in seen] == ["http://h/v1/systemone", "http://h/v1/decide"]
    assert seen[0][1]["model"] == "zoro-1" and "questions" in seen[0][1] and "request" in seen[1][1]
    assert raw.endpoint == "http://h/v1/decide" and raw.client_kind == "real"
