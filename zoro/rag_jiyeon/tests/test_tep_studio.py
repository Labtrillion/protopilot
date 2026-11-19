"""TEP Studio 센서별 SOP·경보 v1 형식·정답 누출 차단."""
import json
import os
from pathlib import Path

import yaml

from zoro_rag import ZoroRAG
from zoro_rag.alarm import AlarmContextBuilder, AlarmNormalizer, RetrievalPlanner
from zoro_rag.ingestion.chunker import StructureAwareChunker
from zoro_rag.ingestion.loader import load_documents
from zoro_rag.pipeline import sop_plan_match
from zoro_rag.schemas import AlarmEvent

ROOT = Path(__file__).parents[1]
SOP_DIR = ROOT / "examples/tep_studio/knowledge"
EVENT = ROOT / "examples/tep_studio/alarm_event.json"


def test_sensor_sop_chunks():
    blocks, reports = load_documents(SOP_DIR)
    assert all(r["status"] == "parsed" for r in reports)
    chunks = StructureAwareChunker().chunk(blocks)
    steps = {c.procedure["step_id"]: c for c in chunks if c.procedure}
    assert sum(1 for s in steps if s.startswith("sensor_XMEAS_")) == 41
    case = steps["simulate_purge_b_cases"]
    assert case.source_type == "authored_review_protocol" and case.document_version.startswith("sha256:")
    plan = case.procedure["action"]["plan"]
    assert plan["simulator"] == "tep_studio_braatz" and plan["parameter"] == "SETPT(19)"
    assert plan["candidates"][:2] == [["SIM_SP19_UP050", 0.5], ["SIM_SP19_DN050", -0.5]]
    assert "+0.2133 kscmh" in case.procedure["instruction_ko"] and "+26.03%p" in case.procedure["instruction_ko"]
    assert max(c.token_count for c in chunks) <= 650


def test_zoro_alarm_event_v1_is_converted_without_ground_truth():
    raw = json.loads(EVENT.read_text(encoding="utf-8"))
    event = AlarmEvent.model_validate(raw)
    assert event.alarms[0].tag == "XMEAS7" and event.alarms[0].direction == "high"
    assert event.timestamp.isoformat() == "1970-01-01T01:00:00+00:00"
    alarms = AlarmNormalizer(ROOT / "zoro_rag_knowledge").normalize(event)
    context = AlarmContextBuilder().build(event, alarms)
    assert alarms[0].normalized_tag == "XMEAS_7"
    assert context.known_disturbances == [] and "IDV" not in context.model_dump_json()
    assert "disturbance_id" in context.withheld_ground_truth_fields
    queries = RetrievalPlanner(include_sop_review=True).plan(context)
    assert any(q.intent == "SOP_REVIEW" and q.variable_ids == ["XMEAS_7"] for q in queries)
    assert not any("IDV" in q.query for q in queries)


def test_reactor_pressure_alarm_returns_purge_case_plan(tmp_path):
    config = yaml.safe_load((ROOT / "config/testing.yaml").read_text(encoding="utf-8"))
    config["knowledge_base"] = str(ROOT / "zoro_rag_knowledge")
    config["additional_knowledge"] = [str(SOP_DIR)]
    if os.environ.get("ZORO_SPARSE_CACHE"):
        config["models"]["sparse_cache_dir"] = os.environ["ZORO_SPARSE_CACHE"]
    for key, name in (("path", "qdrant"), ("manifest", "manifest.json"), ("chunks", "chunks.jsonl")):
        config["index"][key] = str(tmp_path / name)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with ZoroRAG(path) as rag:
        rag.ingest()
        result = rag.process_alarm_event(json.loads(EVENT.read_text(encoding="utf-8")))
    plans = [a for a in result.candidate_actions if a["step_id"] == "simulate_purge_b_cases"]
    assert len(plans) == 1
    action = plans[0]
    assert action["matched_alarm_tags"] == ["XMEAS_7"]
    # 스냅샷·현재 목표가 원본 v1 형식에 없으므로 입력 검토가 필요하다
    assert action["status"] == "input_review_required"
    assert any("snapshot_id" in r for r in action["input_review_reasons"])
    assert "IDV(6)" not in result.alarm_context.model_dump_json()
    assert all(e.source_version for e in result.evidence)
    assert not result.control_action_authorized
    out = os.environ.get("ZORO_RESULT_OUT")
    if out:
        Path(out).write_text(result.model_dump_json(indent=2), encoding="utf-8")


def test_sop_plan_matches_only_trigger_tags_and_simulator():
    blocks, _ = load_documents(SOP_DIR)
    case = next(c for c in StructureAwareChunker().chunk(blocks) if c.procedure.get("step_id") == "simulate_purge_b_cases")
    assert sop_plan_match(case, {"XMEAS_9"}, "tep_studio_braatz") is None      # 반응기 온도 경보는 퍼지 case 아님
    _, matched, status = sop_plan_match(case, {"XMEAS_7", "XMEAS_9"}, "tep_studio_braatz")
    assert matched == ["XMEAS_7"] and status == "awaiting_simulation"
    assert sop_plan_match(case, {"XMEAS_13"}, "apmonitor_teaching_tep")[2] == "simulator_mismatch"
    sensor = next(c for c in StructureAwareChunker().chunk(blocks) if c.procedure.get("step_id") == "sensor_XMEAS_7")
    assert sop_plan_match(sensor, {"XMEAS_7"}, None) is None                    # 센서 단계는 계획이 아님
