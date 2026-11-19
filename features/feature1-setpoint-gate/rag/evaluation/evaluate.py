from __future__ import annotations

import json
from pathlib import Path

from zoro_rag.schemas import AlarmEvent


def matches(hit, label):
    return (label.get("source_contains", "").lower() in hit.chunk.source_file.lower()
            and all(term.lower() in hit.chunk.text.lower() for term in label.get("text_contains", [])))


def evaluate_cases(rag, path: Path):
    cases = json.loads(path.read_text(encoding="utf-8"))
    corpus_path = rag.settings.path("index", "chunks")
    corpus = {item["chunk_id"]: item["text"] for line in corpus_path.read_text(encoding="utf-8").splitlines()
              if (item := json.loads(line))} if corpus_path.exists() else {}
    results = {}
    for mode in ("dense", "bm25", "hybrid", "hybrid_reranker"):
        if mode == "hybrid_reranker" and rag.reranker is None:
            results[mode] = {"status": "disabled"}
            continue
        rows = []
        for case in cases:
            event = AlarmEvent.model_validate(case["event"])
            context = rag.context_builder.build(event, rag.normalizer.normalize(event))
            hits = rag.retriever.retrieve(rag.planner.plan(context), mode=mode, limit=10)
            labels = case.get("expected_evidence", [])
            ranks = [next((rank for rank, hit in enumerate(hits, 1) if matches(hit, label)), None) for label in labels]
            safety_ranks = [rank for rank, label in zip(ranks, labels) if label.get("evidence_type") == "SAFETY"]
            found = [rank for rank in ranks if rank is not None and rank <= 10]
            citations = rag.extractor.extract(hits)
            citation_accuracy = sum(e.supporting_quote in corpus.get(e.source_chunk_id, "") and e.source_chunk_id in corpus for e in citations) / len(citations) if citations else None
            categories = {t.value for hit in hits for t in hit.chunk.evidence_types}
            expected_categories = set(case.get("expected_evidence_types", []))
            metrics = {
                "evidence_recall_at_5": sum(r is not None and r <= 5 for r in ranks) / len(labels) if labels else None,
                "evidence_recall_at_10": len(found) / len(labels) if labels else None,
                "mrr_at_10": 1 / min(found) if found else 0.0,
                "source_citation_accuracy": citation_accuracy,
                "safety_constraint_recall": sum(r is not None and r <= 10 for r in safety_ranks) / len(safety_ranks) if safety_ranks else None,
                "evidence_coverage": len(expected_categories & categories) / len(expected_categories) if expected_categories else None,
            }
            rows.append({"event_id": event.event_id, "label_status": case["label_status"],
                         "retrieved": [hit.chunk.chunk_id for hit in hits], "expected_evidence_ranks": ranks, "metrics": metrics})
        averages = {}
        for key in rows[0]["metrics"] if rows else []:
            values = [row["metrics"][key] for row in rows if row["metrics"][key] is not None]
            averages[key] = sum(values) / len(values) if values else None
        results[mode] = {"status": "evaluated", "metrics": averages, "cases": rows}
    return {"warning": "Draft labels, manually unverified. Citation accuracy checks passage provenance, not claim truth. Category coverage uses inferred metadata.",
            "modes": results}
