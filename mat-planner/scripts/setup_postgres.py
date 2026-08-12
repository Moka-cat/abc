"""One-shot PostgreSQL + pgvector setup script.

Run this ONCE after spinning up the PostgreSQL container:

    uv run python scripts/setup_postgres.py

What it does:
  1. Creates all ORM tables (documents, chunks, entities, …)
  2. Installs the pgvector extension
  3. Adds embedding_vec vector(1024) column to document_chunks
  4. Builds HNSW index on embedding_vec (cosine distance)
  5. Creates ingestion_jobs table

Optionally backfills embedding_vec from existing LargeBinary blobs:

    uv run python scripts/setup_postgres.py --backfill
"""
import argparse
import sys

# Ensure project root is on sys.path when run directly
sys.path.insert(0, ".")

from loguru import logger
from app.core.config import settings
from app.core.database import create_all_tables, setup_pgvector, backfill_pgvector_from_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up PostgreSQL + pgvector for mat-planner")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill embedding_vec from existing LargeBinary bytes")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="Drop and recreate the HNSW index (useful after dimension change)")
    args = parser.parse_args()

    if not settings.is_postgres:
        logger.error(
            "DATABASE_URL does not point to PostgreSQL.\n"
            f"Current value: {settings.DATABASE_URL!r}\n"
            "Set DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname in .env"
        )
        sys.exit(1)

    logger.info(f"Connecting to: {settings.DATABASE_URL}")

    # Step 1: create all ORM-managed tables
    logger.info("Creating ORM tables …")
    create_all_tables()
    logger.info("  ✓ Tables created")

    # Step 2: pgvector extension + column + HNSW index
    logger.info("Setting up pgvector …")
    result = setup_pgvector(drop_index=args.rebuild_index)
    for step in result.get("done", []):
        logger.info(f"  ✓ {step}")

    # Step 3 (optional): backfill vector column from existing blobs
    if args.backfill:
        logger.info("Backfilling embedding_vec from LargeBinary blobs …")
        n = backfill_pgvector_from_bytes()
        logger.info(f"  ✓ Updated {n} chunks")

    logger.info("PostgreSQL setup complete.")


if __name__ == "__main__":
    main()
