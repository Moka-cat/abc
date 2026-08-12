#!/usr/bin/env python
"""Migration: add parent_chunk_id column and create parent chunks for existing data.

What it does:
  1. ALTER TABLE document_chunks ADD COLUMN parent_chunk_id TEXT REFERENCES document_chunks(id)
  2. For each (document_id, section_path) group of existing flat chunks:
     - Concatenate their texts → create a new "parent" chunk (chunk_type="parent")
     - Set parent_chunk_id on each existing chunk to the new parent's id
  3. Add index on parent_chunk_id

Run once after upgrading the code:
    uv run python scripts/migrate_hierarchical_chunks.py
"""
import sys
import uuid
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from sqlalchemy import text
from app.core.database import SessionLocal

MAX_PARENT_CHARS = 4000


def main() -> None:
    db = SessionLocal()
    try:
        # ── Step 1: Add column if not exists ──────────────────────────
        try:
            db.execute(text(
                "ALTER TABLE document_chunks ADD COLUMN parent_chunk_id TEXT "
                "REFERENCES document_chunks(id)"
            ))
            db.commit()
            logger.info("Added column parent_chunk_id")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                logger.info("Column parent_chunk_id already exists — skipping ALTER")
                db.rollback()
            else:
                raise

        # ── Step 2: Add index ──────────────────────────────────────────
        try:
            db.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_chunks_parent_chunk_id "
                "ON document_chunks(parent_chunk_id)"
            ))
            db.commit()
            logger.info("Index idx_chunks_parent_chunk_id ensured")
        except Exception as e:
            logger.warning(f"Index creation: {e}")
            db.rollback()

        # ── Step 3: Group existing flat chunks, create parents ─────────
        rows = db.execute(text(
            "SELECT id, document_id, section_id, section_path, chunk_text, "
            "       chunk_index, page_start, page_end "
            "FROM document_chunks "
            "WHERE chunk_type != 'parent' AND parent_chunk_id IS NULL "
            "ORDER BY document_id, section_path, chunk_index"
        )).fetchall()

        logger.info(f"Found {len(rows)} flat (no-parent) chunks to group")

        # Group by (document_id, section_path)
        groups: dict[tuple, list] = defaultdict(list)
        for row in rows:
            key = (row.document_id, row.section_path)
            groups[key].append(row)

        logger.info(f"Grouping into {len(groups)} section groups")

        created = 0
        linked = 0
        for (doc_id, section_path), group_rows in groups.items():
            combined = " ".join(r.chunk_text for r in group_rows).strip()[:MAX_PARENT_CHARS]
            if not combined:
                continue

            # section_id: use the first chunk's section_id
            section_id = group_rows[0].section_id

            parent_id = str(uuid.uuid4())
            db.execute(text(
                "INSERT INTO document_chunks "
                "(id, document_id, section_id, chunk_type, chunk_text, chunk_index, "
                " section_path, page_start, page_end, parent_chunk_id, embedding) "
                "VALUES (:id, :doc_id, :sec_id, 'parent', :text, -1, "
                "        :section_path, NULL, NULL, NULL, NULL)"
            ), {
                "id": parent_id,
                "doc_id": doc_id,
                "sec_id": section_id,
                "text": combined,
                "section_path": section_path,
            })
            created += 1

            # Link all children to this parent
            child_ids = [r.id for r in group_rows]
            for cid in child_ids:
                db.execute(text(
                    "UPDATE document_chunks SET parent_chunk_id = :pid WHERE id = :cid"
                ), {"pid": parent_id, "cid": cid})
                linked += 1

        db.commit()
        logger.info(
            f"Done — {created} parent chunks created, {linked} child chunks linked"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
