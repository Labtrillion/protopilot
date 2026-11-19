from __future__ import annotations

import json
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

from zoro_rag.schemas import Chunk


class QdrantStore:
    def __init__(self, path: Path, collection: str, dimension: int, url: str | None = None, api_key: str | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(url=url, api_key=api_key) if url else QdrantClient(path=str(path))
        self.collection = collection
        self.dimension = dimension

    def ensure_collection(self, recreate: bool = False) -> None:
        exists = self.client.collection_exists(self.collection)
        if exists and recreate:
            self.client.delete_collection(self.collection); exists = False
        if not exists:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config={"dense": models.VectorParams(size=self.dimension, distance=models.Distance.COSINE)},
                sparse_vectors_config={"sparse": models.SparseVectorParams(index=models.SparseIndexParams(on_disk=False), modifier=models.Modifier.IDF)},
            )

    @staticmethod
    def point_id(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"zoro-rag:{chunk_id}"))

    def upsert(self, chunks: list[Chunk], dense: list[list[float]], sparse: list[tuple[list[int], list[float]]]) -> None:
        points = []
        for chunk, dense_vector, (indices, values) in zip(chunks, dense, sparse):
            points.append(models.PointStruct(
                id=self.point_id(chunk.chunk_id),
                vector={"dense": dense_vector, "sparse": models.SparseVector(indices=indices, values=values)},
                payload=chunk.model_dump(mode="json"),
            ))
        self.client.upsert(self.collection, points=points, wait=True)

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            self.client.delete(self.collection, models.PointIdsList(points=[self.point_id(item) for item in chunk_ids]), wait=True)

    def existing_chunk_ids(self, chunk_ids: list[str]) -> list[str]:
        found = []
        for start in range(0, len(chunk_ids), 256):
            points = self.client.retrieve(self.collection, ids=[self.point_id(c) for c in chunk_ids[start:start + 256]], with_payload=True)
            found.extend(point.payload["chunk_id"] for point in points)
        return found

    def search_dense(self, vector: list[float], limit: int, query_filter=None) -> list[tuple[Chunk, float]]:
        response = self.client.query_points(self.collection, query=vector, using="dense", limit=limit, with_payload=True, query_filter=query_filter)
        return [(Chunk.model_validate(point.payload), float(point.score)) for point in response.points]

    def search_sparse(self, sparse: tuple[list[int], list[float]], limit: int, query_filter=None) -> list[tuple[Chunk, float]]:
        indices, values = sparse
        response = self.client.query_points(self.collection, query=models.SparseVector(indices=indices, values=values), using="sparse", limit=limit, with_payload=True, query_filter=query_filter)
        return [(Chunk.model_validate(point.payload), float(point.score)) for point in response.points]

    def count(self) -> int:
        return int(self.client.count(self.collection, exact=True).count)

