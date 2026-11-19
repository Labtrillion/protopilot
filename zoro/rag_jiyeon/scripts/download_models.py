"""Download only the configured inference assets into the shared Linux cache."""
from pathlib import Path
import sys
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from zoro_rag.config import Settings
from zoro_rag.retrieval.embeddings import BM25Embedder
from huggingface_hub import snapshot_download

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "config/settings.yaml"))
    args = parser.parse_args()
    settings = Settings.load(args.config)
    models = settings.get("models")
    names = [models["dense_model"]]
    if models.get("reranker_enabled", True):
        names.append(models["reranker_model"])
    for name in names:
        path = snapshot_download(name, cache_dir=models.get("cache_dir"),
                                 allow_patterns=["*.json", "*.txt", "*.safetensors", "*.model", "pytorch_model.bin", "1_Pooling/*"], max_workers=4)
        print(f"{name}: {path}", flush=True)
    sparse = BM25Embedder(models.get("sparse_model", "Qdrant/bm25"), models.get("sparse_cache_dir"))
    sparse.query("reactor pressure")
    print("BM25 assets ready", flush=True)
