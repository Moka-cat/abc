"""Embedding service — wraps the OpenAI-compatible embedding API.

All chunks are encoded with bge-m3 (1024-dim, multilingual), which supports
cross-lingual retrieval: Chinese queries can match English document content.

Usage:
    emb_service = EmbeddingService()
    embeddings = emb_service.embed(["RDX density", "铝粉燃烧特性"])
    # → list of list[float], each length EMBED_DIM
"""
import struct
from typing import Sequence

import numpy as np
from loguru import logger

from app.core.config import settings
from app.services.llm_client import get_llm_client


class EmbeddingService:
    """Thin wrapper that batches texts and returns float32 numpy arrays."""

    def __init__(self) -> None:
        self._client = get_llm_client()

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embeddings for *texts*.  Empty/whitespace strings get zero vectors."""
        if not texts:
            return []

        results: list[list[float] | None] = [None] * len(texts)
        non_empty: list[tuple[int, str]] = []

        for i, t in enumerate(texts):
            if t and t.strip():
                non_empty.append((i, t.strip()))
            else:
                results[i] = [0.0] * settings.EMBED_DIM

        # Batch API calls
        batch_size = settings.EMBED_BATCH_SIZE
        for batch_start in range(0, len(non_empty), batch_size):
            batch = non_empty[batch_start : batch_start + batch_size]
            indices, batch_texts = zip(*batch)
            try:
                resp = self._client.embeddings.create(
                    model=settings.EMBED_MODEL,
                    input=list(batch_texts),
                    timeout=8.0,  # fail fast: degrade to keyword-only if API is slow
                )
                for item, idx in zip(resp.data, indices):
                    results[idx] = item.embedding
            except Exception as e:
                logger.warning(f"Embedding API error (batch {batch_start}): {e}")
                for idx, _ in batch:
                    results[idx] = [0.0] * settings.EMBED_DIM

        return [r for r in results]  # type: ignore[return-value]

    # ──────────────────────────────────────────────────────────────
    # Serialization helpers (for storing in SQLite BLOB)
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def to_bytes(embedding: list[float]) -> bytes:
        return np.array(embedding, dtype=np.float32).tobytes()

    @staticmethod
    def from_bytes(blob: bytes) -> np.ndarray:
        return np.frombuffer(blob, dtype=np.float32)

    @staticmethod
    def cosine(a: np.ndarray, b: np.ndarray) -> float:
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)
