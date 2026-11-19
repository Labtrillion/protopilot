"""Zoro 대 심슨(SOP 단독, hold) 벤치마크: run 점수, 비교 규칙, case 탐색 종료 규칙.

지표 두 개
- 비용: Downs & Vogel (1993) 표 9 운전비(퍼지·제품 손실·압축기·스팀)를 시간 적분한 뒤 생산량으로 나눈
  USD/m³. zoro-rag/studio_eval.py 와 같은 식이다(tests 에서 validation CSV 로 대조).
  생산량이 정책 간 최대 1% 달라질 수 있으므로 총액이 아니라 m³당 비용으로 비교한다.
- 정확도(tracking accuracy, 0~1): 추적 변수마다 s(t) = max(0, 1 − |x − 목표| / |경보 임계값 − 목표|).
  목표에 있으면 1, 경보 임계값에 닿거나 넘으면 0. 평가 구간 시간 평균 → 변수 가중 평균.

수치(여유폭·patience·관찰 시간)는 모두 예시 설정이다. 근거는 docs/BENCHMARK.md.
"""
from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from .common import Model

# Downs & Vogel (1993) Table 9, zoro-rag/studio_eval.py 와 동일. benchmark USD per kmol.
PURGE_PRICES = (2.206, 0, 6.177, 22.06, 14.56, 17.89, 30.44, 22.94)  # XMEAS(29..36) A..H
PRODUCT_PRICES = (22.06, 14.56, 17.89, 0, 0)  # XMEAS(37..41) D,E,F,G,H
PURGE_KMOL_PER_KSCM = 44.79
PRODUCT_KMOL_PER_M3 = 9.21
POWER_USD_PER_KWH = 0.0536
STEAM_USD_PER_KG = 0.0318
G_MW, H_MW = 62, 76

ValuesStatus = Literal["example", "agreed"]


# ------------------------------------------------------------------ 설정
class TrackedVariable(Model):
    tag: str = Field(description="예: XMEAS(7)")
    setpoint: float = Field(description="정확도 기준값(목표)")
    unit: str
    hi: float | None = Field(description="상한 경보 임계값. null = 상한 없음")
    lo: float | None = Field(description="하한 경보 임계값. null = 하한 없음")
    weight: float = 1.0
    source: str = Field(description="목표·임계값 출처")


class Margins(Model):
    cost_rel: float = Field(description="m³당 비용 상대 차이 (0.01 = 1%)")
    acc_abs: float = Field(description="정확도 절대 차이 (0~1 척도)")


class BenchmarkPolicy(Model):
    label: str
    values_status: ValuesStatus = "example"
    tracked: list[TrackedVariable] = Field(description="항상 추적하는 변수. 경보 변수는 경보 임계값으로 덮어씀")
    reference_setpoints: dict[str, float] = Field(
        default={}, description="경보 태그의 정확도 목표 (tracked 에 없을 때)")
    alarm_weight: float = Field(default=2.0, description="사건 경보 변수의 정확도 가중치(나머지는 각 변수 weight)")
    horizon_s: float = Field(description="case 평가 run 길이 [s]")
    final_window_s: float = Field(description="마지막 이 구간 동안 모든 추적 변수가 임계값 안이어야 적격")
    production_ratio_min: float = Field(description="심슨 생산량 대비 최소 비율")
    g_mass_pct_range: tuple[float, float]
    max_def_mol_pct: float
    vs_baseline: Margins = Field(description="심슨보다 '의미 있게 좋다'의 최소 개선폭")
    vs_incumbent: Margins = Field(description="현재 최선 case 를 갈아치울 최소 개선폭")
    non_inferiority: Margins = Field(description="다른 지표가 이만큼까지만 나빠져도 허용")
    patience: int = Field(description="최선 case 이후 연속으로 이기지 못한 case 수가 이 값이면 종료")
    max_cases: int = Field(description="심슨 외 case 최대 시험 수")


# ------------------------------------------------------------------ 점수
class RunScore(Model):
    """run 하나의 벤치마크 점수. 시뮬레이션 run 이면 가상 예측."""

    run_id: str
    tripped: bool
    usd_per_m3: float | None = Field(description="null = 생산량 0 → 계산 불가")
    cost_usd: dict[str, float] = Field(description="purge/product/power/steam/total, 평가 구간 누적")
    product_m3: float
    tracking_accuracy: float = Field(ge=0, le=1)
    per_variable_accuracy: dict[str, float]
    in_threshold_fraction: float = Field(description="모든 추적 변수가 임계값 안인 시간 비율")
    time_to_stable_s: float | None = Field(description="이후 끝까지 모두 임계값 안인 첫 시각(run 기준). null = 없음")
    final_window_inside: bool
    quality_ok: bool | None = Field(description="null = 품질 계측 없음")
    failed_gates: list[str] = Field(description="심슨 생산량 비교 전 자체 게이트 실패 목록")


