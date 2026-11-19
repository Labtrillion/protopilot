import json
from pathlib import Path

from zoro_rag.alarm import AlarmContextBuilder, AlarmNormalizer, RetrievalPlanner
from zoro_rag.ingestion.chunker import StructureAwareChunker
from zoro_rag.ingestion.loader import load_documents
from zoro_rag.ingestion.metadata import normalize_identifier
from zoro_rag.schemas import AlarmEvent

ROOT = Path(__file__).parents[1]


def test_identifier_normalization():
    assert normalize_identifier("IDV(8)") == "IDV_8"
    assert normalize_identifier("XMEAS_7") == "XMEAS_7"
    assert normalize_identifier("XMV (6)") == "XMV_6"


def test_documents_parse_and_chunk_deterministically():
    blocks, reports = load_documents(ROOT / "zoro_rag_knowledge")
    assert all(item["status"] in {"parsed", "partial"} for item in reports)
    assert all(item.get("warnings") for item in reports if item["status"] == "partial")
    chunks_a = StructureAwareChunker().chunk(blocks)
    chunks_b = StructureAwareChunker().chunk(blocks)
    assert chunks_a
    assert [item.chunk_id for item in chunks_a] == [item.chunk_id for item in chunks_b]
    assert any("IDV_8" in item.disturbance_ids for item in chunks_a)


def test_unknown_and_multi_alarm_planning():
    normalizer = AlarmNormalizer(ROOT / "zoro_rag_knowledge")
    event = AlarmEvent.model_validate(json.loads((ROOT / "examples/multi_alarm_event.json").read_text(encoding="utf-8")))
    alarms = normalizer.normalize(event)
    context = AlarmContextBuilder().build(event, alarms)
    queries = RetrievalPlanner().plan(context)
    assert len(alarms) == 2
    assert {item.normalized_tag for item in alarms} == {"XMEAS_7", "XMEAS_10"}
    assert any(item.intent == "PROCESS_RELATIONSHIPS" and len(item.alarm_ids) == 2 for item in queries)


def test_unknown_alarm_graceful():
    event = AlarmEvent(event_id="UNKNOWN", alarms=[{"alarm_id": "NOT_IN_CATALOG"}])
    alarm = AlarmNormalizer(ROOT / "zoro_rag_knowledge").normalize(event)[0]
    assert alarm.variable_definition is None
    assert alarm.alarm_id == "NOT_IN_CATALOG"
