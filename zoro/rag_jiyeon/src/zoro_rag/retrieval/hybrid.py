from __future__ import annotations

from collections import defaultdict
import re
from qdrant_client import models

from zoro_rag.schemas import RetrievalQuery, RetrievedChunk
from zoro_rag.progress import progress


class HybridRetriever:
    def __init__(self, store, dense_embedder, sparse_embedder, reranker=None, dense_top_k=20,
                 sparse_top_k=20, fused_top_k=15, final_top_k=5, rrf_k=60, safety_coverage=True):
        self.store, self.dense, self.sparse, self.reranker = store, dense_embedder, sparse_embedder, reranker
        self.dense_top_k, self.sparse_top_k = dense_top_k, sparse_top_k
        self.fused_top_k, self.final_top_k, self.rrf_k = fused_top_k, final_top_k, rrf_k
        self.safety_coverage = safety_coverage

    def retrieve(self, queries: list[RetrievalQuery], mode="hybrid_reranker", limit=None) -> list[RetrievedChunk]:
        if not queries:
            return []
        if mode not in {"dense", "bm25", "hybrid", "hybrid_reranker"}:
            raise ValueError(f"Unknown retrieval mode: {mode}")
        scores: dict[str, float] = defaultdict(float)
        records: dict[str, RetrievedChunk] = {}
        safety_ranks = defaultdict(float)
        definitions = []
        procedures = []
        vectors = self.dense.embed([q.query for q in queries]) if mode != "bm25" else [None] * len(queries)
        for query, vector in progress(list(zip(queries, vectors)), desc="Dense + BM25 검색", unit="query"):
            query_filter = None
            if query.intent == "VARIABLE_DEFINITION" and query.variable_ids:
                query_filter = models.Filter(must=[models.FieldCondition(key="variable_ids", match=models.MatchAny(any=query.variable_ids))])
            elif query.intent == "DISTURBANCE_DEFINITION":
                ids = re.findall(r"IDV_\d+", query.query)
                query_filter = models.Filter(must=[models.FieldCondition(key="disturbance_ids", match=models.MatchAny(any=ids))])
            elif query.intent == "OPERATING_CONSTRAINTS":
                query_filter = models.Filter(must=[models.FieldCondition(key="evidence_types", match=models.MatchValue(value="SAFETY"))])
            elif query.intent == "SOP_REVIEW":
                query_filter = models.Filter(must=[
                    models.FieldCondition(key="source_type", match=models.MatchAny(any=["synthetic_demo_sop", "authored_review_protocol"])),
                    models.FieldCondition(key="variable_ids", match=models.MatchAny(any=query.variable_ids)),
                    models.FieldCondition(key="procedure.action.kind", match=models.MatchValue(value="simulation_review")),
                ])
            if query_filter is not None and query.intent in {"VARIABLE_DEFINITION", "DISTURBANCE_DEFINITION", "OPERATING_CONSTRAINTS"}:
                # Authored procedures must not establish benchmark definitions/limits.
                query_filter.must_not = [models.FieldCondition(
                    key="source_type", match=models.MatchAny(any=["synthetic_demo_sop", "authored_review_protocol"]),
                )]
            dense_hits = self.store.search_dense(vector, self.dense_top_k, query_filter) if mode != "bm25" else []
            sparse_hits = self.store.search_sparse(self.sparse.query(query.query), self.sparse_top_k, query_filter) if mode != "dense" else []
            local_scores = defaultdict(float)
            for channel, hits in (("dense", dense_hits), ("sparse", sparse_hits)):
                for rank, (chunk, _) in enumerate(hits, start=1):
                    scores[chunk.chunk_id] += 1.0 / (self.rrf_k + rank)
                    local_scores[chunk.chunk_id] += 1.0 / (self.rrf_k + rank)
                    record = records.setdefault(chunk.chunk_id, RetrievedChunk(chunk=chunk, score=0.0))
                    setattr(record, f"{channel}_rank", min(rank, getattr(record, f"{channel}_rank") or rank))
                    record.intents = sorted(set(record.intents + [query.intent]))
                    record.alarm_ids = sorted(set(record.alarm_ids + query.alarm_ids))
                    if query.intent == "OPERATING_CONSTRAINTS" and re.search(r"(?:shutdown|interlock|limit)[^\n]{0,100}\d", chunk.text, re.I):
                        safety_ranks[chunk.chunk_id] += 1.0 / (self.rrf_k + rank)
            if query.intent in {"VARIABLE_DEFINITION", "DISTURBANCE_DEFINITION"} and local_scores:
                definitions.append(records[max(local_scores, key=local_scores.get)])
            if query.intent == "SOP_REVIEW" and local_scores:
                procedures.append(records[max(local_scores, key=local_scores.get)])
        ordered = sorted(records.values(), key=lambda item: (-scores[item.chunk.chunk_id], item.chunk.chunk_id))
        for item in ordered:
            item.score = scores[item.chunk.chunk_id]
        # Reserve safety evidence from the full union before candidate truncation.
        safety = records[max(safety_ranks, key=safety_ranks.get)] if safety_ranks else None
        reserved = list({item.chunk.chunk_id: item for item in definitions + procedures + ([safety] if self.safety_coverage and safety else [])}.values())
        pool = ordered[: self.fused_top_k]
        for item in reserved:
            if item not in pool:
                pool.append(item)
        if mode == "hybrid_reranker" and self.reranker:
            pool = self.reranker.rerank("\n".join(query.query for query in queries), pool)
        selected = pool[:limit if limit is not None else self.final_top_k]
        # Coverage reservations survive reranking; scores remain model scores.
        for item in reserved[-len(selected):] if selected else []:
            if item not in selected:
                replace = next((i for i in range(len(selected) - 1, -1, -1) if selected[i] not in reserved), None)
                if replace is not None:
                    selected[replace] = item
        return selected
