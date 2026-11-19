"""JSON Schema 와 경계별 예시 JSON 을 docs/ 아래로 내보낸다.

    python scripts/export_schemas.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sop_ranking.benchmark import CaseSelection, EvaluationSpec, RunScore  # noqa: E402
from sop_ranking.inputs import EventInput  # noqa: E402
from sop_ranking.outputs import FinalOutput, LogEntry, ProgressStatus  # noqa: E402
from sop_ranking.policy import AgentPolicy  # noqa: E402
from sop_ranking.rag import NormalizedRagResult  # noqa: E402
from sop_ranking.registry import ActionCatalog  # noqa: E402
from sop_ranking.tool_io import ExternalComparison, ToolRequest, ToolResult  # noqa: E402
from sop_ranking.decision import Decision  # noqa: E402
from zoro.schemas import ZoroRawResponse, ZoroRequest  # noqa: E402

SCHEMAS = {
    "A1_event_input": EventInput,
    "A2_rag_normalized": NormalizedRagResult,
    "A3_action_catalog": ActionCatalog,
    "A4_tool_result": ToolResult,
    "A5_external_comparison": ExternalComparison,
    "B1_zoro_request": ZoroRequest,
    "B2_zoro_raw_response": ZoroRawResponse,
    "B3_decision": Decision,
    "C1_tool_request": ToolRequest,
    "C2_log_entry": LogEntry,
    "C3_progress_status": ProgressStatus,
    "C4_final_output": FinalOutput,
    "X_agent_policy": AgentPolicy,
    "D1_evaluation_spec": EvaluationSpec,
    "D2_run_score": RunScore,
    "D3_case_selection": CaseSelection,
}


def main() -> None:
    sd = ROOT / "docs" / "schemas"
    sd.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        (sd / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")

    subprocess.run([sys.executable, str(ROOT / "examples" / "run_demo.py")], check=True,
                   stdout=subprocess.DEVNULL)
    out, scen = ROOT / "examples" / "output", ROOT / "examples" / "scenario_tep_purge"
    ed = ROOT / "docs" / "examples"
    ed.mkdir(parents=True, exist_ok=True)
    picks = {
        "A1_event_input.json": scen / "event_input.json",
        "A2_rag_raw_example_v0.json": scen / "rag_raw.json",
        "A2_rag_normalized.json": out / "rag_normalized.json",
        "A3_action_catalog.json": scen / "action_catalog.json",
        "A4_tool_result_sensor_no_new_value.json": out / "tool_result_1.json",
        "A4_tool_result_sensor_new_value.json": out / "tool_result_2.json",
        "A4_tool_result_simulation.json": out / "tool_result_5.json",
        "B1_zoro_request_first.json": out / "zoro_request_1.json",
        "B1_zoro_request_after_sims.json": out / "zoro_request_6.json",
        "B2_zoro_raw_response.json": out / "zoro_raw_response_3.json",
        "B3_decision_tool_call.json": out / "decision_3.json",
        "B3_decision_recommendation.json": out / "decision_6.json",
        "C1_tool_request_simulation_hold.json": out / "tool_request_3.json",
        "C1_tool_request_simulation_setpoint.json": out / "tool_request_5.json",
        "C2_log.json": out / "log.json",
        "C3_progress.json": out / "progress.json",
        "C4_final_output.json": out / "final_output.json",
        "X_policy.json": scen / "policy.json",
        "A0_alarm_event_raw.json": scen / "alarm_event.json",
    }
    for dst, src in picks.items():
        shutil.copyfile(src, ed / dst)
    print(f"schemas -> {sd}\nexamples -> {ed}")


if __name__ == "__main__":
    main()
