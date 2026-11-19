"""TEP Studio(Braatz) 센서별 대응 SOP 생성.

    python scripts/build_tep_studio_sop.py

제어 구조·증분 계산은 server/tep_control.py(= tep-rust tepsim-control 이식)에서 가져온다.
출력
- rag/examples/tep_studio/knowledge/tep_studio_braatz_sop.json  (지연님 RAG protocol 형식)
- docs/SOP_TEP_STUDIO.md                                             (사람이 읽는 표)

원칙
- 코드에서 나온 사실(어느 루프가 어느 계측을 읽고 어디에 쓰는지, P/PI, 주기, 오버라이드, 증분식)과
  공정 가설(퍼지를 늘리면 반응기 압력이 내려간다 등)을 구분해 적는다. 가설은 시뮬레이션으로 확인한다.
- 벤치마크 공유 목표(생산·품질·반응기 온도·세 액위)는 바꾸지 않는다(rag/legacy/STUDIO.md).
- 후보 변경폭·순서·허용 범위는 예시 설정이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))

from server.tep_control import (  # noqa: E402
    OVERRIDE_HIGH, OVERRIDE_LOW, OVERRIDE_RELEASE, OVERRIDE_RESET_SETPOINT, OVERRIDE_RESET_VALVE, SOURCE,
    cascade_response, chain_for_measurement, loop_reading, owner_of, preset, top_level_setpoints, writer_of)

OUT_JSON = ROOT / "rag" / "examples" / "tep_studio" / "knowledge" / "tep_studio_braatz_sop.json"
OUT_MD = ROOT / "docs" / "SOP_TEP_STUDIO.md"
DOC_ID = "tep-studio-braatz-sensor-response"
VERSION = "1.0"

# 지연님 RAG zoro_rag_knowledge/03_Control_and_Variables/README.md 변수표
XMEAS = {
    1: ("A Feed (stream 1)", "kscmh"), 2: ("D Feed (stream 2)", "kg/hr"), 3: ("E Feed (stream 3)", "kg/hr"),
    4: ("A and C Feed (stream 4)", "kscmh"), 5: ("Recycle Flow (stream 8)", "kscmh"),
    6: ("Reactor Feed Rate (stream 6)", "kscmh"), 7: ("Reactor Pressure", "kPa gauge"), 8: ("Reactor Level", "%"),
    9: ("Reactor Temperature", "Deg C"), 10: ("Purge Rate (stream 9)", "kscmh"), 11: ("Product Sep Temp", "Deg C"),
    12: ("Product Sep Level", "%"), 13: ("Prod Sep Pressure", "kPa gauge"),
    14: ("Prod Sep Underflow (stream 10)", "m3/hr"), 15: ("Stripper Level", "%"), 16: ("Stripper Pressure", "kPa gauge"),
    17: ("Stripper Underflow (stream 11)", "m3/hr"), 18: ("Stripper Temperature", "Deg C"),
    19: ("Stripper Steam Flow", "kg/hr"), 20: ("Compressor Work", "kW"),
    21: ("Reactor Cooling Water Outlet Temp", "Deg C"), 22: ("Separator Cooling Water Outlet Temp", "Deg C"),
}
for i, c in enumerate("ABCDEF"):
    XMEAS[23 + i] = (f"Reactor feed analysis, component {c}", "mol%")
for i, c in enumerate("ABCDEFGH"):
    XMEAS[29 + i] = (f"Purge gas analysis, component {c}", "mol%")
for i, c in enumerate("DEFGH"):
    XMEAS[37 + i] = (f"Product analysis, component {c}", "mol%")
XMV = {1: "D Feed Flow", 2: "E Feed Flow", 3: "A Feed Flow", 4: "A and C Feed Flow", 5: "Compressor Recycle Valve",
       6: "Purge Valve", 7: "Separator Pot Liquid Flow", 8: "Stripper Liquid Product Flow", 9: "Stripper Steam Valve",
       10: "Reactor Cooling Water Flow", 11: "Condenser Cooling Water Flow", 12: "Agitator Speed"}

# 벤치마크에서 두 정책이 공유하는 목표(바꾸면 비교가 무의미해짐). rag/legacy/STUDIO.md
FIXED = {11: "생산 목표(제품 유량)", 20: "제품 품질 목표(제품 E)", 18: "반응기 온도 목표",
         17: "반응기 액위 목표", 7: "분리기 액위 목표", 8: "스트리퍼 액위 목표"}
# 감독기가 바꿀 수 있게 열어 둔 상위 목표와 시험 격자(예시). 나머지 최상위 목표는 격자 미정.
ADJUSTABLE = {19: [0.5, -0.5, 1.0, -1.0, 1.5, -1.5, 2.0, -2.0]}
# 루프가 읽지 않는 계측 → 같은 장치의 밸브(변수표 기준. 물리 영향은 가설)
RELATED_VALVES = {6: [4, 5], 11: [11], 14: [7], 16: [9], 20: [5], 22: [11]}
ANALYZER = [(range(23, 29), "반응기 공급 분석기", 360, "S04"), (range(29, 37), "퍼지 분석기", 360, "S04"),
            (range(37, 42), "제품 분석기", 900, "S06")]
PURGE_TRIGGERS = [7, 10, 13, 30]


def fmt(x: float) -> str:
    return f"{x:.4g}"


def loop_text(n: int) -> str:
    p = preset(n)
    t = p.tuning
    out = (f"SETPT({t.output.index})" if t.output.kind == "setpoint" else f"XMV({t.output.index}) {XMV[t.output.index]}")
    kind = "PI" if t.reset_h is not None else "P 전용"
    return (f"CONTRL{n}: XMEAS({t.measurement}) 을 SETPT({p.setpoint_index}) 기동값 {fmt(p.setpoint)} 로 추종, "
            f"{kind}, {t.period.value} s 주기, 이득 {fmt(t.gain)}, span {fmt(t.span)} → {out}")


def analyzer(n: int):
    return next(((name, period, sop) for r, name, period, sop in ANALYZER if n in r), None)


def chain_string(chain) -> str:
    parts = []
    for p in chain:
        parts.append(f"CONTRL{p.tuning.number}")
        o = p.tuning.output
        parts.append(f"SETPT({o.index})" if o.kind == "setpoint" else f"XMV({o.index})")
    return " → ".join(parts)


def sensor_step(n: int) -> tuple[dict, dict]:
    name, unit = XMEAS[n]
    reader = loop_reading(n)
    chain = chain_for_measurement(n)
    lines, refs, kind = [], ["studio_control_src", "variables"], "observe"
    to = "operator_review"
    if reader is not None:
        sp = reader.setpoint_index
        lines.append(f"[코드] {loop_text(reader.tuning.number)}.")
        if len(chain) > 1:
            lines.append(f"[코드] cascade: {chain_string(chain)}.")
        w = writer_of(sp)
        if w is not None:
            lines.append(f"[코드] SETPT({sp}) 는 상위 CONTRL{w.tuning.number} 가 운전 중 바꾸므로 기동값과 달라도 정상이다. "
                         f"상위 계측 XMEAS({w.tuning.measurement}) 를 함께 본다.")
        if reader.tuning.reset_h is None:
            lines.append("[코드] P 전용 속도형 루프는 오차가 일정해지면 더 움직이지 않는다. 남은 편차는 정상 오프셋일 수 있다.")
        else:
            lines.append(f"[코드] PI 루프라 오차가 남아 있는 동안 {reader.tuning.period.value} s 마다 계속 출력을 바꾼다.")
        final = chain[-1].tuning.output.index
        lines.append(f"[코드] 최종 밸브 XMV({final}) {XMV[final]} 가 0% 또는 100% 에 붙어 있으면(CONSHAND 클램프) "
                     "이 루프는 더 대응할 수 없다 → 운영자 검토.")
        if final == 6:
            lines.append(f"[코드] XMV(6) 은 분리기 압력 오버라이드가 잡을 수 있다(check_purge_override).")
            to = "check_purge_override"
        top = chain[0].setpoint_index
        if top in FIXED:
            lines.append(f"[정책] SETPT({top}) 는 {FIXED[top]}로 벤치마크 공유 목표다. 바꾸지 않는다.")
            kind = "loop_verify"
        elif top in ADJUSTABLE:
            lines.append(f"[정책] SETPT({top}) 는 감독기 조정 대상이다 → simulate_purge_b_cases.")
            kind, to = "case_review", "check_purge_override"
        elif top in top_level_setpoints():
            lines.append(f"[정책] SETPT({top}) 는 코드상 최상위 목표지만 승인된 시험 격자가 없다. 제안하지 않는다(운영자 검토).")
            kind = "loop_verify"
    else:
        if n == 13:
            lines += [f"[코드] 이 계측을 읽는 실행 중 루프는 없다(CONTRL22 는 정의만 있고 실행되지 않음). "
                      f"대신 CONTRL6 오버라이드가 읽는다: ≥ {fmt(OVERRIDE_HIGH)} kPa 이면 XMV(6)=100% 고정, "
                      f"≤ {fmt(OVERRIDE_LOW)} 이면 0% 고정, {fmt(OVERRIDE_RELEASE)} 을 지나면 해제.",
                      "→ check_purge_override."]
            kind, to = "case_review", "check_purge_override"
        elif n == 7:
            lines += ["[코드] 반응기 압력을 읽는 루프는 없다. 이 구조에서 압력에 닿는 조작은 퍼지 경로 "
                      f"({chain_string(chain_for_measurement(30))}) 와 분리기 압력 오버라이드뿐이다.",
                      "[코드] 퍼지 B 목표를 낮추면 CONTRL19 가 SETPT(6)(퍼지 유량 목표)을 올린다(simulate_purge_b_cases 표). "
                      "[가설] 퍼지가 늘면 반응기 압력이 내려간다. 시뮬레이션으로 확인한다.",
                      "[문헌] 반응기 압력 셧다운 한계는 Downs & Vogel 표 6 의 3000 kPa(g).",
                      "→ check_purge_override 후 simulate_purge_b_cases."]
            refs.append("downs_vogel")
            kind, to = "case_review", "check_purge_override"
        else:
            lines.append("[코드] 이 계측을 읽는 제어 루프는 없다. 등록 대응 없음 → 관측 기록 후 운영자 검토.")
            for v in RELATED_VALVES.get(n, []):
                own = next((p for p in [owner_of(i) for i in range(1, 21)] if p and p.tuning.output.kind == "valve"
                            and p.tuning.output.index == v), None)
                who = f"CONTRL{own.tuning.number}(XMEAS({own.tuning.measurement}))" if own else "제어기 없음"
                lines.append(f"[변수표] 같은 장치 밸브 XMV({v}) {XMV[v]} 는 {who} 의 출력이다.")
    a = analyzer(n)
    if a:
        lines.append(f"[팀 SOP {a[2]}] {a[0]}는 {a[1] // 60} 분 간격으로 이전 시료를 보고한다. 같은 시료가 반복되면 새 정보가 아니다.")
        refs.append("team_sop")
    step = {
        "id": f"sensor_XMEAS_{n}",
        "actor": "운영자 / Zoro AI",
        "instruction_ko": f"XMEAS({n}) {name} [{unit}] 경보(높음/낮음) 대응. " + " ".join(lines),
        "retrieval_text_en": (f"XMEAS({n}) {name} alarm high low response "
                              + " ".join(f"CONTRL{p.tuning.number}" for p in chain)
                              + (f" XMV({chain[-1].tuning.output.index})" if chain else "")),
        "source_refs": refs,
        "response_kind": kind,
        "control_chain": [p.tuning.number for p in chain],
        "transitions": [{"when_ko": "확인 완료", "to": to}, {"when_ko": "밸브 포화·정보 부족", "to": "operator_review"}],
    }
    row = {"n": n, "name": name, "unit": unit, "chain": chain_string(chain) or "-",
           "control": ("P" if reader and reader.tuning.reset_h is None else "PI") if reader else "-",
           "period": reader.tuning.period.value if reader else "-", "kind": kind}
    return step, row


def purge_case_step() -> tuple[dict, list[dict]]:
    sp = preset(19)
    cands, rows = [], []
    for d in ADJUSTABLE[19]:  # 계산 결과 전체는 docs/SOP_TEP_STUDIO.md 표
        code = f"{'UP' if d > 0 else 'DN'}{int(round(abs(d) * 100)):03d}"
        resp = cascade_response(19, d)
        cands.append([f"SIM_SP19_{code}", d])  # 하위 루프 변화는 tep_control.cascade_response 로 재계산
        rows.append({"delta": d, "sp6": resp[0].delta, "xmv6": resp[1].delta, "integ": resp[0].integral_per_run})
    steps = cascade_response(19, -1.0)
    instr = (f"SETPT(19) 를 현재값 대비 ±0.5~±2.0 mol% 바꾼 case 를 hold(SOP 단독)와 같은 조건에서 시뮬레이션한다. "
             f"[코드] ΔSETPT(6)=K19·ΔSETPT(19)·100/26/100: −1 mol% → SETPT(6) {steps[0].delta:+.4f} kscmh, "
             f"XMV(6) {steps[1].delta:+.2f}%p 즉시. 오차가 남으면 360 s 마다 SETPT(6) {steps[0].integral_per_run:+.5f}. "
             "XMV(6)+즉시 변화가 0~100% 밖이면 시험하지 않는다. 오버라이드 래치 중에는 CONTRL6 이 돌지 않아 효과가 없다.")
    step = {
        "id": "simulate_purge_b_cases", "actor": "Zoro AI 시뮬레이션 검토 모듈",
        "instruction_ko": instr,
        "retrieval_text_en": "XMEAS(7) XMEAS(10) XMEAS(13) XMEAS(30) reactor pressure purge rate purge B composition "
                             "CONTRL19 CONTRL6 XMV(6) SETPT(19) setpoint case simulation",
        "source_refs": ["studio_control_src", "team_sop", "studio_md", "benchmark"],
        "action": {
            "kind": "simulation_review",
            "trigger_variable_ids": [f"XMEAS_{n}" for n in PURGE_TRIGGERS],
            "plan": {
                "simulator": "tep_studio_braatz",
                "parameter": "SETPT(19)", "scheme_call": "Scheme::set_setpoint(19, value)",
                "loop": "CONTRL19", "controlled_tag": "XMEAS(30)", "unit": "mol%",
                "preset_value": round(sp.setpoint, 6),
                "value_basis": "relative_to_active_setpoint",
                "candidates": cands,
                "candidate_fields": ["catalog_action_id", "delta_mol_pct"],
                "baseline": "SIM_HOLD",
                "preconditions": ["override_released", "XMEAS(30)_fresh", "active_SETPT(19)_known"],
                "valve_limit_rule": "XMV(6)+immediate in [0,100]",
                "selection_rule": "docs/BENCHMARK.md (patience 4)",
                "constraints": {"reactor_pressure_shutdown_kpag": 3000, "production_ratio_min": 0.99},
                "objective": {"metric": "benchmark_vs_sop_only", "direction": "maximize"},
                "values_status": "example",
                "results": None,
            },
        },
        "transitions": [{"when_ko": "시뮬레이션 결과 확보", "to": "operator_review"},
                        {"when_ko": "전제 불충족", "to": "hold_current_targets"}],
    }
    return step, rows


def build() -> dict:
    sensor_steps, rows = zip(*(sensor_step(n) for n in range(1, 42)))
    case_step, case_rows = purge_case_step()
    common = [
        {"id": "verify_event", "actor": "운영자 / Zoro AI",
         "instruction_ko": "경보 태그·단위·측정 시각·품질·시료 ID 를 확인한다. 시뮬레이터가 주입한 외란 번호·고장 일정은 판단에 쓰지 않는다. "
                           "센서별 단계(sensor_XMEAS_n)로 간다.",
         "retrieval_text_en": "alarm verification measurement time quality sample id",
         "source_refs": ["team_sop"], "transitions": [{"when_ko": "확인 완료", "to": "sensor_XMEAS_7"}]},
        {"id": "check_purge_override", "actor": "운영자 / Zoro AI",
         "instruction_ko": f"[코드] CONTRL6 오버라이드 상태를 XMEAS(13) 로 판정한다: ≥ {fmt(OVERRIDE_HIGH)} kPa 이면 XMV(6)=100% 고정(래치), "
                           f"≤ {fmt(OVERRIDE_LOW)} 이면 0% 고정, 래치는 {fmt(OVERRIDE_RELEASE)} 을 지나야 풀리고 그때 SETPT(6)={fmt(OVERRIDE_RESET_SETPOINT)}, "
                           f"XMV(6)={fmt(OVERRIDE_RESET_VALVE)}% 로 초기화된다. 래치 중에는 CONTRL6 이 실행되지 않는다. "
                           "[팀 SOP S05] 래치 중이면 경제 조정을 보류하고 hold_current_targets 로 간다.",
         "retrieval_text_en": "XMEAS(13) separator pressure override purge valve XMV(6) 2950 2300 2633.7 latch release",
         "source_refs": ["studio_control_src", "team_sop"],
         "transitions": [{"when_ko": "래치 없음", "to": "simulate_purge_b_cases"},
                         {"when_ko": "래치 중", "to": "hold_current_targets"}]},
        {"id": "hold_current_targets", "actor": "Zoro AI",
         "instruction_ko": "상위 목표를 바꾸지 않는다(hold). [팀 SOP S08] 바꿀 근거가 부족하면 현재 목표를 유지한다.",
         "source_refs": ["team_sop"], "transitions": [{"when_ko": "기록", "to": "operator_review"}]},
        {"id": "operator_review", "actor": "운영자",
         "instruction_ko": "선택된 case(또는 hold)와 심슨 대비 비용·정확도, 즉시 제어기 변화, 남은 불확실성을 검토한다. "
                           "적용은 감독기가 한다. 이 SOP 는 설정값을 직접 적용하지 않는다.",
         "source_refs": ["benchmark"], "transitions": [{"when_ko": "검토 완료", "to": "review_completed"}]},
    ]
    return {
        "schema_version": "tep-appendix-d-test-protocol-1",
        "document_id": DOC_ID, "document_version": VERSION, "test_id": None, "scenario_id": None,
        "title_ko": "TEP Studio(Braatz 폐루프) 센서별 경보 대응 SOP",
        "summary": {"text_ko": "41개 계측마다 Studio 제어 코드의 루프 연결·P/PI·주기·최종 밸브·오버라이드를 적고, "
                               "감독기가 바꿀 수 있는 상위 목표(SETPT(19))의 하위 루프 즉시 변화를 코드 식으로 계산했다.",
                    "text_en": "TEP Studio Braatz closed-loop sensor alarm response: control loops, cascade, purge override, "
                               "SETPT(19) cases with computed lower setpoint and valve increments.",
                    "operator_action_status": "NOT_EXECUTED"},
        "scope": {"language": "ko", "source_model": "TEP Studio (tep-rust tepsim-control, Braatz preset)",
                  "source_event_kind": "authored_review_protocol",
                  "protocol_kind": "code_derived_review_protocol_values_are_examples",
                  "physical_action_executed": False},
        "applicability": {"simulator": "tep_studio_braatz"},
        "protocol": {"authorship": "generated by scripts/build_tep_studio_sop.py from server/tep_control.py",
                     "start_step": "verify_event",
                     "steps": common[:2] + [case_step] + list(sensor_steps) + common[2:],
                     "terminals": [{"id": "review_completed", "meaning_ko": "검토 완료. 실제 적용은 감독기 결정"}]},
        "provenance": {"source_registry": {
            "studio_control_src": {"url": SOURCE, "note": "PRESET, Loop::increment, Override::step, Scheme::run"},
            "team_sop": {"file": "rag/legacy/Zoro-RAG-Knowledge/SOP/tep-studio-closed-loop.json", "version": "1.0"},
            "studio_md": {"file": "rag/legacy/STUDIO.md", "section": "Zoro 입력, 출력, 실행 경계"},
            "downs_vogel": {"file": "zoro_rag_knowledge/01_Process_Knowledge/Downs_Vogel_1993.pdf", "locator": "Table 6"},
            "variables": {"file": "zoro_rag_knowledge/03_Control_and_Variables/README.md"},
            "benchmark": {"file": "docs/BENCHMARK.md"}}},
        "known_limits": [
            "제어기 증분은 측정값이 아직 변하지 않았다는 가정의 즉시 변화다. 공정 응답은 시뮬레이션으로만 확인한다.",
            "[가설] 표시는 공정 영향 추정이며 코드에서 나온 사실이 아니다.",
            "SETPT(19) 후보 폭·순서와 허용 범위는 예시 설정이다. 다른 최상위 목표(5, 13, 14, 15)는 격자가 정해지지 않았다.",
            "배포된 Studio WASM 이 이 소스 커밋과 같은 빌드인지는 확인할 수 없다.",
        ],
        "resolution_check": {"status": "NOT_RUN", "simulation_results": None},
        "_rows": {"sensors": list(rows), "purge_cases": case_rows},
    }


def write_md(doc: dict) -> None:
    rows, cases = doc["_rows"]["sensors"], doc["_rows"]["purge_cases"]
    kind_ko = {"observe": "관측·운영자 검토", "loop_verify": "루프 동작·밸브 포화 확인 (상위 목표 변경 안 함)",
               "case_review": "오버라이드 확인 → SETPT(19) case 시뮬레이션"}
    lines = [
        "# TEP Studio 센서별 대응 SOP (생성 문서)", "",
        f"`scripts/build_tep_studio_sop.py` 가 `server/tep_control.py`(tep-rust tepsim-control 이식)에서 생성합니다. "
        f"RAG 용 원본은 `{OUT_JSON.relative_to(ROOT).as_posix()}` 입니다.", "",
        f"원본 코드: <{SOURCE}>", "",
        "## 1. 계측별 제어 루프와 대응", "",
        "| 계측 | 이름 | 단위 | 제어 경로 (코드) | P/PI | 주기 s | 대응 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| XMEAS({r['n']}) | {r['name']} | {r['unit']} | {r['chain']} | {r['control']} | {r['period']} | {kind_ko[r['kind']]} |")
    lines += ["", f"최상위 목표(어떤 루프도 덮어쓰지 않는 SETPT): {top_level_setpoints()}. "
              f"이 중 벤치마크 공유 목표로 고정: {sorted(FIXED)}. 감독기 조정 대상: {sorted(ADJUSTABLE)}.", "",
              "## 2. SETPT(19) case 와 하위 루프 즉시 변화", "",
              "속도형 증분(lib.rs `Loop::increment`)에서 목표를 Δ 바꾸면 비례항이 한 번 `K·Δe` 만큼 움직인다. "
              "`Δe = Δ·100/span`, 하위 목표로 가는 출력은 `ΔSETPT(inner) = Δu·span_inner/100`(lib.rs:1050).", "",
              "| ΔSETPT(19) mol% | ΔSETPT(6) kscmh (즉시) | ΔXMV(6) %p (즉시) | ΔSETPT(6) / 360 s (오차 유지 시) |",
              "|---|---|---|---|"]
    for c in cases:
        lines.append(f"| {c['delta']:+g} | {c['sp6']:+.4f} | {c['xmv6']:+.2f} | {c['integ']:+.5f} |")
    lines += ["", "현재 XMV(6) 에 즉시 변화를 더한 값이 0~100% 를 벗어나면 그 case 는 시험하지 않습니다. "
              f"기동 시 XMV(6) 은 40.064% 이므로 −2.0 mol% 까지는 범위 안입니다.", "",
              "## 3. 한계", ""] + [f"- {x}" for x in doc["known_limits"]]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    doc = build()
    write_md(doc)
    doc.pop("_rows")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SOP -> {OUT_JSON}\nMD  -> {OUT_MD}")


if __name__ == "__main__":
    main()
