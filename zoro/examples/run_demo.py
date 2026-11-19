"""End-to-end 데모: 가상 시나리오 + 가짜 도구 + (기본) 가짜 Zoro.

    python examples/run_demo.py                 # 가짜 Zoro, 임시 RAG 형식
    python examples/run_demo.py --rag jiyeon    # 지연님 RAG 실제 출력(rag_jiyeon_result.json)
    ZORO_MODE=http ZORO_BASE_URL=... python examples/run_demo.py   # 실제 Zoro 서버 (도구는 여전히 가짜)

결과 JSON 은 examples/output/ 에 저장된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zoro_agent.agent import ZoroAgent  # noqa: E402
from zoro_agent.options import OptionStatus  # noqa: E402
from zoro_agent.scenario import load_scenario  # noqa: E402
from zoro_agent.tools import FakeToolkit, ToolExecutor  # noqa: E402
from zoro_agent.zoro import client_from_env  # noqa: E402

SCENARIO = ROOT / "examples" / "scenario_tep_purge"
OUT = ROOT / "examples" / "output"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rag = sys.argv[sys.argv.index("--rag") + 1] if "--rag" in sys.argv else "example"
    sc = load_scenario(SCENARIO, rag=rag)
    client = client_from_env(sc.fake_model_preference)
    executor = ToolExecutor(FakeToolkit(sc.fake_tools).tools(), virtual=True)
    agent = ZoroAgent(sc.catalog, sc.policy, client, executor)
    report = agent.run(sc.event, sc.rag)

    print("=" * 78)
    print(f" ProtoPilot Zoro 데모 — 가상 시나리오 | 모델: {client.kind.upper()} | 도구: VIRTUAL")
    print("=" * 78)
    for i, ex in enumerate(report.exchanges, 1):
        dec = report.decisions[i - 1]
        print(f"\n[판단 {i}] state_version={ex.state_version}  sim_time={ex.request.state['event']['sim_time_s']}s")
        for o in ex.options.options:
            mark = {"open": "O", "deferred": "~", "excluded": "x"}[str(o.status)]
            line = f"   {mark} {o.option_id:<42}"
            if o.status != OptionStatus.OPEN:
                line += " " + o.reason[:110]
            print(line)
        dist = dec.selection_distribution.probabilities if dec.selection_distribution else {}
        print(f"   → Zoro 선택: {dec.model_choice_raw}  (p={dist.get(dec.model_choice_raw or '', '-')}, "
              f"검증={'OK' if dec.validation.valid else 'REJECT ' + str(dec.validation.errors)})")
        rec = next((h for h in report.final_state.tool_history if h.decision_id == dec.decision_id), None)
        if rec:
            print(f"   → 도구 {rec.tool_name}: {rec.status}, 정보 갱신={rec.info_updated}, "
                  f"v{rec.state_version_before}→v{rec.state_version_after}")
            for n in rec.notes:
                print(f"       · {n}")
    f = report.final
    print("\n" + "-" * 78)
    print(f" 결과: {f.outcome}  ({f.termination_reason})")
    print(f" {f.summary}")
    c = f.setpoint_command
    print(f" 시뮬레이터/감독기 명령: {c.decision}  case={c.case_id}  (값={c.values_status}, 감독기 승인 필요)")
    for ch in c.setpoint_changes:
        print(f"   - {ch.setpoint_id} [{ch.loop_id}, Scheme index {ch.scheme_index}] "
              f"{ch.from_value} → {ch.target_value} {ch.unit} (허용 {ch.allowed_min}~{ch.allowed_max})")
        for k in ch.controller_response:
            vb = f" ({k.value_before}% → {k.value_after}%)" if k.value_before is not None else ""
            print(f"       ⇒ {k.loop}({k.control}, {k.period_s}s): {k.target} {k.immediate_delta:+.4g} 즉시{vb}")
    print(f"   근거 SOP: {', '.join(f'{r.sop_id}@{r.version}/{r.step_id}' for r in c.sop_refs)}")
    print(f"   예상 비용 방향(사전 가설): {c.expected_cost_direction}")
    print(f"   기준 대비 가상 예측: " + ", ".join(f"{e.metric} {e.delta:+g} {e.unit}" for e in c.simulated_effects))
    print(f"   확인 신호: {c.watch_signals} / 재검토 시각 {c.review_at_sim_time_s}s")
    print(f"   원복 조건: " + "; ".join(r.description for r in c.revert_rules))
    print(" 근거 (에이전트 구성, 모델 출력 아님):")
    for r in f.rationale:
        print(f"   - [{r.basis}] {r.statement}")
    print(" 남은 불확실성:")
    for u in f.uncertainties:
        print(f"   - {u}")
    print(f"\n ※ {f.disclaimer}")

    OUT.mkdir(parents=True, exist_ok=True)

    def dump(name: str, obj) -> None:
        data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
        (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    dump("run_report.json", report)
    dump("final_output.json", report.final)
    dump("rag_normalized.json", sc.rag)
    for i, ex in enumerate(report.exchanges, 1):
        dump(f"zoro_request_{i}.json", ex.request)
        if ex.raw_response:
            dump(f"zoro_raw_response_{i}.json", ex.raw_response)
        dump(f"decision_{i}.json", report.decisions[i - 1])
    for i, tr in enumerate(report.tool_requests, 1):
        dump(f"tool_request_{i}.json", tr)
        dump(f"tool_result_{i}.json", report.tool_results[i - 1])
    dump("log.json", [e.model_dump(mode="json") for e in report.log])
    dump("progress.json", [p.model_dump(mode="json") for p in report.progress])
    print(f"\n 출력 저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
