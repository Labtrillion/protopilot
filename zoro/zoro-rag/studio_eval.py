"""Score a full-resolution CMU TEP Studio CSV, or compare matched policy runs."""
import argparse
import csv
import json
import math
import re
from pathlib import Path
from statistics import mean

# Downs & Vogel (1993), Table 9. Prices in benchmark dollars per kmol.
PURGE_PRICES = (2.206, 0, 6.177, 22.06, 14.56, 17.89, 30.44, 22.94)
PRODUCT_PRICES = (22.06, 14.56, 17.89, 0, 0)
BANDS = {9: (120.40, 0.50), 8: (75, 5), 12: (50, 5), 15: (50, 5)}
COST_NAMES = ("purge", "product", "power", "steam")


def fractions(values):
    # Noisy analyser readings can be negative and may not sum to 100.
    positive = [max(0.0, x) for x in values]
    total = sum(positive)
    if total <= 0:
        raise ValueError("unusable composition measurement")
    return [x / total for x in positive]


def read_run(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        notes = []
        for line in stream:
            if not line.startswith("#"):
                break
            notes.append(line.strip())
        else:
            raise ValueError("missing Studio CSV header")
        fields = next(csv.reader([line]))
        reader = csv.DictReader(stream, fieldnames=fields)
        channels = {}
        for name in fields:
            match = re.match(r"^XMEAS_(\d+)_", name)
            if match:
                channels[int(match[1])] = name
        if set(channels) != set(range(1, 42)):
            raise ValueError("expected Studio XMEAS 1..41")
        samples = []
        for row in reader:
            t = float(row["time_hours"])
            x = [0.] + [float(row[channels[n]]) for n in range(1, 42)]
            if not all(map(math.isfinite, [t, *x])):
                raise ValueError("non-finite measurement or time")
            samples.append((t, x))
    def header(prefix):
        return next(l.removeprefix(prefix) for l in notes if l.startswith(prefix))
    scenario_text = header("# scenario: ")
    scenario = dict(v.split("=", 1) for v in scenario_text.split(";")[1:])
    return (samples, scenario, scenario_text, header("# TEP Studio export, "),
            header("# outcome: ").split(",")[0])


def time_in_band(times, flags):
    outside = [i for i, flag in enumerate(flags) if not flag]
    status = "maintained"
    first_confirmed = final_entry = None
    if outside:
        status = "outside"
        entry = None
        for i in range(outside[0] + 1, len(flags)):
            if not flags[i]:
                entry = None
                continue
            if entry is None:
                entry = times[i]
            if times[i] - entry >= 1.0 - 1e-8 and first_confirmed is None:
                first_confirmed = times[i]
        if outside[-1] + 1 < len(times):
            final_entry = times[outside[-1] + 1]
        if final_entry is not None and times[-1] - final_entry >= 1.0 - 1e-8:
            status = "recovered"
        elif first_confirmed is not None:
            status = "recurred"
    return {"status": status, "outside_samples": len(outside),
            "first_outside_h": times[outside[0]] if outside else None,
            "first_confirmed_after_1h": first_confirmed,
            "final_reentry_h": final_entry,
            "final_2h_all_in_band": times[-1]-times[0] >= 2 and
            all(f for t, f in zip(times, flags) if t > times[-1]-2+1e-8)}


def score(path):
    samples, scenario, token, model, outcome = read_run(path)
    if len(samples) < 2:
        raise ValueError("at least two samples needed")
    times = [t for t, _ in samples]
    dt = float(scenario["step"])
    interval = dt * int(scenario["every"])
    horizon = float(scenario["hours"])
    if not (dt > 0 and 0 < interval <= .05+1e-9 and horizon >= 2):
        raise ValueError("use a horizon of at least 2 h and sample at least every 3 min")
    for a, b in zip(times, times[1:]):
        if not math.isclose(b-a, interval, rel_tol=0, abs_tol=1e-7):
            raise ValueError("non-uniform or incomplete Studio sample grid")
    flags, fine, rates, quality, pressure = [], [], [], [], []
    for _, x in samples:
        purge = fractions(x[29:37])
        product = fractions(x[37:42])
        # Nominal conversions in Table 9, applied to measured flows.
        rates.append((
            44.79*x[10]*sum(v*p for v, p in zip(purge, PURGE_PRICES)),
            9.21*x[17]*sum(v*p for v, p in zip(product, PRODUCT_PRICES)),
            .0536*x[20], .0318*x[19], x[17]))
        flags.append(all(abs(x[n]-sp) <= band for n, (sp, band) in BANDS.items()))
        fine.append(abs(x[9]-120.40) <= .10)
        g, h = 62*product[3], 76*product[4]
        if g+h <= 0:
            raise ValueError("missing G and H")
        quality.append((100*g/(g+h), 100*sum(product[:3])))
        pressure.append(x[7])
    span = times[-1] - times[0]
    integrals = [sum((b-a)*(v+w)/2 for a, b, v, w in
                     zip(times, times[1:], series, series[1:])) for series in zip(*rates)]
    costs = dict(zip(COST_NAMES, integrals[:4]))
    costs["total"] = sum(costs.values())
    vol = integrals[4]
    primary = time_in_band(times, flags)
    eligible_checks = {
        "closed_loop_trip_ends_run": scenario["controlled"] == scenario["trip"] == "1",
        "full_completed_run": outcome == "completed"
        and math.isclose(times[0], interval-dt, abs_tol=1e-7)
        and math.isclose(times[-1], horizon-dt, abs_tol=1e-7),
        "last_2h_primary_in_band": primary["final_2h_all_in_band"],
        "mean_throughput_at_least_99pct_of_nominal": vol >= .99*22.949*span,
        "quality_and_pressure": all(45 <= g <= 55 and impurity <= 2 for g, impurity in quality)
        and max(pressure) <= 2895,
    }
    return {"scenario": token, "model": model, "outcome": outcome, "samples": len(times),
            "window_h": [times[0], times[-1]], "observed_hours": span,
            "cost_usd": costs, "mean_usd_h": {k:v/span for k,v in costs.items()},
            "product_m3": vol, "usd_per_m3": costs["total"]/vol if vol > 0 else None,
            "primary_band": primary, "temperature_fine_band": time_in_band(times, fine),
            "final_2h_mean": {f"XMEAS({n})": mean(x[n] for t,x in samples if t > times[-1]-2+1e-8)
                              for n in (*BANDS, 17)},
            "temperature_range_c": [min(x[9] for _,x in samples), max(x[9] for _,x in samples)],
            "reactor_pressure_max_kpag": max(pressure),
            "g_mass_pct_of_gh_range": [min(g for g,_ in quality), max(g for g,_ in quality)],
            "max_product_def_mol_pct": max(q for _,q in quality),
            "failed_gates": [k for k,v in eligible_checks.items() if not v]}


def pair(baseline, zoro):
    issues = [*(f"sop:{k}" for k in baseline["failed_gates"]),
              *(f"zoro:{k}" for k in zoro["failed_gates"])]
    for key in ("scenario", "model", "window_h", "samples"):
        if baseline[key] != zoro[key]:
            issues.append(f"mismatch:{key}")
    if abs(baseline["product_m3"] - zoro["product_m3"]) > .01*baseline["product_m3"]:
        issues.append("paired_volume_difference_over_1pct")
    differences = None if issues else {k: v - zoro["cost_usd"][k]
                                       for k,v in baseline["cost_usd"].items()}
    return {"eligible": not issues, "failures": issues, "savings_usd": differences,
            "savings_pct": (100*differences["total"]/baseline["cost_usd"]["total"]
                            if differences and baseline["cost_usd"]["total"] > 0 else None),
            "sop": baseline, "sop_zoro": zoro}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sop_csv", type=Path)
    parser.add_argument("--zoro-csv", type=Path)
    args = parser.parse_args()
    try:
        result = score(args.sop_csv)
        if args.zoro_csv:
            result = pair(result, score(args.zoro_csv))
        print(json.dumps(result, indent=2, allow_nan=False))
    except (KeyError, ValueError, StopIteration, OSError) as error:
        parser.exit(2, f"Studio evaluation failed: {error}\n")
