"""심슨(SOP 단독) CSV 와 SOP+Zoro CSV 를 같은 기준으로 채점하고 비교한다.

    python scripts/benchmark_pair.py SIMPSON.csv ZORO.csv [--policy policy.json] [--alarm alarm_event.json]
           [--start-h 0] [--end-h 48]

두 CSV 는 같은 시드·외란·초기 상태·빌드여야 한다(STUDIO.md 짝비교 조건). 이 스크립트는
시나리오 헤더·표본 시각 일치만 확인하고, 시드·외란 일치는 로그 생성자가 보장해야 한다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sop_ranking.alarm_adapter import adapt_alarm_event  # noqa: E402
from sop_ranking.benchmark import beats, eligible, evaluation_spec, read_studio_csv, score_timeseries  # noqa: E402
from sop_ranking.policy import AgentPolicy  # noqa: E402

DEFAULT_POLICY = ROOT / "examples" / "scenario_tep_purge" / "policy.json"


def _window(times, samples, start_h, end_h):
    keep = [(t, x) for t, x in zip(times, samples)
            if (start_h is None or t >= start_h * 3600 - 1e-6) and (end_h is None or t <= end_h * 3600 + 1e-6)]
    if len(keep) < 2:
        raise ValueError("평가 구간 표본 부족")
    t0 = keep[0][0]
    return [t - t0 for t, _ in keep], [x for _, x in keep]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("simpson_csv", type=Path)
    ap.add_argument("zoro_csv", type=Path)
    ap.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    ap.add_argument("--alarm", type=Path, help="zoro-alarm-event-v1 JSON (경보 임계값을 평가에 반영)")
    ap.add_argument("--start-h", type=float)
    ap.add_argument("--end-h", type=float)
    a = ap.parse_args()

    bench = AgentPolicy.model_validate_json(a.policy.read_text(encoding="utf-8")).benchmark
    if bench is None:
        ap.exit(2, "policy 에 benchmark 설정이 없음\n")
    alarms = []
    if a.alarm:
        alarms = adapt_alarm_event(json.loads(a.alarm.read_text(encoding="utf-8")),
                                   snapshot_id="offline-benchmark").event.alarms
    spec = evaluation_spec(alarms, bench)

    runs = {}
    for name, path in (("simpson", a.simpson_csv), ("zoro", a.zoro_csv)):
        t, x, tripped = read_studio_csv(path)
        t, x = _window(t, x, a.start_h, a.end_h)
        runs[name] = (t, score_timeseries(name, t, x, spec.tracked, bench, tripped))
    (ts, s), (tz, z) = runs["simpson"], runs["zoro"]
    issues = []
    if len(ts) != len(tz) or any(abs(p - q) > 1e-6 for p, q in zip(ts, tz)):
        issues.append("표본 시각 불일치 → 짝비교 불가")
    win, why = beats(z, s, s, bench, bench.vs_baseline)
    out = {
        "policy_label": bench.label, "values_status": bench.values_status,
        "tracked": [v.model_dump() for v in spec.tracked], "untracked_alarm_tags": spec.untracked_alarm_tags,
        "pair_issues": issues,
        "simpson": {**s.model_dump(), "eligible": eligible(s, s, bench)},
        "zoro": {**z.model_dump(), "eligible": eligible(z, s, bench)},
        "delta": {"usd_per_m3_rel": (z.usd_per_m3 - s.usd_per_m3) / s.usd_per_m3
                  if s.usd_per_m3 and z.usd_per_m3 else None,
                  "tracking_accuracy": z.tracking_accuracy - s.tracking_accuracy},
        "zoro_beats_simpson": None if issues else win,
        "reason": why,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
