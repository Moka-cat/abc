#!/usr/bin/env python
"""Migration: run LLM NER on existing chunks and add newly discovered property values.

Only processes chunks that:
  1. chunk_type in ('text', 'table')
  2. Contain at least one known entity name
  3. Contain numeric data (≥3 digits)

Run once after the llm_ner module is available:
    uv run python scripts/migrate_llm_ner.py [--dry-run] [--limit N]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from app.core.database import SessionLocal
from app.models.orm.document import DocumentChunk
from app.models.orm.domain import DomainEntity, EntityAlias, PropertyValue
from app.models.orm.evidence import EvidenceLink
from app.pipeline.domain_extractor import KNOWN_ENTITIES
from app.pipeline.llm_ner import llm_extract_properties, _has_entity_mention, _has_numeric_data
from app.pipeline.domain_extractor import ExtractedEntity


def _upsert_entity(db, canonical_name: str, namespace: str = "default") -> DomainEntity:
    existing = db.query(DomainEntity).filter_by(canonical_name=canonical_name).first()
    if existing:
        return existing
    info = KNOWN_ENTITIES.get(canonical_name, {})
    orm_entity = DomainEntity(
        canonical_name=canonical_name,
        entity_type=info.get("type", "energetic_material"),
        namespace=namespace,
    )
    db.add(orm_entity)
    db.flush()
    for alias in info.get("aliases", []):
        db.add(EntityAlias(entity_id=orm_entity.id, alias=alias))
    return orm_entity


def main(dry_run: bool = False, limit: int | None = None) -> None:
    db = SessionLocal()
    try:
        # Load chunks eligible for LLM NER
        q = db.query(DocumentChunk).filter(
            DocumentChunk.chunk_type.in_(["text", "table"])
        )
        if limit:
            q = q.limit(limit)
        chunks = q.all()
        logger.info(f"Loaded {len(chunks)} text/table chunks")

        eligible = [
            c for c in chunks
            if _has_entity_mention(c.chunk_text.lower()) and _has_numeric_data(c.chunk_text)
        ]
        logger.info(f"{len(eligible)} chunks eligible (entity mention + numeric data)")

        total_added = 0
        for i, chunk in enumerate(eligible):
            props = llm_extract_properties(chunk.chunk_text, chunk_id=chunk.id)
            if not props:
                continue

            for prop in props:
                # Check if this property value already exists (same entity+property+rounded value)
                entity_orm = db.query(DomainEntity).filter_by(
                    canonical_name=prop.entity_name
                ).first()
                if not entity_orm and not dry_run:
                    entity_orm = _upsert_entity(db, prop.entity_name, namespace="default")

                if entity_orm:
                    num = round(prop.value_numeric, 2) if prop.value_numeric is not None else None
                    existing_pv = db.query(PropertyValue).filter_by(
                        entity_id=entity_orm.id,
                        property_name=prop.property_name,
                    ).filter(
                        PropertyValue.value_numeric.between(
                            (num or 0) - 0.01, (num or 0) + 0.01
                        )
                    ).first() if num is not None else None

                    if existing_pv:
                        logger.debug(
                            f"  SKIP duplicate: {prop.entity_name}.{prop.property_name}={num}"
                        )
                        continue

                logger.info(
                    f"  [{i+1}/{len(eligible)}] NEW: {prop.entity_name}.{prop.property_name}"
                    f" = {prop.value_numeric} {prop.unit}"
                )

                if dry_run:
                    total_added += 1
                    continue

                if not entity_orm:
                    entity_orm = _upsert_entity(db, prop.entity_name, namespace="default")

                num_stored = round(prop.value_numeric, 6) if prop.value_numeric is not None else None
                pv = PropertyValue(
                    entity_id=entity_orm.id,
                    property_name=prop.property_name,
                    value_text=prop.value_text,
                    value_numeric=num_stored,
                    unit=prop.unit,
                    source_document_id=chunk.document_id,
                )
                db.add(pv)
                db.flush()

                ev = EvidenceLink(
                    property_value_id=pv.id,
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    section_path=chunk.section_path or "",
                    quote=prop.evidence_quote[:500] if prop.evidence_quote else None,
                )
                db.add(ev)
                total_added += 1

        if not dry_run:
            db.commit()
            logger.info(f"Done — {total_added} new property values added via LLM NER")
        else:
            logger.info(f"Dry run — would add {total_added} new property values")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill property values with LLM NER")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N chunks")
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit)
