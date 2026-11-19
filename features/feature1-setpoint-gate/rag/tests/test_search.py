"""Regression checks for malformed PDF summaries and the user search entry point."""
from pathlib import Path
import json

import pytest

from zoro_rag import ZoroRAG
from zoro_rag.cli import main
from zoro_rag.evidence import extract_statement
from zoro_rag.ingestion.parser import clean_pdf_page, parse_pdf
from zoro_rag.ingestion.chunker import StructureAwareChunker
from zoro_rag.schemas import DocumentBlock

ROOT = Path(__file__).parents[1]


def test_print_furniture_removed_without_losing_process_limits():
    raw = (
        "26. 11. 19. 오후 3:49 Tennessee Eastman Challenge Process: Instructions\n\n"
        "The pressure limit is 3000 kPa g.\nTemperature limit: 175 °C.\n\n"
        "https://apmonitor.com/apps/tennessee-eastman/instructions.php 19/37\n"
    )
    cleaned = clean_pdf_page(raw)
    assert cleaned == "The pressure limit is 3000 kPa g.\nTemperature limit: 175 °C."
    assert clean_pdf_page("Pressure\n\n3000\n\nkPa g\nBody text\nEnd text") == "Pressure\n\n3000\n\nkPa g\nBody text\nEnd text"


def test_real_pdf_pages_keep_sources_and_drop_browser_dates():
    path = ROOT / "zoro_rag_knowledge/02_Operational_Guidance/APMonitor_Instructions.pdf"
    blocks = parse_pdf(path, path.name)
    assert not any("26. 11. 19. 오후" in block.text for block in blocks)
    assert any("3000" in block.text and "shutdown" in block.text.lower() for block in blocks)
    assert all(block.page and block.metadata["parsing_warnings"] for block in blocks)
    assert any(block.metadata.get("source_url", "") == "https://apmonitor.com/apps/tennessee-eastman/instructions.php" for block in blocks)


def test_statement_uses_data_row_instead_of_header():
    text = (
        "Model > Variables\n\nSymbol | Meaning | Units | Where it appears\n"
        "P_R | Reactor pressure (XMEAS 7) | kPa g | XMEAS 7. The interlock trips above 3000 kPa g.\n"
        "J | Operating cost | $/h | Published cost function."
    )
    statement = extract_statement(text, "Model > Variables")
    assert statement.startswith("P_R | Reactor pressure")
    assert "3000" in statement
    assert extract_statement("Mean cost is 176.1 $/h.\n\nResults") == "Mean cost is 176.1 $/h."


def test_statement_selects_the_requested_measurement_in_a_large_table():
    text = (
        "Variable | Description | unit\n"
        "XMEAS(1) | A Feed (stream 1) | kscmh\n"
        "XMEAS(7) | Reactor Pressure | kPa gauge\n"
        "XMEAS(10) | Purge Rate (stream 9) | kscmh"
    )
    assert extract_statement(text, focus="XMEAS_7 Reactor Pressure") == "XMEAS(7) | Reactor Pressure | kPa gauge"
    assert extract_statement(text, focus="What is the purge rate?") == "XMEAS(10) | Purge Rate (stream 9) | kscmh"


def test_long_prose_chunks_reserve_space_for_section_headings():
    block = DocumentBlock(
        block_type="paragraph", text="Reactor pressure remains 2705.0 kPa. " * 50,
        source_file="process.md", heading_path=["Process", "Reactor pressure"],
    )
    chunks = StructureAwareChunker(target_tokens=35, max_tokens=45, overlap_tokens=5).chunk([block])
    assert len(chunks) > 1
    assert all(chunk.token_count <= 45 for chunk in chunks)
    assert all("2705.0" in chunk.text for chunk in chunks)


def test_missing_index_has_actionable_error_without_loading_models(tmp_path):
    import yaml
    config = yaml.safe_load((ROOT / "config/testing.yaml").read_text())
    config["knowledge_base"] = str(ROOT / "zoro_rag_knowledge")
    config["index"]["manifest"] = str(tmp_path / "missing.json")
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(yaml.safe_dump(config))
    with ZoroRAG(config_path) as rag:
        with pytest.raises(RuntimeError, match="ingest"):
            rag.query("reactor pressure")
        with pytest.raises(ValueError, match="질문"):
            rag.query("   ")
        assert "dense" not in rag.__dict__ and "reranker" not in rag.__dict__


def test_explicit_interactive_command_accepts_repeated_questions(monkeypatch, capsys):
    import zoro_rag.cli as cli
    questions = iter(["", "reactor pressure", "purge rate", "/quit"])
    received = []

    class SearchSession:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            received.append("closed")

        def check_index(self):
            pass

        def query(self, question):
            received.append(question)
            return []

    monkeypatch.setattr(cli, "ZoroRAG", SearchSession)
    monkeypatch.setattr("builtins.input", lambda prompt: next(questions))
    assert main(["interactive"]) == 0
    assert received == ["reactor pressure", "purge rate", "closed"]
    assert "검색 결과가 없습니다" in capsys.readouterr().out


@pytest.mark.parametrize("custom_input", [False, True])
def test_default_entry_point_processes_alarm_json_without_prompt(monkeypatch, tmp_path, capsys, custom_input):
    import zoro_rag.cli as cli
    (tmp_path / "examples/idv8").mkdir(parents=True)
    path = tmp_path / ("notification.json" if custom_input else "examples/idv8/alarm_event.json")
    event = {"event_id": "FROM-NOTIFICATION", "alarms": [{"alarm_id": "PRESSURE_HIGH", "tag": "XMEAS(7)"}]}
    path.write_text(json.dumps(event))
    received = []

    class AlarmSession:
        def __init__(self, config):
            assert Path(config) == tmp_path / "config/demo_idv8.yaml"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            received.append("closed")

        def process_alarm_event(self, value):
            assert received == ["ingested"]
            received.append(value)
            return {"event_id": value["event_id"], "candidate_actions": []}

        def ingest(self):
            received.append("ingested")
            return {"chunk_count": 1, "indexed": 1, "unchanged": 0}

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "ZoroRAG", AlarmSession)
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("알람 실행에서 질문을 요청하면 안 됩니다."))
    args = ["--input", str(path)] if custom_input else []
    assert main(args) == 0
    result = json.loads((tmp_path / "artifacts/idv8/result.json").read_text())
    assert result["event_id"] == "FROM-NOTIFICATION"
    assert received == ["ingested", event, "closed"]
    if not custom_input:
        assert json.loads((tmp_path / "artifacts/idv8/ingest.json").read_text())["indexed"] == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == result
    assert ("데모를 처리" in output.err) is (not custom_input)
