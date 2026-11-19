import json
from pathlib import Path

import pytest
import yaml

from zoro_rag import ZoroRAG
from zoro_rag.alarm import AlarmContextBuilder, AlarmNormalizer
from zoro_rag.schemas import AlarmEvent, AlarmResult, DocumentBlock
from zoro_rag.ingestion.chunker import StructureAwareChunker
from zoro_rag.ingestion.parser import parse_json, parse_html

ROOT = Path(__file__).parents[1]


def test_chunk_provenance_stays_with_page_and_section():
    blocks = [DocumentBlock(block_type="paragraph", text="Reactor pressure facts.", source_file="a.pdf", page=page) for page in (1, 2)]
    chunks = StructureAwareChunker().chunk(blocks)
    assert [c.page for c in chunks] == [1, 2]
    assert len({c.chunk_id for c in chunks}) == 2
    blocks = [DocumentBlock(block_type="paragraph", text="Short facts.", source_file="a.md", heading_path=[h]) for h in ("A", "B")]
    assert len(StructureAwareChunker().chunk(blocks)) == 2


def test_catalog_and_experiment_context():
    path = ROOT / "zoro_rag_knowledge/05_Disturbance_knowledge/idv_catalog.json"
    chunks = StructureAwareChunker().chunk(parse_json(path, str(path.name)))
    chunk = next(c for c in chunks if c.section == "IDV_8")
    assert "IDV_8" in chunk.disturbance_ids
    path = ROOT / "zoro_rag_knowledge/02_Operational_Guidance/APMonitor_Instructions.html"
    chunks = StructureAwareChunker().chunk(parse_html(path, path.name))
    chunk = next(c for c in chunks if "Over 24 h the mean cost is 176.1" in c.text)
    assert all(text in chunk.text for text in ("IDV 8", "24 h", "2800", "146.3", "2827"))
    assert all("Clara Sterling" not in c.text for c in chunks)


def test_event_preserves_conflict_staleness_and_limits():
    event = AlarmEvent(event_id="STALE", timestamp="2026-11-19T10:00:00Z", alarms=[{
        "alarm_id": "PRESSURE_HIGH", "tag": "XMEAS(7)", "current_value": 2810,
        "threshold": 2800, "direction": "high", "timestamp": "2026-11-19T09:00:00Z",
    }], process_state={"XMEAS(7)": 2790})
    alarms = AlarmNormalizer(ROOT / "zoro_rag_knowledge").normalize(event)
    context = AlarmContextBuilder().build(event, alarms)
    assert alarms[0].threshold == 2800 and alarms[0].stale
    assert any("Conflicting" in message for message in context.missing_information)
    assert context.observed_variables["XMEAS_7"] == 2790
    with pytest.raises(ValueError):
        AlarmResult(event_id="X", status="insufficient_evidence", alarm_context=context, control_action_authorized=True)


def test_persistent_index_repeated_ingestion_and_empty_results(tmp_path):
    config = yaml.safe_load((ROOT / "config/testing.yaml").read_text())
    config["knowledge_base"] = str(ROOT / "zoro_rag_knowledge")
    config["models"]["sparse_cache_dir"] = "/home/yongbin53/hf_cache/fastembed"
    for key, name in (("path", "qdrant"), ("manifest", "manifest.json"), ("chunks", "chunks.jsonl")):
        config["index"][key] = str(tmp_path / name)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(config))
    with ZoroRAG(path) as rag:
        assert "dense" not in rag.__dict__ and "reranker" not in rag.__dict__
        result = rag.process_alarm_event({"event_id": "EMPTY", "alarms": [{"alarm_id": "REACTOR_PRESSURE_HIGH", "tag": "XMEAS(7)"}]})
        assert result.status == "insufficient_evidence" and not result.evidence
        first = rag.ingest()
        second = rag.ingest()
        assert first["indexed"] > 0 and second["indexed"] == 0
        assert rag.store.count() == first["chunk_count"]
        rag.store.ensure_collection(recreate=True)
        assert rag.ingest()["indexed"] == first["chunk_count"]
        result = rag.process_alarm_event({"event_id": "UNKNOWN", "alarms": [{"alarm_id": "NOT_IN_CATALOG"}]})
        assert result.status == "insufficient_evidence"
        event = json.loads((ROOT / "examples/multi_alarm_event.json").read_text())
        result = rag.process_alarm_event(event)
        assert result.evidence and not result.control_action_authorized
    with ZoroRAG(path) as rag:
        assert rag.stats()["points"] == first["chunk_count"]
