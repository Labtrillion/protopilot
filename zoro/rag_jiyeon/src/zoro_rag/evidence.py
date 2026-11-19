from __future__ import annotations

import hashlib
import re

from zoro_rag.ingestion.metadata import identifiers
from zoro_rag.schemas import EvidenceItem, EvidenceType, RetrievedChunk


def extract_statement(text: str, section: str | None = None, focus: str = "") -> str:
    """Choose a source sentence or table row rather than a heading or print date."""
    if section and text.startswith(section + "\n\n"):
        text = text[len(section) + 2:]
    focus_ids = set(sum(identifiers(focus), []))
    stopwords = {"what", "which", "where", "when", "does", "this", "that", "with", "from", "have", "about"}
    focus_words = set(re.findall(r"[a-z]{4,}", focus.lower())) - stopwords
    candidates = []
    for paragraph in re.split(r"\n\s*\n", text):
        # Preserve table rows; join PDF prose wrapped across physical lines.
        parts = paragraph.splitlines() if "|" in paragraph else [" ".join(paragraph.split())]
        for part in parts:
            sentences = [part.strip()] if "|" in part else re.split(r"(?<=[.!?])\s+(?=[A-Z가-힣])", part.strip())
            for sentence in sentences:
                if not sentence or not re.search(r"[A-Za-z가-힣]", sentence):
                    continue
                if section and sentence == section.split(" > ")[-1]:
                    continue
                if re.fullmatch(r"[\s|:—\-]+", sentence):
                    continue
                if sentence.startswith(("http://", "https://")):
                    continue
                # Data rows and complete prose take precedence over table headings.
                score = 0
                sentence_ids = set(sum(identifiers(sentence), []))
                score += 12 * len(focus_ids & sentence_ids)
                score += 3 * len(focus_words & set(re.findall(r"[a-z]{4,}", sentence.lower())))
                if re.search(r"\b(?:XMEAS|XMV|IDV)[_(\s]*\d+", sentence, re.I):
                    score += 3
                if re.search(r"\b(?:shutdown|interlock|limit|pressure|temperature)\b", sentence, re.I):
                    score += 2
                if re.search(r"\d", sentence):
                    score += 1
                if len(sentence.split()) >= 8:
                    score += 2
                if "|" in sentence and not re.search(r"\d", sentence):
                    score -= 3
                candidates.append((score, sentence))
    statement = max(candidates, key=lambda item: item[0])[1] if candidates else text.strip()
    if len(statement) > 500:
        statement = statement[:500].rsplit(" ", 1)[0]
    return statement


class EvidenceExtractor:
    def extract(self, retrieved: list[RetrievedChunk], focus: str = "") -> list[EvidenceItem]:
        evidence = []
        for item in retrieved:
            chunk = item.chunk
            statement_focus = focus
            if "OPERATING_CONSTRAINTS" in item.intents and EvidenceType.SAFETY in chunk.evidence_types:
                statement_focus += " shutdown interlock limits pressure"
            statement = chunk.procedure.get("instruction_ko") or extract_statement(chunk.text, chunk.section, statement_focus)
            evidence_type = EvidenceType.SAFETY if EvidenceType.SAFETY in chunk.evidence_types else (chunk.evidence_types[0] if chunk.evidence_types else EvidenceType.PROCESS)
            digest = hashlib.sha256((chunk.chunk_id + statement).encode()).hexdigest()[:12].upper()
            evidence.append(EvidenceItem(
                evidence_id=f"EVIDENCE-{digest}",
                evidence_type=evidence_type,
                statement=statement,
                source_document=chunk.document_title,
                source_section=chunk.section,
                source_page=chunk.page,
                source_chunk_id=chunk.chunk_id,
                supporting_quote=chunk.text,
                related_alarm_ids=item.alarm_ids,
                related_variable_ids=sorted(set(chunk.variable_ids)),
                source_url=chunk.source_url,
                source_version=chunk.document_version,
                provenance={"retrieval_intents": item.intents, "rrf_score": item.score, "reranker_score": item.reranker_score,
                            "source_type": chunk.source_type, "procedure": chunk.procedure,
                            "source_file": chunk.source_file, "parsing_warnings": chunk.parsing_warnings,
                            "evidence_types": [t.value for t in chunk.evidence_types], "inferred_metadata": chunk.inferred_metadata},
            ))
        return evidence

