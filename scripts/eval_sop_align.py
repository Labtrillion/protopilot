#!/usr/bin/env python3
"""Evaluate SOP alignment on the live ProtoPilot Plant (headless) for IDV 0-28.

For each seed and IDV: the fault starts at ONSET_H and the plant runs to END_H.
  * alarm checkpoints : every alarm the Plant raises -> sop_align.align(alarm, checkpoint.history)
                        and the previous text retrieval (zoro-rag BM25 restricted to IDV SOPs, queried
                        with the alarm tag/name/side as the pipeline would send it)
  * probes            : at ONSET_H + PROBES_H, align() on the plant's current history (on-demand query)
Ground truth (the injected IDV) is used only for scoring here; the aligner never reads plant.idv.

usage: python scripts/eval_sop_align.py [--bm25 PATH_TO_VENDORED_ZORO_RAG] [--out FILE]
"""
import argparse
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import sop_align  # noqa: E402
from server.catalog import CHANNELS  # noqa: E402
from server.plant import Plant  # noqa: E402
from server.scenario import DEFAULT_FLAGS, LIVE_FORMAT, STEP_HOURS, live_scenario  # noqa: E402

ONSET_H = 1.0
END_H = 9.0
PROBES_H = (0.5, 1.0, 2.0, 4.0, 8.0)
SEEDS = (1431655765, 20261119)


def bm25_factory(path):
    if not path:
        return None
    sys.path.insert(0, str(path))
    import rag  # zoro-rag from protopilot@zoro-agent, unmodified
    retriever = rag.EvidenceRAG(mode="local")

    def top_idv(alarm):
        name = CHANNELS[alarm["tag"]]["name"]
        query = f"{alarm['tag']} {name} {alarm.get('side', '')} alarm"
        vocab = retriever.index["vocabulary"]
        words = {str(vocab[w]) for w in rag.tokenize(query) if w in vocab}
        best = {}
        for record, vector in zip(retriever.index["records"], retriever.index["sparse"], strict=True):
            sop_id = record.get("source", {}).get("sop_id", "")
            if record["kind"] != "sop_step" or not sop_id.startswith("IDV-"):
                continue
            score = sum(vector.get(w, 0.0) for w in words)
            best[sop_id] = max(best.get(sop_id, 0.0), score)
        ranked = sorted(best, key=lambda k: (-best[k], k))
        return ranked
    return top_idv


def run(seed, idv, bm25, root):
    onset = round(ONSET_H / STEP_HOURS)
    timeline = [] if idv == 0 else [{"step": onset, "op": "disturbance", "id": f"IDV({idv})", "value": 1}]
    plant = Plant(root)
    plant.reset(running=True, scenario=live_scenario({
        "format": LIVE_FORMAT, "step_hours": STEP_HOURS, "seed": seed, "controller": DEFAULT_FLAGS,
        "initial_speed": 60, "through_step": round(END_H / STEP_HOURS), "timeline": timeline}))
    truth = None if idv == 0 else f"IDV-{idv:02d}"
    seen, alarms, probes = set(), [], []
    pending_probes = list(PROBES_H)
    while plant.running:
        plant.tick()
        for key, cp in list(plant.alarm_checkpoints.items()):
            if key in seen:
                continue
            seen.add(key)
            if cp.alarm["kind"] == "trip" or cp.native.time < ONSET_H - 1e-9 and idv:
                continue
            a = sop_align.align(cp.alarm, list(cp.history))
            ranked = [r["protocol_id"] for r in a["ranking"]]
            row = {"t": round(cp.native.time, 3), "tag": cp.alarm["tag"], "side": cp.alarm["side"],
                   "selected": a["idv_selected"], "top3": ranked[:3], "tied": a["idv_tied"],
                   "sensor_sop": a["sensor_sop"], "sensor_gap": a["sensor_gap"]}
            if bm25:
                row["bm25_top3"] = bm25(cp.alarm)[:3]
            alarms.append(row)
        while pending_probes and plant.sim.time >= ONSET_H + pending_probes[0] - 1e-9:
            dt = pending_probes.pop(0)
            a = sop_align.align({"kind": "probe"}, list(plant.history))
            ranked = [r["protocol_id"] for r in a["ranking"]]
            probes.append({"after_onset_h": dt, "selected": a["idv_selected"], "top3": ranked[:3],
                           "tied": a["idv_tied"]})
    return {"seed": seed, "idv": idv, "truth": truth, "end_h": round(plant.sim.time, 3),
            "shutdown": bool(plant.shutdown["terminated"]), "alarms": alarms, "probes": probes}


def summarize(rows):
    s = defaultdict(lambda: defaultdict(int))
    for r in rows:
        truth = r["truth"]
        for a in r["alarms"]:
            k = s["alarm"]
            k["n_fault" if truth else "n_nofault"] += 1
            if truth:
                k["selected_correct"] += a["selected"] == truth
                k["selected_wrong"] += a["selected"] not in (None, truth)
                k["abstained"] += a["selected"] is None
                k["top3"] += truth in a["top3"]
                k["truth_in_tie"] += truth in a["tied"]
                if "bm25_top3" in a:
                    k["bm25_top1"] += a["bm25_top3"][:1] == [truth]
                    k["bm25_top3"] += truth in a["bm25_top3"]
            else:
                k["nofault_selected"] += a["selected"] is not None
            k["sensor_gap"] += a["sensor_gap"] is not None
        for p in r["probes"]:
            k = s[f"probe_{p['after_onset_h']}h"]
            if truth:
                k["n_fault"] += 1
                k["selected_correct"] += p["selected"] == truth
                k["selected_wrong"] += p["selected"] not in (None, truth)
                k["abstained"] += p["selected"] is None
                k["top3"] += truth in p["top3"]
                k["truth_in_tie"] += truth in p["tied"]
            else:
                k["n_nofault"] += 1
                k["nofault_selected"] += p["selected"] is not None
    return {k: dict(v) for k, v in s.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bm25", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "scripts" / "eval_sop_align.json")
    parser.add_argument("--seeds", type=int, nargs="*", default=list(SEEDS))
    parser.add_argument("--idvs", type=int, nargs="*", default=list(range(29)))
    args = parser.parse_args()
    bm25 = bm25_factory(args.bm25)
    rows, wall = [], time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        for seed in args.seeds:
            for idv in args.idvs:
                r = run(seed, idv, bm25, Path(tmp))
                rows.append(r)
                last = r["probes"][-1] if r["probes"] else {}
                print(json.dumps({"seed": seed, "idv": idv, "alarms": len(r["alarms"]),
                                  "first_alarm": r["alarms"][0] if r["alarms"] else None,
                                  "probe_8h": last.get("selected"), "probe_8h_top3": last.get("top3")}), flush=True)
    result = {"onset_h": ONSET_H, "end_h": END_H, "seeds": args.seeds, "summary_by_seed":
              {seed: summarize([r for r in rows if r["seed"] == seed]) for seed in args.seeds},
              "rows": rows, "wall_seconds": round(time.monotonic() - wall, 1),
              "aligner": {k: getattr(sop_align, k) for k in ("PERSIST_HOURS", "WINDOW_HOURS", "HOLD_FRACTION",
                                                             "SPECIFIC_WEIGHT", "LIMIT_WEIGHT", "TYPE_WEIGHT",
                                                             "MECHANISM_WEIGHT")}}
    args.out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result["summary_by_seed"], indent=1))


if __name__ == "__main__":
    main()
