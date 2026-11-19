from __future__ import annotations

import hashlib
import re
from typing import Iterable

import numpy as np


class DenseEmbedder:
    """BGE-M3 dense embeddings; hash mode is explicit and only intended for tests."""

    def __init__(self, model_name: str, device: str = "auto", batch_size: int = 16, cache_dir: str | None = None, testing_hash: bool = False, max_length: int = 2048):
        self.model_name, self.batch_size, self.testing_hash = model_name, batch_size, testing_hash
        self.dimension = 384 if testing_hash else 1024
        self.model = None
        if not testing_hash:
            try:
                from sentence_transformers import SentenceTransformer
                import torch
            except ImportError as exc:
                raise RuntimeError("BGE-M3 requires sentence-transformers and torch; run bash scripts/setup_linux.sh.") from exc
            selected_device = ("cuda:0" if torch.cuda.is_available() else "cpu") if device == "auto" else device
            torch.set_num_threads(4)
            try:
                self.model = SentenceTransformer(model_name, device=selected_device, cache_folder=cache_dir)
                self.model.max_seq_length = max_length
                if selected_device.startswith("cuda"):
                    self.model.half()
                self.dimension = self.model.get_sentence_embedding_dimension()
            except Exception as exc:
                raise RuntimeError(f"Unable to load dense model {model_name} on {selected_device}: {exc}") from exc

    def _hash(self, text: str) -> list[float]:
        vector = np.zeros(self.dimension, dtype=np.float32)
        for token in re.findall(r"[A-Za-z]+_?\d*|\d+(?:\.\d+)?", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += -1.0 if digest[4] & 1 else 1.0
        norm = float(np.linalg.norm(vector)) or 1.0
        return (vector / norm).tolist()

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        values = list(texts)
        if not values:
            return []
        if self.testing_hash:
            return [self._hash(text) for text in values]
        dense = np.asarray(self.model.encode(values, batch_size=self.batch_size, normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)
        dense /= np.maximum(np.linalg.norm(dense, axis=1, keepdims=True), 1e-12)
        return dense.tolist()


class BM25Embedder:
    def __init__(self, model_name: str = "Qdrant/bm25", cache_dir: str | None = None):
        try:
            from fastembed import SparseTextEmbedding
        except ImportError as exc:
            raise RuntimeError("BM25 requires the fastembed package") from exc
        self.model = SparseTextEmbedding(model_name=model_name, cache_dir=cache_dir)

    @staticmethod
    def prepare(text: str) -> str:
        # Use the same spelling at document/query time; source passages stay intact.
        from zoro_rag.ingestion.metadata import ID_PATTERN
        return ID_PATTERN.sub(lambda m: f"{m.group(1).upper()}_{int(m.group(2))}", text)

    def embed(self, texts: Iterable[str]) -> list[tuple[list[int], list[float]]]:
        result = []
        for vector in self.model.embed([self.prepare(text) for text in texts]):
            result.append((vector.indices.tolist(), vector.values.tolist()))
        return result

    def query(self, text: str) -> tuple[list[int], list[float]]:
        vector = next(iter(self.model.query_embed(self.prepare(text))))
        return vector.indices.tolist(), vector.values.tolist()

