"""Add span_start / span_end columns to evidence_links (existing DB migration).

Safe to run multiple times — skips if columns already exist.

    uv run python scripts/migrate_evidence_spans.py
    uv run python scripts/migrate_evidence_spans.py --backfill   # compute spans from quotes
"""
import argparse
import sys

sys.path.insert(0, ".")

from loguru import logger
from sqlalchemy import text
from app.core.database import engine, SessionLocal


def add_columns() -> None:
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            # SQLite: check if column exists via PRAGMA
            cols = conn.execute(text("PRAGMA table_info(evidence_links)")).fetchall()
            existing = {row[1] for row in cols}
            for col in ("span_start", "span_end"):
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE evidence_links ADD COLUMN {col} INTEGER"))
                    logger.info(f"  Added column: {col}")
                else:
                    logger.info(f"  Column already exists: {col}")
        else:
            # PostgreSQL
            for col in ("span_start", "span_end"):
                conn.execute(text(
                    f"ALTER TABLE evidence_links ADD COLUMN IF NOT EXISTS {col} INTEGER"
                ))
                logger.info(f"  ADD COLUMN IF NOT EXISTS: {col}")
        conn.commit()


def backfill_spans() -> int:
    """Compute span_start/span_end from quote + chunk text for existing rows."""
    from app.models.orm.evidence import EvidenceLink
    from app.models.orm.document import DocumentChunk

    updated = 0
    db = SessionLocal()
    try:
        # Only rows that have a quote but no span yet
        rows = (
            db.query(EvidenceLink)
            .filter(
                EvidenceLink.quote.isnot(None),
                EvidenceLink.span_start.is_(None),
                EvidenceLink.chunk_id.isnot(None),
            )
            .all()
        )
        logger.info(f"Backfilling spans for {len(rows)} evidence links …")
        chunk_cache: dict[str, str] = {}
        for ev in rows:
            if ev.chunk_id not in chunk_cache:
                chunk = db.query(DocumentChunk).filter_by(id=ev.chunk_id).first()
                chunk_cache[ev.chunk_id] = chunk.chunk_text if chunk else ""
            text_content = chunk_cache[ev.chunk_id]
            if not text_content or not ev.quote:
                continue
            idx = text_content.find(ev.quote)
            if idx >= 0:
                ev.span_start = idx
                ev.span_end = idx + len(ev.quote)
                updated += 1
            if updated % 200 == 0:
                db.commit()
        db.commit()
    finally:
        db.close()
    return updated


def main():
    parser = argparse.ArgumentParser(description="Migrate evidence_links: add span columns")
    parser.add_argument("--backfill", action="store_true",
                        help="Compute span_start/span_end from existing quotes")
    args = parser.parse_args()

    logger.info("Adding span_start / span_end columns …")
    add_columns()

    if args.backfill:
        n = backfill_spans()
        logger.info(f"Backfilled spans: {n} rows updated")

    logger.info("Done.")


if __name__ == "__main__":
    main()
