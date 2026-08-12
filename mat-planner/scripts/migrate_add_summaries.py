"""Add summary / keywords columns to document_chunks and document_sections (safe migration).

These columns power the agentic retrieval navigator:
  - document_chunks.summary   — LLM-generated section summary (parent chunks only)
  - document_chunks.keywords  — semicolon-separated keywords for coarse matching
  - document_sections.summary — propagated from parent chunk summary for outline display

Run after upgrading to the version that adds ChunkSummaryStage and AgenticRetrievalService:

    uv run python scripts/migrate_add_summaries.py

Existing data is unaffected. Re-ingest documents to populate the new columns.
"""
import sys
sys.path.insert(0, ".")

from loguru import logger
from sqlalchemy import text
from app.core.database import engine


_CHUNK_COLUMNS = [
    ("summary", "TEXT"),
    ("keywords", "TEXT"),
]

_SECTION_COLUMNS = [
    ("summary", "TEXT"),
]


def _add_columns_sqlite(conn, table: str, columns: list[tuple[str, str]]) -> None:
    existing_rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    existing = {row[1] for row in existing_rows}
    for col, ddl in columns:
        if col not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            logger.info(f"Added column: {table}.{col}")
        else:
            logger.info(f"Column already exists (skipped): {table}.{col}")


def _add_columns_postgres(conn, table: str, columns: list[tuple[str, str]]) -> None:
    for col, ddl in columns:
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}"
        ))
        logger.info(f"ADD COLUMN IF NOT EXISTS: {table}.{col}")


def main() -> None:
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            _add_columns_sqlite(conn, "document_chunks", _CHUNK_COLUMNS)
            _add_columns_sqlite(conn, "document_sections", _SECTION_COLUMNS)
        else:
            _add_columns_postgres(conn, "document_chunks", _CHUNK_COLUMNS)
            _add_columns_postgres(conn, "document_sections", _SECTION_COLUMNS)
        conn.commit()
    logger.info("Migration complete. Re-ingest documents to populate new columns.")


if __name__ == "__main__":
    main()