def _side_distance(v: TrackedVariable, x: float) -> float | None:
    if x >= v.setpoint and v.hi is not None:
        return v.hi - v.setpoint
    if x < v.setpoint and v.lo is not None:
        return v.setpoint - v.lo
    if v.hi is not None:  # 단측 경보: 반대쪽도 같은 거리로 본다
        return v.hi - v.setpoint
    if v.lo is not None:
        return v.setpoint - v.lo
    return None


def closeness(v: TrackedVariable, x: float) -> float:
    d = _side_distance(v, x)
    if d is None or d <= 0:
        raise ValueError(f"{v.tag}: 목표와 임계값으로 거리 계산 불가")
    return max(0.0, 1.0 - abs(x - v.setpoint) / d)


def inside(v: TrackedVariable, x: float) -> bool:
    return (v.hi is None or x < v.hi) and (v.lo is None or x > v.lo)


def _trapz(ts: list[float], ys: list[float]) -> float:
    return sum((b - a) * (u + w) / 2 for a, b, u, w in zip(ts, ts[1:], ys, ys[1:]))


def _fractions(values: list[float]) -> list[float]:
    pos = [max(0.0, x) for x in values]
    tot = sum(pos)
    if tot <= 0:
        raise ValueError("unusable composition measurement")
    return [x / tot for x in pos]


def cost_rates(x: dict[str, float]) -> tuple[float, float, float, float]:
    """USD/h: purge, product, power, steam (Downs & Vogel 표 9)."""
    purge = _fractions([x[f"XMEAS({n})"] for n in range(29, 37)])
    prod = _fractions([x[f"XMEAS({n})"] for n in range(37, 42)])
    return (PURGE_KMOL_PER_KSCM * x["XMEAS(10)"] * sum(a * p for a, p in zip(purge, PURGE_PRICES)),
            PRODUCT_KMOL_PER_M3 * x["XMEAS(17)"] * sum(a * p for a, p in zip(prod, PRODUCT_PRICES)),
            POWER_USD_PER_KWH * x["XMEAS(20)"], STEAM_USD_PER_KG * x["XMEAS(19)"])


def score_timeseries(run_id: str, times_s: list[float], samples: list[dict[str, float]],
                     tracked: list[TrackedVariable], policy: BenchmarkPolicy, tripped: bool = False) -> RunScore:
    """평가 구간 전체 표본(시각 오름차순, run 기준 초)으로 점수를 낸다. 동은 모듈의 참조 구현."""
    if len(times_s) < 2 or len(times_s) != len(samples):
        raise ValueError("표본 2개 이상, 시각과 표본 수 일치 필요")
    if any(b <= a for a, b in zip(times_s, times_s[1:])):
        raise ValueError("시각이 증가하지 않음")
    hours = [t / 3600 for t in times_s]
    rates = [cost_rates(x) for x in samples]
    names = ("purge", "product", "power", "steam")
    cost = {n: _trapz(hours, [r[i] for r in rates]) for i, n in enumerate(names)}
    cost["total"] = sum(cost.values())
    vol = _trapz(hours, [x["XMEAS(17)"] for x in samples])

    span = times_s[-1] - times_s[0]
    per_var, wsum, acc = {}, 0.0, 0.0
    for v in tracked:
        a = _trapz(times_s, [closeness(v, x[v.tag]) for x in samples]) / span
        per_var[v.tag] = a
        acc += v.weight * a
        wsum += v.weight
    accuracy = acc / wsum if wsum else 0.0
    flags = [all(inside(v, x[v.tag]) for v in tracked) for x in samples]
    in_frac = _trapz(times_s, [1.0 if f else 0.0 for f in flags]) / span
    stable = None
    for i in range(len(flags) - 1, -1, -1):
        if not flags[i]:
            break
        stable = times_s[i]
    final_ok = stable is not None and times_s[-1] - stable >= policy.final_window_s - 1e-6

    q_ok: bool | None = None
    if all(f"XMEAS({n})" in samples[0] for n in range(37, 42)):
        q_ok = True
        lo, hi = policy.g_mass_pct_range
        for x in samples:
            p = _fractions([x[f"XMEAS({n})"] for n in range(37, 42)])
            g, h = G_MW * p[3], H_MW * p[4]
            if g + h <= 0 or not lo <= 100 * g / (g + h) <= hi or 100 * sum(p[:3]) > policy.max_def_mol_pct:
                q_ok = False
                break
    failed = [n for n, bad in (("no_trip", tripped), ("final_window_inside", not final_ok),
                               ("quality", q_ok is False)) if bad]
    return RunScore(run_id=run_id, tripped=tripped, usd_per_m3=cost["total"] / vol if vol > 0 else None,
                    cost_usd=cost, product_m3=vol, tracking_accuracy=round(accuracy, 6),
                    per_variable_accuracy={k: round(a, 6) for k, a in per_var.items()},
                    in_threshold_fraction=round(in_frac, 6), time_to_stable_s=stable,
                    final_window_inside=final_ok, quality_ok=q_ok, failed_gates=failed)


