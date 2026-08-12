"""Add retrieval_hit_count column to document_chunks (safe migration).

Run once on existing databases:
    uv run python scripts/migrate_retrieval_hits.py
"""
import sys
sys.path.insert(0, ".")

from loguru import logger
from sqlalchemy import text
from app.core.database import engine


def main() -> None:
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            cols = conn.execute(text("PRAGMA table_info(document_chunks)")).fetchall()
            existing = {row[1] for row in cols}
            if "retrieval_hit_count" not in existing:
                conn.execute(text(
                    "ALTER TABLE document_chunks ADD COLUMN retrieval_hit_count INTEGER NOT NULL DEFAULT 0"
                ))
                conn.commit()
                logger.info("Added column: retrieval_hit_count")
            else:
                logger.info("Column already exists: retrieval_hit_count")
        else:
            conn.execute(text(
                "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
                "retrieval_hit_count INTEGER NOT NULL DEFAULT 0"
            ))
            conn.commit()
            logger.info("ADD COLUMN IF NOT EXISTS: retrieval_hit_count")
    logger.info("Done.")


if __name__ == "__main__":
    main()
