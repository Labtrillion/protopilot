"""Verify the real Feature 1 retrieval models and save the alarm results."""

import argparse
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zoro_rag import ZoroRAG
from zoro_rag.cli import dump

SCENARIOS = {"tep_studio": "tep_studio", "idv8": "demo_idv8"}
PROBE = "XMEAS(7) reactor pressure"


def summarize_hits(hits):
    if not hits or not all(math.isfinite(score) for _, score in hits):
        raise RuntimeError("Expected scored retrieval hits for the pressure probe.")
    chunk, score = hits[0]
    return {"hits": len(hits), "top_chunk_id": chunk.chunk_id, "top_score": score}


def verify(scenario: str, rebuild: bool) -> None:
    output = ROOT / "artifacts" / "verification" / scenario
    index = output / "index"
    event = json.loads((ROOT / "examples" / scenario / "alarm_event.json").read_text(encoding="utf-8"))
    with ZoroRAG(ROOT / "config" / f"{SCENARIOS[scenario]}.yaml") as rag:
        rag.settings.data["index"].update(
            path=str(index / "qdrant"), manifest=str(index / "manifest.json"),
            chunks=str(index / "chunks.jsonl"), url=None, api_key=None,
        )
        models = rag.settings.get("models")
        if models.get("testing_hash_embeddings") or not models.get("reranker_enabled", True):
            raise RuntimeError("Verification requires real dense embeddings and an enabled reranker.")

        print(f"[{scenario}] Indexing on {models['device']}", file=sys.stderr, flush=True)
        ingest = rag.ingest(rebuild=rebuild)
        dense = rag.dense.embed([PROBE])[0]
        sparse = rag.sparse.query(PROBE)
        if len(dense) != rag.store.dimension or not any(dense) or not sparse[0]:
            raise RuntimeError("The models did not produce usable probe embeddings.")
        dense_search = summarize_hits(rag.store.search_dense(dense, limit=5))
        sparse_search = summarize_hits(rag.store.search_sparse(sparse, limit=5))

        print(f"[{scenario}] Retrieving alarm evidence", file=sys.stderr, flush=True)
        result = rag.process_alarm_event(event)
        scores = [item.provenance.get("reranker_score") for item in result.evidence]
        if not scores or any(score is None or not 0 <= score <= 1 for score in scores):
            raise RuntimeError("Expected a Qwen reranker score on every returned evidence item.")

        result_path = output / "result.json"
        dump(result, str(result_path))
        dump({
            "scenario": scenario,
            "event_id": result.event_id,
            "device": models["device"],
            "index": {key: ingest[key] for key in ("chunk_count", "indexed", "unchanged")},
            "probe": PROBE,
            "dense": {"model": models["dense_model"], "dimension": len(dense), **dense_search},
            "bm25": {"model": models["sparse_model"], "query_terms": len(sparse[0]), **sparse_search},
            "reranker": {"model": models["reranker_model"], "evidence_scores": scores},
            "status": result.status,
            "candidate_actions": [
                {key: action[key] for key in ("procedure_id", "step_id", "status", "recommended_setpoint", "plan")}
                for action in result.candidate_actions
            ],
            "missing_evidence": result.missing_evidence,
            "result_file": str(result_path),
        })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--device", default=os.environ.get("ZORO_DEVICE", "cpu"))
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the verification index.")
    args = parser.parse_args()
    os.environ["ZORO_DEVICE"] = args.device
    os.environ.setdefault("ZORO_MODEL_CACHE", str(ROOT / ".cache" / "hub"))
    os.environ.setdefault("ZORO_SPARSE_CACHE", str(ROOT / ".cache" / "fastembed"))
    os.environ.pop("QDRANT_URL", None)
    os.environ.pop("QDRANT_API_KEY", None)

    for scenario in SCENARIOS if args.scenario == "all" else (args.scenario,):
        verify(scenario, args.rebuild)


if __name__ == "__main__":
    main()