def read_studio_csv(path: Path) -> tuple[list[float], list[dict[str, float]], bool]:
    """TEP Studio CSV → (run 기준 초, 표본, tripped). fault/hours_since_onset 열은 읽지 않는다."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        notes = []
        for line in f:
            if not line.startswith("#"):
                break
            notes.append(line.strip())
        fields = next(csv.reader([line]))
        cols = {int(m[1]): n for n in fields if (m := re.match(r"^XMEAS_(\d+)_", n))}
        xmv = {int(m[1]): n for n in fields if (m := re.match(r"^XMV_(\d+)_", n))}
        times, samples = [], []
        for row in csv.DictReader(f, fieldnames=fields):
            t = float(row["time_hours"]) * 3600
            x = {f"XMEAS({n})": float(row[c]) for n, c in cols.items()}
            x.update({f"XMV({n})": float(row[c]) for n, c in xmv.items()})
            if not all(map(math.isfinite, [t, *x.values()])):
                raise ValueError("non-finite value")
            times.append(t)
            samples.append(x)
    outcome = next((n.removeprefix("# outcome: ").split(",")[0] for n in notes if n.startswith("# outcome: ")), None)
    return times, samples, outcome not in (None, "completed")


# ------------------------------------------------------------------ 비교
def eligible(run: RunScore, baseline: RunScore, policy: BenchmarkPolicy) -> tuple[bool, list[str]]:
    """자체 게이트 + 심슨 대비 생산량 게이트."""
    failed = list(run.failed_gates)
    if run is not baseline and run.product_m3 < policy.production_ratio_min * baseline.product_m3:
        failed.append("production_vs_baseline")
    if run.usd_per_m3 is None:
        failed.append("no_production")
    return not failed, failed


def beats(a: RunScore, b: RunScore, baseline: RunScore, policy: BenchmarkPolicy, margin: Margins) -> tuple[bool, str]:
    """a 가 b 보다 의미 있게 좋은가. 적격이 먼저, 그다음 ε-지배(한 지표는 margin 이상 개선, 다른 지표는 허용폭 안)."""
    ea, fa = eligible(a, baseline, policy)
    eb, _ = eligible(b, baseline, policy)
    if not ea:
        return False, f"부적격({', '.join(fa)})"
    if not eb:
        return True, "상대가 부적격, 이쪽은 적격"
    ni = policy.non_inferiority
    dc = (a.usd_per_m3 - b.usd_per_m3) / b.usd_per_m3
    da = a.tracking_accuracy - b.tracking_accuracy
    if dc > ni.cost_rel:
        return False, f"비용 {dc:+.2%} > 허용 {ni.cost_rel:.2%}"
    if da < -ni.acc_abs:
        return False, f"정확도 {da:+.3f} < 허용 -{ni.acc_abs}"
    if da >= margin.acc_abs or dc <= -margin.cost_rel:
        return True, f"정확도 {da:+.3f}, 비용 {dc:+.2%}"
    return False, f"개선폭 부족(정확도 {da:+.3f} < {margin.acc_abs}, 비용 {dc:+.2%} > -{margin.cost_rel:.2%})"


class SelectionStep(Model):
    run_id: str
    action_id: str | None
    beats_baseline: bool
    vs_baseline: str
    beats_incumbent: bool | None = Field(description="null = 아직 최선 case 없음")
    vs_incumbent: str | None
    incumbent_after: str | None


class CaseSelection(Model):
    status: Literal["continue", "select_case", "select_baseline", "none_acceptable"]
    reason: str
    baseline_run_id: str
    selected_run_id: str | None
    selected_action_id: str | None
    since_incumbent: int = Field(description="최선 case 이후 이기지 못한 case 수")
    tested: int
    remaining: int
    steps: list[SelectionStep]


def select_case(baseline: RunScore, tested: list[tuple[RunScore, str | None]], remaining: int,
                policy: BenchmarkPolicy) -> CaseSelection:
    """시험 순서대로 본다. case_n 이 심슨을 이기고, 이후 patience 개가 모두 case_n 을 이기지 못하면 case_n."""
    best: tuple[RunScore, str | None] | None = None
    since, steps = 0, []
    for run, aid in tested:
        bb, wb = beats(run, baseline, baseline, policy, policy.vs_baseline)
        bi = wi = None
        if best is None:
            if bb:
                best, since = (run, aid), 0
        else:
            bi, wi = beats(run, best[0], baseline, policy, policy.vs_incumbent)
            if bb and bi:
                best, since = (run, aid), 0
            else:
                since += 1
        steps.append(SelectionStep(run_id=run.run_id, action_id=aid, beats_baseline=bb, vs_baseline=wb,
                                   beats_incumbent=bi, vs_incumbent=wi,
                                   incumbent_after=best[0].run_id if best else None))
    n = len(tested)
    exhausted = remaining <= 0 or n >= policy.max_cases
    common = dict(baseline_run_id=baseline.run_id, since_incumbent=since, tested=n,
                  remaining=max(0, min(remaining, policy.max_cases - n)), steps=steps)
    if best is not None and since >= policy.patience:
        return CaseSelection(status="select_case", selected_run_id=best[0].run_id, selected_action_id=best[1],
                             reason=f"{best[0].run_id} 이후 {since}개 case 가 모두 이기지 못함(patience {policy.patience})",
                             **common)
    if exhausted:
        if best is not None:
            return CaseSelection(status="select_case", selected_run_id=best[0].run_id, selected_action_id=best[1],
                                 reason=f"시험할 case 소진, 최선 {best[0].run_id}", **common)
        if eligible(baseline, baseline, policy)[0]:
            return CaseSelection(status="select_baseline", selected_run_id=baseline.run_id, selected_action_id=None,
                                 reason="심슨을 의미 있게 이긴 case 없음, 심슨은 적격 → hold", **common)
        return CaseSelection(status="none_acceptable", selected_run_id=None, selected_action_id=None,
                             reason="적격 case 없음, 심슨도 부적격 → 운영자 검토", **common)
    return CaseSelection(status="continue", selected_run_id=best[0].run_id if best else None,
                         selected_action_id=best[1] if best else None,
                         reason=(f"최선 {best[0].run_id}, 이후 {since}/{policy.patience}" if best
                                 else "아직 심슨을 이긴 case 없음"), **common)


# ------------------------------------------------------------------ 평가 명세 (시뮬레이션 요청에 첨부)
class EvaluationSpec(Model):
    """모든 case run 을 같은 기준으로 채점하도록 시뮬레이션 요청에 붙이는 평가 명세."""

    policy_label: str
    values_status: ValuesStatus
    horizon_s: float
    final_window_s: float
    tracked: list[TrackedVariable]
    untracked_alarm_tags: list[str] = Field(default=[], description="목표값이 없어 정확도에서 빠진 경보 태그")
    production_ratio_min: float
    g_mass_pct_range: tuple[float, float]
    max_def_mol_pct: float


def evaluation_spec(alarms, policy: BenchmarkPolicy) -> EvaluationSpec:
    """정책의 추적 변수 + 사건 경보 임계값. 경보 임계값이 있으면 그 값을 쓴다."""
    tracked = {v.tag: v.model_copy() for v in policy.tracked}
    missing = []
    for a in alarms:
        if a.threshold is None:
            continue
        side = "hi" if a.condition in ("high", "high_high") else "lo" if a.condition in ("low", "low_low") else None
        if side is None:
            continue
        v = tracked.get(a.sensor_id)
        if v is None:
            sp = policy.reference_setpoints.get(a.sensor_id)
            if sp is None:
                missing.append(a.sensor_id)
                continue
            v = TrackedVariable(tag=a.sensor_id, setpoint=sp, unit=a.unit or "", hi=None, lo=None,
                                source=f"목표: reference_setpoints / 임계값: 경보 {a.alarm_id}")
        else:
            v = v.model_copy(update={"source": v.source + f" / 임계값: 경보 {a.alarm_id}"})
        tracked[a.sensor_id] = v.model_copy(update={side: a.threshold, "weight": policy.alarm_weight})
    return EvaluationSpec(policy_label=policy.label, values_status=policy.values_status, horizon_s=policy.horizon_s,
                          final_window_s=policy.final_window_s, tracked=list(tracked.values()),
                          untracked_alarm_tags=sorted(set(missing)), production_ratio_min=policy.production_ratio_min,
                          g_mass_pct_range=policy.g_mass_pct_range, max_def_mol_pct=policy.max_def_mol_pct)
