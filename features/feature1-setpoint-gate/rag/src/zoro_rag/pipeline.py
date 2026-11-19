from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from functools import cached_property
import os
from typing import Any

from zoro_rag.alarm import AlarmContextBuilder, AlarmNormalizer, RetrievalPlanner
from zoro_rag.config import Settings
from zoro_rag.progress import progress
from zoro_rag.evidence import EvidenceExtractor
from zoro_rag.ingestion import StructureAwareChunker, load_documents
from zoro_rag.retrieval.embeddings import BM25Embedder, DenseEmbedder
from zoro_rag.retrieval.hybrid import HybridRetriever
from zoro_rag.retrieval.reranker import Qwen3Reranker
from zoro_rag.retrieval.vectorstore import QdrantStore
from zoro_rag.schemas import AlarmEvent, AlarmResult, Chunk, RetrievalQuery


def sop_plan_match(chunk: Chunk, alarm_tags: set[str], simulator: str | None):
    """SOP 단계가 이 경보의 시뮬레이션 검토 후보인가. (action, 일치 태그, 상태) 또는 None.

    경보 태그가 단계의 trigger_variable_ids 와 겹칠 때만 후보다. 외란 번호·입력 출처로 거르지 않는다.
    """
    action = chunk.procedure.get("action")
    if not (action and action.get("kind") == "simulation_review"):
        return None
    triggers = set(action.get("trigger_variable_ids") or chunk.variable_ids)
    matched = sorted(alarm_tags & triggers)
    if not matched:
        return None
    plan_simulator = action["plan"].get("simulator")
    status = "simulator_mismatch" if simulator and plan_simulator and plan_simulator != simulator else "awaiting_simulation"
    return action, matched, status


