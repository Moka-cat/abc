"""Add summary / summary_embedding columns to documents (safe migration).

    uv run python scripts/migrate_doc_summary.py
"""
import sys
sys.path.insert(0, ".")

from loguru import logger
from sqlalchemy import text
from app.core.database import engine


def main() -> None:
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            cols = conn.execute(text("PRAGMA table_info(documents)")).fetchall()
            existing = {row[1] for row in cols}
            for col, ddl in [
                ("summary", "TEXT"),
                ("summary_embedding", "BLOB"),
            ]:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE documents ADD COLUMN {col} {ddl}"))
                    logger.info(f"Added column: {col}")
                else:
                    logger.info(f"Column already exists: {col}")
        else:
            for col, ddl in [
                ("summary", "TEXT"),
                ("summary_embedding", "BYTEA"),
            ]:
                conn.execute(text(
                    f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS {col} {ddl}"
                ))
                logger.info(f"ADD COLUMN IF NOT EXISTS: {col}")
        conn.commit()
    logger.info("Done.")


if __name__ == "__main__":
    main()
