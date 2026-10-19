"""Small async-friendly wrapper around a Chroma collection."""

from __future__ import annotations

import asyncio
import os
from typing import Any


class WikiVectorStore:
    def __init__(
        self,
        persist_dir: str,
        host: str | None = None,
        port: int | None = None,
        collection_name: str = "wiki_query_pages",
    ):
        self.persist_dir = persist_dir
        self.host = host
        self.port = port
        self.collection_name = collection_name
        self._collection = None

    def _get_collection(self):
        if self._collection is not None:
            return self._collection
        import chromadb
        from chromadb.config import Settings

        if self.host:
            if self.host in {"localhost", "127.0.0.1", "::1"}:
                bypass = {item.strip() for item in os.environ.get("NO_PROXY", "").split(",") if item.strip()}
                bypass.update({"localhost", "127.0.0.1", "::1"})
                os.environ["NO_PROXY"] = ",".join(sorted(bypass))
                os.environ["no_proxy"] = os.environ["NO_PROXY"]
            client = chromadb.HttpClient(
                host=self.host,
                port=self.port or 8000,
                settings=Settings(anonymized_telemetry=False),
            )
        else:
            client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
        self._collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    async def upsert(
        self,
        slugs: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        documents: list[str] | None = None,
    ) -> None:
        if not slugs:
            return
        kwargs: dict[str, Any] = {
            "ids": slugs,
            "embeddings": embeddings,
            "metadatas": metadatas,
        }
        if documents is not None:
            kwargs["documents"] = documents
        await asyncio.to_thread(self._get_collection().upsert, **kwargs)

    async def search_similar(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        threshold: float = 0.35,
        where: dict[str, Any] | None = None,
        include_documents: bool = False,
    ) -> list[dict[str, Any]]:
        include = ["metadatas", "distances"]
        if include_documents:
            include.append("documents")
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
            "include": include,
        }
        if where:
            kwargs["where"] = where
        result = await asyncio.to_thread(self._get_collection().query, **kwargs)
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        documents = (result.get("documents") or [[]])[0] if include_documents else []
        out: list[dict[str, Any]] = []
        for i, (rec_id, distance, metadata) in enumerate(
            zip(ids, distances, metadatas, strict=False)
        ):
            score = max(0.0, 1.0 - float(distance))
            if score < threshold:
                continue
            row: dict[str, Any] = {"slug": rec_id, "semantic_score": score, **(metadata or {})}
            row["id"] = rec_id
            if include_documents and i < len(documents):
                row["document"] = documents[i]
            out.append(row)
        return out

    async def delete(self, slugs: list[str]) -> None:
        if slugs:
            await asyncio.to_thread(self._get_collection().delete, ids=slugs)

    async def delete_where(self, where: dict[str, Any]) -> None:
        if where:
            await asyncio.to_thread(self._get_collection().delete, where=where)

    async def count(self) -> int:
        return await asyncio.to_thread(self._get_collection().count)

    async def has_records(self, *, where: dict[str, Any] | None = None) -> bool:
        kwargs: dict[str, Any] = {"limit": 1, "include": []}
        if where:
            kwargs["where"] = where
        result = await asyncio.to_thread(self._get_collection().get, **kwargs)
        return bool(result.get("ids"))

    async def list_records(self, *, where: dict[str, Any] | None = None) -> dict[str, dict]:
        kwargs: dict[str, Any] = {"include": ["metadatas"]}
        if where:
            kwargs["where"] = where
        result = await asyncio.to_thread(self._get_collection().get, **kwargs)
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        return {slug: (metadata or {}) for slug, metadata in zip(ids, metadatas, strict=False)}