class ZoroRAG:
    def __init__(self, config_path: str | Path = "config/settings.yaml"):
        self.settings = Settings.load(config_path)
        self.knowledge_root = self.settings.path("knowledge_base")
        self.normalizer = AlarmNormalizer(self.knowledge_root, self.settings.get("alarm", "stale_after_seconds", default=300))
        self.context_builder = AlarmContextBuilder(
            stale_after_seconds=self.normalizer.stale_after_seconds,
            use_simulation_disturbance_id=bool(self.settings.get("alarm", "use_simulation_disturbance_id", default=False)),
        )
        self.simulator = self.settings.get("simulator")
        self.planner = RetrievalPlanner(include_sop_review=bool(self.settings.get("additional_knowledge")))
        self.extractor = EvidenceExtractor()

    @cached_property
    def dense(self):
        m = self.settings.get("models")
        return DenseEmbedder(m["dense_model"], m.get("device", "auto"), m.get("batch_size", 16),
                             m.get("cache_dir"), m.get("testing_hash_embeddings", False), m.get("max_length", 2048))

    @cached_property
    def sparse(self):
        m = self.settings.get("models")
        return BM25Embedder(m.get("sparse_model", "Qdrant/bm25"), m.get("sparse_cache_dir"))

    @cached_property
    def store(self):
        index = self.settings.get("index")
        dimension = 384 if self.settings.get("models", "testing_hash_embeddings", default=False) else 1024
        store = QdrantStore(self.settings.path("index", "path"), index["collection"], dimension,
                            os.environ.get("QDRANT_URL") or index.get("url"),
                            os.environ.get("QDRANT_API_KEY") or index.get("api_key"))
        store.ensure_collection()
        return store

    @cached_property
    def reranker(self):
        m = self.settings.get("models")
        if not m.get("reranker_enabled", True):
            return None
        return Qwen3Reranker(m["reranker_model"], m.get("device", "auto"), m.get("cache_dir"),
                             m.get("reranker_batch_size", 4), m.get("reranker_max_length", 2048))

    @cached_property
    def retriever(self):
        retrieval = self.settings.get("retrieval", default={})
        return HybridRetriever(self.store, self.dense, self.sparse, self.reranker,
                               **{key: retrieval[key] for key in ("dense_top_k", "sparse_top_k", "fused_top_k", "final_top_k", "rrf_k", "safety_coverage") if key in retrieval})

    def close(self):
        if "store" in self.__dict__:
            self.store.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def index_signature(self):
        m = self.settings.get("models")
        return {"schema": 5, "dense_model": m["dense_model"], "testing_hash": m.get("testing_hash_embeddings", False),
                "max_length": m.get("max_length", 2048), "sparse_model": m.get("sparse_model", "Qdrant/bm25"),
                "bm25_idf": True, "technical_normalization": 1,
                **({"additional_knowledge": self.settings.get("additional_knowledge")}
                   if self.settings.get("additional_knowledge") else {})}

    def _build_chunks(self):
        blocks, reports = load_documents(self.knowledge_root)
        for directory in self.settings.get("additional_knowledge", default=[]):
            root = Path(directory)
            if not root.is_absolute():
                root = self.settings.root / root
            if not root.is_dir():
                raise FileNotFoundError(f"Additional knowledge directory does not exist: {root}")
            extra_blocks, extra_reports = load_documents(root)
            prefix = f"additional/{root.relative_to(self.settings.root).as_posix()}" if root.is_relative_to(self.settings.root) else f"additional/{root.name}"
            for block in extra_blocks:
                block.source_file = f"{prefix}/{block.source_file}"
            for report in extra_reports:
                report["file"] = f"{prefix}/{report['file']}"
            blocks.extend(extra_blocks)
            reports.extend(extra_reports)
        cfg = self.settings.get("chunking", default={})
        failures = [r for r in reports if r["status"] == "failed"]
        if failures or not blocks:
            raise RuntimeError(f"Knowledge parsing incomplete: {failures or 'no documents found'}")
        chunks = StructureAwareChunker(**cfg).chunk(blocks)
        return chunks, reports

    def ingest(self, rebuild: bool = False) -> dict[str, Any]:
        # Parse before touching the live collection so unreadable inputs do not erase it.
        chunks, reports = self._build_chunks()
        if rebuild:
            # Fail on missing model weights before clearing an existing index.
            _ = self.dense, self.sparse
            self.store.ensure_collection(recreate=True)
            self.settings.path("index", "manifest").unlink(missing_ok=True)
        manifest_path = self.settings.path("index", "manifest")
        chunk_path = self.settings.path("index", "chunks")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"chunks": []}
        if previous.get("signature") != self.index_signature() and previous.get("chunks"):
            raise RuntimeError("Index model/schema changed. Run ingest --rebuild to explicitly rebuild this index.")
        old_ids = set(previous.get("chunks", [])); new_ids = {chunk.chunk_id for chunk in chunks}
        self.store.delete_chunks(sorted(old_ids - new_ids))
        existing = set(self.store.existing_chunk_ids(sorted(old_ids & new_ids)))
        changed = [chunk for chunk in chunks if chunk.chunk_id not in existing]
        for start in progress(range(0, len(changed), 64), desc="문서 임베딩·저장", unit="batch"):
            batch = changed[start:start + 64]
            self.store.upsert(batch, self.dense.embed([item.text for item in batch]), self.sparse.embed([item.text for item in batch]))
        with chunk_path.open("w", encoding="utf-8") as handle:
            for chunk in chunks:
                handle.write(chunk.model_dump_json() + "\n")
        manifest_path.write_text(json.dumps({"chunks": sorted(new_ids), "signature": self.index_signature()}, indent=2), encoding="utf-8")
        return {"documents": reports, "chunk_count": len(chunks), "indexed": len(changed), "unchanged": len(existing), "removed": len(old_ids - new_ids)}

    def inspect_chunks(self, output: str | Path = ".zoro_index/chunk_preview.csv") -> dict[str, Any]:
        chunks, reports = self._build_chunks()
        path = Path(output)
        if not path.is_absolute(): path = self.settings.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["chunk_id", "document", "section", "page", "token_count", "evidence_categories", "variables", "preview"])
            writer.writeheader()
            for chunk in chunks:
                writer.writerow({"chunk_id": chunk.chunk_id, "document": chunk.source_file, "section": chunk.section, "page": chunk.page, "token_count": chunk.token_count, "evidence_categories": ",".join(x.value for x in chunk.evidence_types), "variables": ",".join(chunk.variable_ids + chunk.disturbance_ids), "preview": chunk.text[:500]})
        counts: dict[str, int] = {}
        for chunk in chunks: counts[chunk.source_file] = counts.get(chunk.source_file, 0) + 1
        sizes = sorted(chunk.token_count for chunk in chunks)
        stats = {"chunks": len(chunks), "by_document": counts, "tokens": {"min": sizes[0] if sizes else 0, "median": sizes[len(sizes)//2] if sizes else 0, "max": sizes[-1] if sizes else 0}, "output": str(path), "parsing": reports}
        return stats

    def process_alarm_event(self, event: dict[str, Any] | AlarmEvent) -> AlarmResult:
        validated = event if isinstance(event, AlarmEvent) else AlarmEvent.model_validate(event)
        if self.settings.path("index", "manifest").exists():
            self.check_index()
        normalized = self.normalizer.normalize(validated)
        context = self.context_builder.build(validated, normalized)
        for disturbance in context.known_disturbances:
            if disturbance not in self.normalizer.disturbances:
                context.missing_information.append(f"Reported simulation disturbance {disturbance} has no local catalog definition.")
        queries = self.planner.plan(context)
        # Generic query templates cannot establish relevance for wholly unknown signals.
        grounded = bool(context.affected_equipment or context.known_disturbances or any(a.variable_definition for a in normalized))
        retrieved = self.retriever.retrieve(queries) if grounded and self.store.count() else []
        focus = " ".join(
            value for alarm in normalized
            for value in (alarm.normalized_tag, alarm.variable_definition) if value
        )
        evidence = self.extractor.extract(retrieved, focus=focus)
        actions = []
        alarm_tags = {a.normalized_tag for a in normalized if a.normalized_tag}
        # SOP 단계가 주는 것은 시뮬레이션 검토 계획이며 제어 명령이 아니다.
        # 경보 태그가 단계의 대상 변수와 겹칠 때만 후보로 낸다. 외란 번호·입력 출처로 거르지 않는다.
        for hit, item in zip(retrieved, evidence):
            procedure = hit.chunk.procedure
            match = sop_plan_match(hit.chunk, alarm_tags, self.simulator)
            if match is None:
                continue
            action, matched, status = match
            actions.append({
                "kind": "simulation_review", "status": status,
                "source_type": hit.chunk.source_type,
                "procedure_id": procedure["document_id"], "step_id": procedure["step_id"],
                "source_refs": procedure.get("source_refs", []),
                "source_chunk_id": hit.chunk.chunk_id, "evidence_id": item.evidence_id,
                "matched_alarm_tags": matched,
                "plan": action["plan"], "recommended_setpoint": None,
                "snapshot_id": context.snapshot_id,
                "active_objective": context.active_objective.model_dump(mode="json") if context.active_objective else None,
                "simulation_required": True, "control_action_authorized": False,
            })
        missing = context.missing_information.copy()
        for action in actions:
            review = context.missing_information.copy()
            if not context.snapshot_id:
                review.append("No simulator snapshot_id supplied; replay state has not been identified.")
            objective = context.active_objective
            expected = action["plan"].get("objective")
            if objective is None:
                review.append("No active objective supplied; confirm the current optimization goal.")
            elif not expected or (objective.metric, objective.direction) != (expected.get("metric"), expected.get("direction")):
                review.append("The active objective does not match a verified objective in the SOP plan; review the proposed comparison.")
            if objective is not None:
                if expected and expected.get("unit") and objective.unit != expected["unit"]:
                    review.append("The active objective unit does not match the SOP plan objective unit.")
                for name, value in objective.constraints.items():
                    if name not in action["plan"].get("constraints", {}) or action["plan"]["constraints"][name] != value:
                        review.append(f"Active objective constraint {name} is not matched by the SOP plan; review is required.")
            if action["status"] == "simulator_mismatch":
                review.append(f"SOP plan targets simulator {action['plan'].get('simulator')}, configured simulator is {self.simulator}.")
            elif review:
                action["status"] = "input_review_required"
            action["input_review_reasons"] = review
            missing.extend(message for message in review if message not in missing)
        if not any(item.evidence_type.value == "SAFETY" and item.provenance.get("source_type") not in ("synthetic_demo_sop", "authored_review_protocol") for item in evidence):
            missing.append("No relevant safety constraint was retrieved; process safety has not been established.")
        if actions:
            missing.append("Candidate simulations have not been executed; no optimal setpoint is established.")
            if any(a["plan"].get("constraints", {}).get("production_and_quality_tolerances") is None for a in actions):
                missing.append("Production and product quality tolerances must be supplied before selecting a feasible setpoint.")
        missing.extend(sorted({warning for item in evidence for warning in item.provenance.get("parsing_warnings", [])}))
        return AlarmResult(
            event_id=validated.event_id,
            status="evidence_retrieved" if evidence else "insufficient_evidence",
            alarm_context=context,
            evidence=evidence,
            candidate_actions=actions,
            missing_evidence=missing + ([] if evidence else ["No relevant source passage was retrieved."]),
            unresolved_questions=["The alarm does not establish a root cause; candidate causes require downstream validation."],
        )

    def check_index(self):
        manifest_path = self.settings.path("index", "manifest")
        if not manifest_path.exists():
            raise RuntimeError("검색 인덱스가 없습니다. 먼저 python main.py ingest 를 실행하세요.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("signature") != self.index_signature():
            raise RuntimeError("인덱스 형식이 바뀌었습니다. python main.py ingest --rebuild 를 실행하세요.")
        if not self.store.count():
            raise RuntimeError("검색 인덱스가 비어 있습니다. python main.py ingest 를 실행하세요.")

    def query(self, question: str, limit: int | None = None):
        question = question.strip()
        if not question:
            raise ValueError("검색 질문을 입력하세요.")
        if limit is not None and not 1 <= limit <= self.settings.get("retrieval", "fused_top_k", default=15):
            raise ValueError("검색 개수는 1부터 fused_top_k 설정값 사이여야 합니다.")
        self.check_index()
        query = RetrievalQuery(intent="MANUAL_DIAGNOSTIC", query=question)
        return self.retriever.retrieve([query], limit=limit)

    def stats(self) -> dict[str, Any]:
        return {"collection": self.settings.get("index", "collection"), "points": self.store.count(), "dense_model": self.settings.get("models", "dense_model"), "dense_dimension": self.store.dimension}

