#!/usr/bin/env python3
"""Re-measure the SOPs' "measured_nominal_envelope" references on the live ProtoPilot Plant.

tep-sops envelopes were measured over 0-2 h of one prefault run in the source research package.
On this Plant, fault-free signals leave those bands for much of a 9 h run (e.g. XMEAS(15) 70 %,
XMEAS(1) 48 %), so they flag faults that are not there. This script applies the same procedure
(min/max of fault-free observations) on this Plant, over CALIBRATION_SEEDS x 0-END_H, and writes
the result into rag/sop-index.json as `live_envelopes`. The calibration seeds are disjoint from
the evaluation seeds in scripts/eval_sop_align.py. The source envelope stays in the index.

usage: python scripts/calibrate_envelopes.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parents[1]))
from server.plant import Plant  # noqa: E402
from setpoint_rag.sop_align import observed_value  # noqa: E402
from server.scenario import DEFAULT_FLAGS, LIVE_FORMAT, STEP_HOURS, live_scenario  # noqa: E402

INDEX = ROOT / "rag" / "sop-index.json"
CALIBRATION_SEEDS = (11, 12, 13)
END_H = 9.0


def main():
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    # [CHANGED v4] also the SOPs' mechanism signals, used by sop_align's mechanism-signature term
    tags = sorted({c["tag"] for e in index["idv"] for r in e["rules"] for c in r["conditions"]
                   if c["kind"] == "envelope"} | {t for e in index["idv"] for t in e["mechanism_tags"]})
    lo, hi = {t: float("inf") for t in tags}, {t: float("-inf") for t in tags}
    with tempfile.TemporaryDirectory() as tmp:
        for seed in CALIBRATION_SEEDS:
            plant = Plant(Path(tmp))
            plant.reset(running=True, scenario=live_scenario({
                "format": LIVE_FORMAT, "step_hours": STEP_HOURS, "seed": seed, "controller": DEFAULT_FLAGS,
                "initial_speed": 60, "through_step": round(END_H / STEP_HOURS), "timeline": []}))
            while plant.running:
                plant.tick()
                observed = plant.history[-1][1]
                for t in tags:
                    v = observed_value(observed, t)  # [CHANGED] also derived tags
                    if v is not None:
                        lo[t], hi[t] = min(lo[t], v), max(hi[t], v)
    index["live_envelopes"] = {
        "procedure": "min/max of fault-free Plant observations (same kind as the SOP's measured_nominal_envelope)",
        "seeds": list(CALIBRATION_SEEDS), "window_h": [0.0, END_H],
        "by_tag": {t: {"minimum": lo[t], "maximum": hi[t]} for t in tags},
    }
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(index["live_envelopes"]["by_tag"], indent=1))


if __name__ == "__main__":
    main()
