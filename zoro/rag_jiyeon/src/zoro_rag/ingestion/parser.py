from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

from zoro_rag.schemas import DocumentBlock


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def clean_pdf_page(text: str) -> str:
    """Remove browser print furniture, retaining the original body and line order."""
    lines = text.splitlines()
    kept = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        # Browser print dates, sometimes followed by the document title.
        browser_date = re.match(
            r"^\d{2,4}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*(?:오전|오후|AM|PM)?\s*\d{1,2}:\d{2}",
            stripped, re.I,
        )
        browser_footer = re.fullmatch(r"https?://\S+\s+\d+\s*/\s*\d+", stripped)
        margin = index < 3 or index >= len(lines) - 3
        page_number = margin and re.fullmatch(r"(?:Page\s+\d+|\d+\s*/\s*\d+)", stripped, re.I)
        if browser_date or browser_footer or page_number:
            continue
        kept.append(line)
    return _clean("\n".join(kept))


def parse_markdown(path: Path, relative: str) -> list[DocumentBlock]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    headings: list[str] = []
    blocks: list[DocumentBlock] = []
    buffer: list[str] = []

    def flush(block_type: str = "paragraph") -> None:
        text = _clean("\n".join(buffer))
        if text:
            blocks.append(DocumentBlock(block_type=block_type, text=text, heading_path=headings.copy(), source_file=relative))
        buffer.clear()

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush()
            level = len(heading.group(1))
            headings[:] = headings[: level - 1]
            headings.append(heading.group(2).strip())
            blocks.append(DocumentBlock(block_type="heading", text=headings[-1], heading_path=headings.copy(), source_file=relative))
        elif not line.strip():
            flush("table" if buffer and any("|" in item for item in buffer) else "paragraph")
        else:
            buffer.append(line)
    flush("table" if buffer and any("|" in item for item in buffer) else "paragraph")
    return blocks


def parse_html(path: Path, relative: str) -> list[DocumentBlock]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    canonical = soup.find("link", rel="canonical")
    source_url = canonical.get("href") if canonical else None
    content = soup.find("main") or soup
    headings: list[str] = []
    blocks: list[DocumentBlock] = []
    for node in content.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
        if node.find_parent("table") and node.name != "table":
            continue
        if node.name == "li" and node.find(["p", "li"]):
            continue
        text = _clean(node.get_text(" ", strip=True))
        if not text:
            continue
        if node.name.startswith("h"):
            level = int(node.name[1])
            headings[:] = headings[: level - 1]
            headings.append(text)
            block_type = "heading"
        elif node.name == "table":
            rows = []
            for row in node.find_all("tr"):
                cells = [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
                if cells:
                    rows.append(" | ".join(cells))
            text, block_type = "\n".join(rows), "table"
        else:
            block_type = "paragraph"
        blocks.append(DocumentBlock(block_type=block_type, text=text, heading_path=headings.copy(), source_file=relative, metadata={"source_url": source_url}))
    return blocks


def parse_json(path: Path, relative: str) -> list[DocumentBlock]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and data.get("schema_version") == "tep-appendix-d-test-protocol-1":
        return parse_protocol(data, relative)
    records = data if isinstance(data, list) else [data]
    blocks = []
    for index, record in enumerate(records):
        identity = record.get("id", index) if isinstance(record, dict) else index
        text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        # This local catalog defines numeric IDs as IDV entries; keep the original JSON text.
        title = f"IDV_{identity}" if path.stem == "idv_catalog" else f"Record {identity}"
        blocks.append(DocumentBlock(block_type="record", text=text, heading_path=[title], source_file=relative, metadata={"record_id": identity}))
    return blocks


def parse_protocol(data: dict, relative: str) -> list[DocumentBlock]:
    """Index protocol steps separately; never embed the entire raw event archive."""
    document = data["document_id"]
    scope = data.get("scope", {})
    synthetic = scope.get("source_event_kind") == "synthetic_demo_fixture"
    source_type = "synthetic_demo_sop" if synthetic else "authored_review_protocol"
    applicability = data.get("applicability", {})
    context = json.dumps({
        "document_id": document, "source_type": source_type,
        "applicability": applicability, "summary": data.get("summary", {}),
    }, ensure_ascii=False)
    blocks = [DocumentBlock(
        block_type="paragraph", text=context, source_file=relative,
        heading_path=[document, "summary"], metadata={"source_type": source_type},
    )]
    for step in data.get("protocol", {}).get("steps", []):
        # One step retains its transitions, provenance references and authored action.
        text = json.dumps({"applicability": applicability, "step": step}, ensure_ascii=False, indent=2)
        blocks.append(DocumentBlock(
            block_type="record", text=text, source_file=relative,
            heading_path=[document, "protocol", step["id"]],
            metadata={"source_type": source_type, "procedure": {
                "document_id": document, "document_version": data.get("document_version"), "step_id": step["id"],
                "instruction_ko": step.get("instruction_ko"),
                "actor": step.get("actor"), "transitions": step.get("transitions", []),
                "source_refs": step.get("source_refs", []),
                "action": step.get("action"),
            }},
        ))
    return blocks


def parse_pdf(path: Path, relative: str) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    warnings = []
    try:
        reader = PdfReader(str(path))
        pages = [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]
    except Exception as exc:
        # Recover readable text in memory; never rewrite the original document.
        import pymupdf
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        with pymupdf.open(path) as document:
            pages = [(i, page.get_text(sort=True)) for i, page in enumerate(document, 1)]
        warnings.append(f"Damaged PDF recovered in memory with PyMuPDF after {type(exc).__name__}; completeness is unverified")
    empty_pages = []
    for page_number, raw in pages:
        text = clean_pdf_page(raw)
        if not text:
            empty_pages.append(page_number)
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        footer_url = re.search(r"(https?://\S+)\s+\d+\s*/\s*\d+", raw)
        for paragraph in paragraphs:
            blocks.append(DocumentBlock(
                block_type="paragraph", text=paragraph, heading_path=[], source_file=relative,
                page=page_number, metadata={"source_url": footer_url.group(1) if footer_url else None},
            ))
    if empty_pages:
        warnings.append(f"Pages {empty_pages} contain no extractable text; original replacement/OCR/manual review required")
    if not blocks:
        raise ValueError("PDF has no readable text")
    for block in blocks:
        block.metadata["parsing_warnings"] = warnings
    return blocks


PARSERS = {".md": parse_markdown, ".html": parse_html, ".htm": parse_html, ".json": parse_json, ".pdf": parse_pdf}

