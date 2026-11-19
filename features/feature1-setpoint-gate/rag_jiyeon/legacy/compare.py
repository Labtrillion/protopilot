"""Compare two APMonitor CSV exports under a declared common evaluation contract."""
import argparse
import csv
import json
import math
from pathlib import Path

COST = {"total": "cost_usd_h", "purge": "cost_purge_usd_h",
        "product": "cost_product_usd_h", "power": "cost_power_usd_h",
        "steam": "cost_steam_usd_h"}
RATES = {**COST, "production_m3": "xmeas17_strip_underflow_m3h"}


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite log value")
    return value


def summarize(path, spec):
    bounds = spec["bounds"]
    totals = dict.fromkeys(RATES, 0.0)
    failures = set()
    previous = None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        needed = {"time_h", "shutdown", "model_version", *RATES.values(), *bounds}
        missing = needed.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
        for row in reader:
            time = number(row["time_h"])
            rates = {key: number(row[column]) for key, column in RATES.items()}
            if row["model_version"] != spec["model_version"]:
                raise ValueError(f"{path.name}: model version mismatch")
            if abs(rates["total"] - sum(rates[key] for key in ("purge", "product", "power", "steam"))) > spec["rounding_tolerance_usd_h"]:
                raise ValueError(f"{path.name}: component costs disagree with total at {time} h")
            shutdown = row["shutdown"].strip().lower()
            if shutdown in ("1", "true", "1.0"):
                failures.add("shutdown")
            elif shutdown not in ("0", "false", "0.0"):
                raise ValueError(f"{path.name}: unknown shutdown value")
            for field, (low, high) in bounds.items():
                value = number(row[field])
                if low is not None and value < low or high is not None and value > high:
                    failures.add(f"outside_bounds:{field}")
            if previous is None:
                if not math.isclose(time, spec["start_h"], abs_tol=1e-8, rel_tol=0):
                    raise ValueError(f"{path.name}: evaluation start mismatch")
                start = time
            else:
                prev_time, prev_rates = previous
                dt = time - prev_time
                if dt <= 0:
                    raise ValueError(f"{path.name}: non-increasing time")
                if dt > spec["max_gap_h"] + 1e-8:
                    failures.add("sampling_gap")
                for key in totals:
                    totals[key] += dt * (prev_rates[key] + rates[key]) / 2
            previous = time, rates
    if previous is None or previous[0] <= start:
        raise ValueError(f"{path.name}: need at least two samples")
    end = previous[0]
    if not math.isclose(end, spec["end_h"], abs_tol=1e-8, rel_tol=0):
        failures.add("horizon_mismatch")
    volume = totals.pop("production_m3")
    if volume < spec["min_production_m3"]:
        failures.add("production_shortfall")
    return {"eligible": not failures, "failures": sorted(failures), "observed_hours": end-start,
            "cost_usd": totals, "production_m3": volume,
            "usd_per_m3": totals["total"]/volume if volume > 0 else None}


def compare(manifest):
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    spec = data["contract"]
    for key in ("pair_id", "initial_state", "disturbance_trace", "sop_version", "model_version",
                "allowed_actions", "targets"):
        if not isinstance(spec[key], str) or not spec[key].strip():
            raise ValueError(f"Missing pairing declaration: {key}")
    if type(spec["seed"]) is not int:
        raise ValueError("Supply the shared integer seed")
    for key in ("start_h", "end_h", "min_production_m3", "max_gap_h", "rounding_tolerance_usd_h"):
        spec[key] = number(spec[key])
    if (spec["end_h"] <= spec["start_h"] or spec["min_production_m3"] <= 0
            or spec["max_gap_h"] <= 0 or spec["rounding_tolerance_usd_h"] < 0):
        raise ValueError("Invalid horizon, throughput, gap or rounding tolerance")
    bounds = spec["bounds"]
    if not isinstance(bounds, dict) or "g_mass_pct" not in bounds or "xmeas07_reactor_press_kpag" not in bounds:
        raise ValueError("Declare quality and pressure bounds for this pair")
    for key, limits in bounds.items():
        if not isinstance(limits, list) or len(limits) != 2 or limits == [None, None]:
            raise ValueError(f"Invalid bounds for {key}")
        low, high = [None if v is None else number(v) for v in limits]
        if low is not None and high is not None and low > high:
            raise ValueError(f"Reversed bounds for {key}")
        bounds[key] = [low, high]
    baseline = summarize(manifest.parent/data["sop_only_csv"], spec)
    zoro = summarize(manifest.parent/data["sop_zoro_csv"], spec)
    result = {"pair_id": spec["pair_id"], "sop_only": baseline, "sop_zoro": zoro,
              "status": "not_comparable", "savings_usd": None, "savings_pct": None}
    if baseline["eligible"] and zoro["eligible"]:
        delta = {key: baseline["cost_usd"][key] - zoro["cost_usd"][key] for key in COST}
        saved = delta["total"]
        result.update(status="comparable", savings_usd=saved, component_difference_usd=delta,
                      savings_pct=100 * saved / baseline["cost_usd"]["total"] if baseline["cost_usd"]["total"] > 0 else None)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    try:
        print(json.dumps(compare(parser.parse_args().manifest), indent=2, allow_nan=False))
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.exit(2, f"Comparison failed: {error}\n")

