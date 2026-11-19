import json
import os
from pathlib import Path

import yaml

from zoro_rag import ZoroRAG
from zoro_rag.ingestion.chunker import StructureAwareChunker
from zoro_rag.ingestion.parser import parse_json
from zoro_rag.schemas import AlarmEvent

ROOT = Path(__file__).parents[1]
SOP = ROOT / "examples/idv8/knowledge/sop.json"
EVENT = ROOT / "examples/idv8/alarm_event.json"


def test_protocol_steps_keep_action_and_transition_provenance():
    data = json.loads(SOP.read_text(encoding="utf-8"))
    steps = data["protocol"]["steps"]
    destinations = {step["id"] for step in steps} | {item["id"] for item in data["protocol"]["terminals"]}
    sources = data["provenance"]["source_registry"]
    for step in steps:
        assert all(t["to"] in destinations for t in step["transitions"])
        assert all(ref in sources for ref in step["source_refs"])
    chunks = StructureAwareChunker().chunk(parse_json(SOP, "sop.json"))
    action_chunks = [c for c in chunks if c.procedure.get("action")]
    assert len(action_chunks) == 1
    chunk = action_chunks[0]
    assert chunk.source_type == "synthetic_demo_sop" and "IDV_8" in chunk.disturbance_ids
    assert chunk.procedure["step_id"] == "simulate_pressure_candidates"
    assert chunk.procedure["action"]["plan"]["results"] is None
    assert max(c.token_count for c in chunks) <= 650
    assert AlarmEvent.model_validate_json(EVENT.read_text(encoding="utf-8")).source == "synthetic_demo_fixture"


def test_idv8_sop_is_retrieved_and_returns_an_unexecuted_review_plan(tmp_path):
    config = yaml.safe_load((ROOT / "config/testing.yaml").read_text(encoding="utf-8"))
    config["knowledge_base"] = str(ROOT / "zoro_rag_knowledge")
    config["additional_knowledge"] = [str(SOP.parent)]
    config["simulator"] = "apmonitor_teaching_tep"  # 이 데모 SOP 는 APMonitor 구현 전용
    if os.environ.get("ZORO_SPARSE_CACHE"):
        config["models"]["sparse_cache_dir"] = os.environ["ZORO_SPARSE_CACHE"]
    for key, name in (("path", "qdrant"), ("manifest", "manifest.json"), ("chunks", "chunks.jsonl")):
        config["index"][key] = str(tmp_path / name)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    event = json.loads(EVENT.read_text(encoding="utf-8"))
    with ZoroRAG(path) as rag:
        rag.ingest()
        result = rag.process_alarm_event(event)
        assert len(result.candidate_actions) == 1
        action = result.candidate_actions[0]
        assert action["status"] == "awaiting_simulation"
        assert action["snapshot_id"] == event["snapshot_id"]
        assert action["active_objective"] == event["active_objective"]
        assert result.alarm_context.measurements["XMEAS_23"].sample_id == "DEMO-FEED-SAMPLE-0007"
        assert action["recommended_setpoint"] is None
        assert action["plan"]["candidate_values"] == [2705, 2750, 2800, 2850]
        assert action["plan"]["constraints"]["maximum_peak_pressure_kpag"] == 2900
        assert action["evidence_id"] in {e.evidence_id for e in result.evidence}
        assert any(e.evidence_type.value == "SAFETY" and e.provenance["source_type"] != "synthetic_demo_sop" for e in result.evidence)
        assert any("not been executed" in message for message in result.missing_evidence)
        assert not result.control_action_authorized
        event["process_state"]["XMEAS(7)"]["quality"] = "bad"
        review = rag.process_alarm_event(event).candidate_actions[0]
        assert review["status"] == "input_review_required"
        assert any("quality" in reason for reason in review["input_review_reasons"])
        event["process_state"]["XMEAS(7)"]["quality"] = "good"
        event["active_objective"]["direction"] = "maximize"
        review = rag.process_alarm_event(event).candidate_actions[0]
        assert review["status"] == "input_review_required"
        assert any("active objective" in reason for reason in review["input_review_reasons"])
        event["active_objective"]["direction"] = "minimize"
        # 후보는 경보 태그로 정해진다. 입력 출처나 주입 외란 번호로 바뀌지 않는다
        event["source"] = "actual_alarm_feed"
        assert len(rag.process_alarm_event(event).candidate_actions) == 1
        event["simulation_context"]["disturbance_id"] = "IDV(6)"
        other = rag.process_alarm_event(event)
        assert len(other.candidate_actions) == 1
        assert "IDV(6)" not in other.alarm_context.model_dump_json()
    # 다른 시뮬레이터로 설정하면 이 계획은 실행 대상이 아니다
    config["simulator"] = "tep_studio_braatz"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with ZoroRAG(path) as rag:
        assert rag.process_alarm_event(json.loads(EVENT.read_text(encoding="utf-8"))).candidate_actions[0]["status"] == "simulator_mismatch"
