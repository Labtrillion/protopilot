"""Look up the curated demo SOP for a console alarm."""

import json
from pathlib import Path

SOPS = json.loads(Path(__file__).with_name("demo-sops.json").read_text(encoding="utf-8"))


def find_sop(alarm):
    """Curated demo SOP for the alarm tag (fallback when tep-sops has no aligned SOP)."""
    if alarm["kind"] == "trip":
        return next(sop for sop in SOPS if sop.get("kind") == "trip")
    return next((sop for sop in SOPS if alarm["tag"] in sop.get("tags", ())
                 and ("side" not in sop or sop["side"] == alarm.get("side"))), None)
