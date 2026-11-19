"""Curated RAG evaluation guidance for the live setpoint workflow."""

import json
from importlib.resources import files


def evaluation_policy():
    root = files(__package__)
    policy = json.loads(root.joinpath("evaluation-policy.json").read_text(encoding="utf-8"))
    economics = root.joinpath("legacy/Zoro-RAG-Knowledge/economics.json")
    records = json.loads(economics.read_text(encoding="utf-8"))
    policy["evidence"] = [
        {"id": rule["id"], "text": rule["text"]}
        for rule in records if rule["id"] in policy["sources"]["economics"]
    ]
    return policy
