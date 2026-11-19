"""ProtoPilot 시뮬레이터(tep-studio, Ricker 다중 루프 제어기)용 case·정책·SOP 근거 구성.

알람이 뜨면 그 시점의 설정값과 팀 SOP(server/demo-sops.json)로 이번 사건의 카탈로그를 만든다.
목표값은 이 코드의 규칙으로 계산한다(예: 생산 목표 × 0.95). 모델은 이 중에서 고르기만 한다.

Ricker 제어기 설정값 9개 중 PV 가 계측 하나인 7개를 쓴다(ya/yac 는 유도값이라 제외).
수치(감량 폭·지연, 허용 범위, 여유폭)는 팀 SOP 와 예시 설정이며 검증된 운전 기준이 아니다.
"""
from __future__ import annotations

from typing import Any

from .benchmark import BenchmarkPolicy, Margins, TrackedVariable
from .cases import ResponseCase, RevertRule, SetpointChange, SetpointSpec, SopRef
from .policy import AgentPolicy, Budget, FreshnessPolicy, RecommendationPolicy
from .rag import Evidence, NormalizedRagResult, RagCandidate
from .registry import ActionCatalog, ActionKind, ActionSpec, RerunPolicy

# server/catalog.py PV_TAGS
SETPOINT_PV = {"reactor_pressure": "XMEAS(7)", "reactor_temperature": "XMEAS(9)", "reactor_level": "XMEAS(8)",
               "separator_level": "XMEAS(12)", "stripper_level": "XMEAS(15)", "production_rate": "XMEAS(17)",
               "pct_g": "XMEAS(40)"}
TRACKED_SETPOINTS = ("reactor_pressure", "reactor_temperature", "reactor_level", "separator_level", "stripper_level")
# server/alarm_response.py CANDIDATES: (감량 비율, 적용 지연 s). None = 유지
SOP_CANDIDATES = {"hold": None, "reduce_now": (0.95, 0.0), "reduce_30": (0.95, 1800.0), "reduce_60": (0.95, 3600.0)}
WATCH = ["XMEAS(7)", "XMEAS(9)", "XMEAS(12)", "XMEAS(17)"]
HORIZON_S = 3.75 * 3600  # server/alarm_response.py HORIZON_HOURS


def action_id(key: str) -> str:
    return "SIM_HOLD" if key == "hold" else f"SIM_{key.upper()}"


def build_alarm_catalog(sop: dict[str, Any], setpoints: dict[str, float],
                        ranges: dict[str, tuple[str, float, float]], alarm_tags: list[str],
                        horizon_s: float = HORIZON_S) -> ActionCatalog:
    """sop: demo-sops.json 항목, setpoints: 현재 Ricker 설정값, ranges: server/catalog.SETPOINTS 의 (단위, 하한, 상한)."""
    revision = sop.get("revision")
    ref = [SopRef(sop_id=sop["id"], version=str(revision) if revision is not None else "", step_id="candidates")]
    prod = setpoints["production_rate"]
    unit, lo, hi = ranges["production_rate"]
    specs = [SetpointSpec(
        setpoint_id="production_rate", scheme_index=None, loop_id="Ricker:production_rate",
        controlled_tag=SETPOINT_PV["production_rate"], description="생산 목표(제품 유량)", unit=unit,
        default_value=prod, allowed_min=lo, allowed_max=hi, max_step=round(prod * 0.10, 6),
        max_rate_per_h=None, min_wait_s=None, values_status="example",
        source="server/catalog.py SETPOINTS 범위, 최대 변경폭 10%는 예시")]
    revert = [RevertRule(rule_id="RV-TRIP", description="모델 트립", signal=None, op="event"),
              RevertRule(rule_id="RV-PROTECTION", description="자동 보호 제어 개입", signal=None, op="event")]
    cases, actions = [], [
        ActionSpec(action_id="QUERY_LIVE_ALARM_TAGS", description="경보 계측의 현재값 조회", kind=ActionKind.SENSOR_QUERY,
                   tool_name="sensor_query", supported=True, args_template={"sensor_ids": list(alarm_tags)},
                   target_sensors=list(alarm_tags), rerun=RerunPolicy(scope="unlimited", max_runs=2),
                   offer_without_rag=True),
        ActionSpec(action_id="REQUEST_HUMAN_REVIEW", description="근거·불확실성을 정리해 운영자 검토 요청 (설정값 유지)",
                   kind=ActionKind.HUMAN_REVIEW, tool_name=None, supported=True, offer_without_rag=True),
    ]
    for key in sop.get("candidates", []):
        rule = SOP_CANDIDATES[key]
        aid = action_id(key)
        if rule is None:
            case = ResponseCase(case_id="CASE-HOLD", policy="hold", description="현재 생산 목표 유지(SOP 단독 기준)",
                                sop_refs=ref, watch_signals=WATCH, review_after_s=1800, revert_rules=[],
                                expected_cost_direction={k: "no_change" for k in ("purge", "product", "compressor", "steam")},
                                expected_direction_basis="목표 변경 없음", values_status="example")
        else:
            ratio, delay = rule
            case = ResponseCase(
                case_id=f"CASE-{key.upper()}", policy="propose_setpoint",
                description=f"생산 목표 {prod:g} → {prod * ratio:g} {unit} ({delay / 60:g}분 후)",
                setpoint_changes=[SetpointChange(setpoint_id="production_rate", target_value=round(prod * ratio, 6),
                                                 unit=unit, apply_at_s=delay)],
                sop_refs=ref, watch_signals=WATCH, review_after_s=max(1800.0, delay + 1800), revert_rules=revert,
                expected_cost_direction={k: "unknown" for k in ("purge", "product", "compressor", "steam")},
                expected_direction_basis=None, values_status="example")
        cases.append(case)
        actions.append(ActionSpec(
            action_id=aid, description=f"{case.description} 시 {horizon_s / 3600:g}시간 분기 예측",
            kind=ActionKind.SIMULATION, tool_name="run_simulation", supported=True, case_id=case.case_id,
            args_template={"snapshot_id": "$snapshot_id", "horizon_s": horizon_s},
            required_fresh_sensors=list(alarm_tags), rerun=RerunPolicy(scope="per_snapshot", max_runs=1),
            requires_rag_evidence=rule is not None, offer_without_rag=rule is None, recommendable=rule is not None))
    if not any(c.case_id == "CASE-HOLD" for c in cases):
        raise ValueError(f"{sop['id']}: SOP 후보에 기준(hold)이 없음")
    return ActionCatalog(label=f"ProtoPilot {sop['id']} rev{sop.get('revision')} — 사건 시점 생성, 수치는 예시",
                         setpoints=specs, cases=cases, actions=actions, conditions=[],
                         aliases={k: action_id(k) for k in sop.get("candidates", [])})


