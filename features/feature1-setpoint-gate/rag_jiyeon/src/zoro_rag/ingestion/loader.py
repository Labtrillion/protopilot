from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zoro_rag.ingestion.parser import PARSERS
from zoro_rag.progress import progress
from zoro_rag.schemas import DocumentBlock


def load_documents(root: Path) -> tuple[list[DocumentBlock], list[dict[str, Any]]]:
    blocks: list[DocumentBlock] = []
    reports: list[dict[str, Any]] = []
    paths = sorted(item for item in root.rglob("*") if item.is_file())
    for path in progress(paths, desc="문서 읽기", unit="file"):
        parser = PARSERS.get(path.suffix.lower())
        relative = path.relative_to(root).as_posix()
        if not parser:
            reports.append({"file": relative, "status": "unsupported", "detail": path.suffix})
            continue
        try:
            parsed = parser(path, relative)
            version = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            for block in parsed:
                block.metadata["document_version"] = version
            blocks.extend(parsed)
            warnings = sorted({warning for block in parsed for warning in block.metadata.get("parsing_warnings", [])})
            reports.append({"file": relative, "status": "partial" if warnings else "parsed", "detail": f"{len(parsed)} blocks", "warnings": warnings})
        except Exception as exc:
            reports.append({"file": relative, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
    return blocks, reports

