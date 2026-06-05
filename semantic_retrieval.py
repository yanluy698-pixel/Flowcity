"""Optional local embedding retrieval with a deterministic fallback."""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Iterable

from poi_profiles import neutral_poi_document


DEFAULT_MODEL = os.getenv("FLOWCITY_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
MODEL_CACHE_DIR = os.getenv("FLOWCITY_EMBEDDING_CACHE_DIR")
DEFAULT_TOP_K = int(os.getenv("FLOWCITY_SEMANTIC_TOP_K", "8"))
DEFAULT_MIN_SCORE = float(os.getenv("FLOWCITY_SEMANTIC_MIN_SCORE", "0.32"))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _fallback_embedding(text: str, dimensions: int = 384) -> list[float]:
    """Hash Chinese characters and bigrams into a stable semantic-ish vector."""
    normalized = "".join(str(text).lower().split())
    tokens = [*normalized, *[normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))]]
    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class SemanticRetriever:
    def __init__(self) -> None:
        self._model: Any | None = None
        self._provider = "hashed-fallback"
        self._vectors: dict[str, list[float]] = {}
        self._documents: dict[str, str] = {}
        if os.getenv("FLOWCITY_EMBEDDING_ENABLED", "false").lower() == "true":
            try:
                from fastembed import TextEmbedding  # type: ignore

                kwargs: dict[str, Any] = {"model_name": DEFAULT_MODEL}
                if MODEL_CACHE_DIR:
                    kwargs["cache_dir"] = MODEL_CACHE_DIR
                self._model = TextEmbedding(**kwargs)
                self._provider = f"fastembed:{DEFAULT_MODEL}"
            except Exception:
                self._model = None

    @property
    def provider(self) -> str:
        return self._provider

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        values = [str(text) for text in texts]
        if self._model is not None:
            return [list(map(float, vector)) for vector in self._model.embed(values)]
        return [_fallback_embedding(text) for text in values]

    def index(self, pois: list[dict[str, Any]], area_names: dict[str, str] | None = None) -> None:
        area_names = area_names or {}
        pending_ids: list[str] = []
        documents: list[str] = []
        for poi in pois:
            poi_id = str(poi.get("id") or poi.get("poiId") or "")
            if not poi_id:
                continue
            document = neutral_poi_document(poi, area_names.get(str(poi.get("areaId") or ""), ""))
            if self._documents.get(poi_id) == document:
                continue
            pending_ids.append(poi_id)
            documents.append(document)
            self._documents[poi_id] = document
        for poi_id, vector in zip(pending_ids, self.embed(documents)):
            self._vectors[poi_id] = vector

    def search(
        self,
        query: str,
        pois: list[dict[str, Any]],
        *,
        area_ids: set[str] | None = None,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> list[dict[str, Any]]:
        area_names = {str(poi.get("areaId") or ""): str(poi.get("areaName") or "") for poi in pois}
        self.index(pois, area_names)
        query_vector = self.embed([query])[0]
        scored: list[dict[str, Any]] = []
        for poi in pois:
            if area_ids and str(poi.get("areaId") or "") not in area_ids:
                continue
            poi_id = str(poi.get("id") or poi.get("poiId") or "")
            vector = self._vectors.get(poi_id)
            if vector is None:
                continue
            similarity = cosine_similarity(query_vector, vector)
            if similarity >= min_score:
                scored.append(
                    {
                        "poiId": poi_id,
                        "areaId": poi.get("areaId"),
                        "similarity": round(similarity, 4),
                        "query": query,
                        "provider": self.provider,
                    }
                )
        scored.sort(key=lambda item: (-float(item["similarity"]), str(item["poiId"])))
        return scored[:top_k]


RETRIEVER = SemanticRetriever()