def build_benchmark(setpoints: dict[str, float], limits: dict[str, tuple[float | None, float | None]],
                    units: dict[str, str], horizon_s: float = HORIZON_S) -> BenchmarkPolicy:
    """정확도 목표 = 사건 시점 설정값, 임계값 = 라이브 알람 기준."""
    tracked = []
    for key in TRACKED_SETPOINTS:
        tag = SETPOINT_PV[key]
        lo, hi = limits.get(tag, (None, None))
        if lo is None and hi is None:
            continue
        tracked.append(TrackedVariable(tag=tag, setpoint=setpoints[key], unit=units.get(tag, ""), hi=hi, lo=lo,
                                       source=f"목표: Ricker {key} 현재 설정값 / 임계값: 라이브 알람 기준"))
    return BenchmarkPolicy(
        label="ProtoPilot 알람 대응 — 예시 설정", values_status="example", tracked=tracked,
        horizon_s=horizon_s, final_window_s=3600.0,
        # 팀 SOP 가 5% 감량을 대응으로 두므로 생산량 게이트를 94%로 둔다(경제 비교의 99%와 다름).
        production_ratio_min=0.94, g_mass_pct_range=(45.0, 55.0), max_def_mol_pct=2.0,
        vs_baseline=Margins(cost_rel=0.01, acc_abs=0.03), vs_incumbent=Margins(cost_rel=0.005, acc_abs=0.02),
        non_inferiority=Margins(cost_rel=0.005, acc_abs=0.01), patience=4, max_cases=8)


def build_policy(catalog: ActionCatalog, alarm_tags: list[str], benchmark: BenchmarkPolicy) -> AgentPolicy:
    n_sims = sum(1 for a in catalog.actions if a.kind == ActionKind.SIMULATION)
    return AgentPolicy(
        label="ProtoPilot 알람 대응 — 예시 설정", freshness=FreshnessPolicy(default_max_age_s=300),
        recommendation=RecommendationPolicy(
            baseline_action_id="SIM_HOLD", min_candidate_runs=1, required_safety_checks=[],
            required_fresh_sensors=list(alarm_tags), require_alarm_recovery=True,
            # 분기 시작 시점에 이미 경보 중이므로 구간 전체 초과 여부 대신 마지막 구간 적격 게이트로 판단
            block_if_limit_exceedance=False, require_rag_evidence=True),
        budget=Budget(max_decisions=max(n_sims, benchmark.max_cases + 1) + 5,
                      max_tool_calls=max(n_sims, benchmark.max_cases + 1) + 2,
                      max_simulations=max(n_sims, benchmark.max_cases + 1),
                      max_wall_time_s=None),
        benchmark=benchmark, sop_first=True)


def sop_rag(sop: dict[str, Any], catalog: ActionCatalog) -> NormalizedRagResult:
    """팀 SOP 문서를 검색 결과로 정규화: 확인 항목이 근거, SOP 후보가 대응 후보(별칭 매핑)."""
    revision = sop.get("revision")
    ev = [Evidence(evidence_id=f"EV:{sop['id']}#check{i + 1}", doc_id=sop["id"],
                   doc_version=str(revision) if revision is not None else None,
                   doc_title=sop.get("title"), clause=f"checks[{i + 1}]", text=text, source_uri=sop.get("source_url"))
          for i, text in enumerate(sop.get("checks", []))]
    ids = [e.evidence_id for e in ev]
    cands, warnings = [], []
    for key in sop.get("candidates", []):
        aid, mapping = catalog.resolve_action_ref(key)
        if mapping == "unmapped":
            warnings.append(f"SOP 후보 {key!r} 매핑 실패")
        cands.append(RagCandidate(raw_ref=key, action_id=aid, mapping=mapping, purpose=f"{sop['id']} 후보 {key}",
                                  evidence_ids=ids))
    return NormalizedRagResult(query_id=sop["id"], adapter="protopilot_demo_sop_v1", evidence=ev, candidates=cands,
                               warnings=warnings)
