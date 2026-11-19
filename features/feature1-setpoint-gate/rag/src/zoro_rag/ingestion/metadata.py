from __future__ import annotations

import re
from pathlib import Path

from zoro_rag.schemas import EvidenceType

ID_PATTERN = re.compile(r"\b(IDV|XMEAS|XMV)\s*[_ (]?\s*(\d+)\s*\)?", re.I)
EQUIPMENT = ("reactor", "separator", "stripper", "condenser", "compressor", "purge", "feed", "recycle")


def normalize_identifier(value: str | None) -> str | None:
    if not value:
        return None
    match = ID_PATTERN.search(value)
    return f"{match.group(1).upper()}_{int(match.group(2))}" if match else value.strip().upper()


def identifiers(text: str) -> tuple[list[str], list[str]]:
    ids = {f"{kind.upper()}_{int(number)}" for kind, number in ID_PATTERN.findall(text)}
    return sorted(x for x in ids if x.startswith("IDV_")), sorted(x for x in ids if not x.startswith("IDV_"))


def infer_metadata(text: str, source: str) -> tuple[list[str], list[EvidenceType], list[str]]:
    lowered = text.lower()
    equipment = sorted({item for item in EQUIPMENT if item in lowered})
    types: set[EvidenceType] = set()
    if "idv" in lowered or "disturbance" in lowered:
        types.add(EvidenceType.DISTURBANCE)
    if "xmeas" in lowered or "measurement" in lowered:
        types.add(EvidenceType.MEASUREMENT)
    if "xmv" in lowered or "controller" in lowered or "control" in lowered:
        types.add(EvidenceType.CONTROL)
    if any(term in lowered for term in ("alarm", "shutdown", "constraint", "limit", "safety", "override")):
        types.add(EvidenceType.SAFETY)
    if any(term in lowered for term in ("cost", "economic", "profit")):
        types.add(EvidenceType.ECONOMIC)
    if any(term in lowered for term in ("case ", "experiment", "scenario")):
        types.add(EvidenceType.CASE_STUDY)
    if not types:
        types.add(EvidenceType.PROCESS)
    inferred = ["equipment", "evidence_types"]
    return equipment, sorted(types, key=lambda x: x.value), inferred


def document_id(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-")

