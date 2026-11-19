#!/usr/bin/env python3
"""Build rag/sop-index.json from a Labtrillion/tep-sops checkout.

The index holds, per IDV SOP, its observation predicates with tags rewritten to ProtoPilot's
observed keys, plus the compact Zoro records (idv-sops-zoro/, sensor-sops-zoro/) that the alarm
pipeline sends to Zoro. rag/sop_align.py reads only this file at runtime.

Tag rewriting (tep-sops -> ProtoPilot observed key):
  XMEAS(n)                    -> XMEAS(n)
  additional_measurements[k]  -> XMEASADD(k+1)      (tep_studio legacy_symbol, 0-based k)
  XMV(n)                      -> XMV(n)             (implemented command)
  XMV(n) actual               -> XMV(n):actual      (valve position, plant state[37+n])

usage: python scripts/build_sop_index.py --tep-sops /path/to/tep-sops
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "rag" / "sop-index.json"
ENVELOPE_OPS = {"outside_reference_range", "greater_than_reference_maximum", "less_than_reference_minimum"}
THRESHOLD_OPS = {"gt", "gte", "lt", "lte"}
COMPACT_KEYS = ("id", "revision", "kind", "title", "tags", "side", "severity", "activation_threshold",
                "thresholds", "threshold_unit", "checks", "diagnoses", "candidates", "candidate_basis",
                "source_title", "source_url", "source_sop_id", "raw_tag")


def canonical_tag(tag: str) -> str:
    if m := re.fullmatch(r"XMEAS\((\d+)\)", tag):
        return f"XMEAS({int(m[1])})"
    if m := re.fullmatch(r"additional_measurements\[(\d+)\]", tag):
        return f"XMEASADD({int(m[1]) + 1})"
    if m := re.fullmatch(r"XMV\((\d+)\) actual", tag):
        return f"XMV({int(m[1])}):actual"
    if m := re.fullmatch(r"XMV\((\d+)\)", tag):
        return f"XMV({int(m[1])})"
    raise ValueError(f"unmapped tag {tag!r}")


def conditions(node):
    if isinstance(node, dict):
        if "operator" in node:
            yield node
        for key in ("all", "any"):
            for child in node.get(key, []) if isinstance(node.get(key), list) else []:
                yield from conditions(child)


def convert_condition(c: dict) -> dict:
    op = c["operator"]
    out = {"tag": canonical_tag(c["tag"]), "source_tag": c["tag"], "signal": c.get("signal"),
           "unit": c.get("unit"), "operator": op}
    if op in ENVELOPE_OPS:
        ref = c["reference"]
        out.update(kind="envelope", minimum=float(ref["minimum"]), maximum=float(ref["maximum"]))
    elif op in THRESHOLD_OPS:
        value = c.get("threshold", c.get("value"))
        if value is None:
            raise ValueError(f"threshold missing in {c}")
        out.update(kind="threshold", value=float(value))
    else:
        raise ValueError(f"unsupported operator {op}")
    return out


# [ADDED] Conditions that put an SOP's own written check into executable form. Each entry names the
# check it implements; the source rule's conditions are kept and the added one is ANDed with them.
AUGMENTATIONS = {
    "IDV-20-OBS-01": {
        "implements": "IDV-20 diagnosis check: 'Compare reactor/separator pressures' (reactor-to-separator "
                      "gas-flow relation). The source condition uses separator pressure alone, which moves "
                      "with reactor pressure under most faults.",
        "condition": {"tag": "XMEAS(7)-XMEAS(13)", "source_tag": None, "signal": "reactor_minus_separator_pressure",
                      "unit": "kPa", "operator": "outside_reference_range", "kind": "envelope",
                      "minimum": None, "maximum": None},  # measured by scripts/calibrate_envelopes.py
    },
}


def idv_entry(path: Path, compact: dict) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    rules = []
    for rule in d["observation_rules"]:
        logic = "all" if "all" in rule["if"] else "any"
        converted = [convert_condition(c) for c in conditions(rule["if"])]
        added = AUGMENTATIONS.get(rule["rule_id"])
        if added:
            if logic != "all" and len(converted) > 1:
                raise ValueError(f"augmentation needs an all-rule: {rule['rule_id']}")
            logic = "all"
            converted.append({**added["condition"], "added_by_protopilot": added["implements"]})
        rules.append({"rule_id": rule["rule_id"], "logic": logic, "title": rule.get("title"),
                      "conditions": converted})
    return {"id": compact["id"], "protocol_id": d["protocol_id"], "title": d["name"],
            "disturbance_type": d["disturbance"].get("original_type"),
            "mechanism_tags": sorted({canonical_tag(s["tag"]) for s in d["mechanism"].get("signals", [])
                                      if s.get("tag") and not s["tag"].startswith("state")}),
            "rules": rules, "compact": {k: compact[k] for k in COMPACT_KEYS if k in compact}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tep-sops", type=Path, required=True)
    root = parser.parse_args().tep_sops.resolve()
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                            check=True).stdout.strip()
    idv = []
    for path in sorted((root / "idv-sops").glob("IDV-*.json")):
        compact = json.loads((root / "idv-sops-zoro" / path.name).read_text(encoding="utf-8"))
        idv.append(idv_entry(path, compact))
    sensor = []
    for path in sorted((root / "sensor-sops-zoro").glob("*-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("kind") == "sensor_alarm":
            sensor.append({k: record[k] for k in COMPACT_KEYS if k in record})
    index = {"schema": "protopilot-sop-index-1",
             "source": {"repository": "Labtrillion/tep-sops", "commit": commit},
             "idv": idv, "sensor": sensor}
    OUT.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"idv_sops": len(idv), "sensor_alarm_sops": len(sensor),
                      "conditions": sum(len(r["conditions"]) for e in idv for r in e["rules"]),
                      "commit": commit, "out": str(OUT)}))


if __name__ == "__main__":
    main()
