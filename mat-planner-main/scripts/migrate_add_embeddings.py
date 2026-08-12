#!/usr/bin/env python
"""Migration: add embedding column to document_chunks, then embed all existing chunks.

Run once after upgrading to the vector-search version:
    uv run python scripts/migrate_add_embeddings.py

Safe to re-run: skips chunks that already have an embedding.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from loguru import logger

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.orm.document import DocumentChunk
from app.services.embedding import EmbeddingService


# ── Step 1: add column if missing ────────────────────────────────
def _add_column_if_missing(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(document_chunks)")}
    if "embedding" not in cols:
        conn.execute("ALTER TABLE document_chunks ADD COLUMN embedding BLOB")
        conn.commit()
        logger.info("Added 'embedding' column to document_chunks")
    else:
        logger.info("Column 'embedding' already exists — skipping ALTER TABLE")
    conn.close()


# ── Step 2: embed all chunks without an embedding ────────────────
def _embed_existing_chunks(batch_size: int = 64) -> None:
    if not settings.EMBED_ENABLED:
        logger.warning("EMBED_ENABLED=False — skipping embedding generation")
        return

    emb_service = EmbeddingService()
    db = SessionLocal()

    try:
        total = db.query(DocumentChunk).filter(DocumentChunk.embedding.is_(None)).count()
        logger.info(f"Chunks without embedding: {total}")
        if total == 0:
            return

        done = 0
        while True:
            chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.embedding.is_(None))
                .limit(batch_size)
                .all()
            )
            if not chunks:
                break

            texts = [c.chunk_text for c in chunks]
            embeddings = emb_service.embed(texts)

            for chunk, emb in zip(chunks, embeddings):
                chunk.embedding = EmbeddingService.to_bytes(emb)

            db.commit()
            done += len(chunks)
            logger.info(f"  Embedded {done}/{total} chunks …")

        logger.info(f"Done — {done} chunks embedded with {settings.EMBED_MODEL}")
    finally:
        db.close()


def main() -> None:
    # Derive SQLite path from DATABASE_URL  (sqlite:///./foo.db → ./foo.db)
    db_url = settings.DATABASE_URL
    if not db_url.startswith("sqlite"):
        logger.warning(f"Non-SQLite database ({db_url}); skipping ALTER TABLE step")
    else:
        db_path = db_url.replace("sqlite:///", "")
        _add_column_if_missing(db_path)

    _embed_existing_chunks()


if __name__ == "__main__":
    main()
