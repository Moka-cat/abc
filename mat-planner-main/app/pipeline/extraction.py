"""ExtractionPipeline — orchestrates all per-chunk extraction stages.

Current stages (in order):
  1. RulesExtractionStage   — regex/dict-based entity + property extraction
  2. LLMExtractionStage     — LLM-based NER to supplement rules (optional)
  3. FormulationStage       — chemistry formula and composition extraction

To add a new stage, implement ExtractionStage and append to ExtractionPipeline.stages.
"""
from __future__ import annotations

import json
from loguru import logger

from app.pipeline.base import ExtractionContext, ExtractionStage
from app.pipeline.domain_extractor import (
    KNOWN_ENTITIES,
    ExtractedEntity,
    ExtractedProperty,
    extract_entities_from_text,
    extract_properties_from_text,
)
from app.pipeline.llm_ner import llm_extract_properties
from app.models.orm.domain import DomainEntity, EntityAlias, PropertyValue
from app.models.orm.evidence import EvidenceLink


# ---------------------------------------------------------------------------
# Shared DB helpers (used by multiple stages)
# ---------------------------------------------------------------------------

def _upsert_entity(db, entity: ExtractedEntity, namespace: str) -> DomainEntity:
    existing = db.query(DomainEntity).filter_by(canonical_name=entity.canonical_name).first()
    if existing:
        return existing
    orm_entity = DomainEntity(
        canonical_name=entity.canonical_name,
        entity_type=entity.entity_type,
        namespace=namespace,
    )
    db.add(orm_entity)
    db.flush()
    for alias in entity.aliases:
        db.add(EntityAlias(entity_id=orm_entity.id, alias=alias))
    return orm_entity


def _upsert_property(db, prop: ExtractedProperty, document_id: str, chunk_orm, section_orm) -> None:
    entity_orm = db.query(DomainEntity).filter_by(canonical_name=prop.entity_name).first()
    if not entity_orm:
        return
    num = round(prop.value_numeric, 6) if prop.value_numeric is not None else None
    pv = PropertyValue(
        entity_id=entity_orm.id,
        property_name=prop.property_name,
        value_text=prop.value_text,
        value_numeric=num,
        unit=prop.unit,
        condition_json=json.dumps(prop.condition) if prop.condition else None,
        source_document_id=document_id,
    )
    db.add(pv)
    db.flush()
    span_start = prop.evidence_offset if prop.evidence_offset >= 0 else None
    span_end = (span_start + len(prop.evidence_quote)) if (span_start is not None and prop.evidence_quote) else None
    db.add(EvidenceLink(
        property_value_id=pv.id,
        chunk_id=chunk_orm.id if chunk_orm else None,
        document_id=document_id,
        section_path=section_orm.section_path if section_orm else "",
        quote=prop.evidence_quote[:500] if prop.evidence_quote else None,
        span_start=span_start,
        span_end=span_end,
    ))


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------

class RulesExtractionStage(ExtractionStage):
    """Regex + dictionary-based entity and property extraction."""

    def extract(self, ctx: ExtractionContext) -> None:
        entities = extract_entities_from_text(ctx.chunk.text)
        properties = extract_properties_from_text(ctx.chunk.text, entities)
        for entity in entities:
            _upsert_entity(ctx.db, entity, ctx.namespace)
        for prop in properties:
            _upsert_property(ctx.db, prop, ctx.document_id, ctx.chunk_orm, ctx.section_orm)
        # Store for downstream stages to augment
        ctx.chunk._extracted_entities = entities
        ctx.chunk._extracted_properties = properties


class LLMExtractionStage(ExtractionStage):
    """LLM-based NER; merges results with rules-based output (no duplicates)."""

    def extract(self, ctx: ExtractionContext) -> None:
        entities: list[ExtractedEntity] = getattr(ctx.chunk, "_extracted_entities", [])
        properties: list[ExtractedProperty] = getattr(ctx.chunk, "_extracted_properties", [])

        llm_props = llm_extract_properties(
            ctx.chunk.text,
            chunk_id=ctx.chunk_orm.id if ctx.chunk_orm else "",
        )

        existing_keys = {
            (p.entity_name, p.property_name, round(p.value_numeric or 0, 2))
            for p in properties
        }
        for lp in llm_props:
            key = (lp.entity_name, lp.property_name, round(lp.value_numeric or 0, 2))
            if key not in existing_keys:
                properties.append(lp)
                existing_keys.add(key)
                if not any(e.canonical_name == lp.entity_name for e in entities):
                    info = KNOWN_ENTITIES.get(lp.entity_name, {})
                    new_entity = ExtractedEntity(
                        canonical_name=lp.entity_name,
                        entity_type=info.get("type", "energetic_material"),
                        aliases=info.get("aliases", []),
                    )
                    entities.append(new_entity)
                    _upsert_entity(ctx.db, new_entity, ctx.namespace)
                _upsert_property(ctx.db, lp, ctx.document_id, ctx.chunk_orm, ctx.section_orm)


class FormulationStage(ExtractionStage):
    """Extract composite propellant / explosive formulations."""

    def extract(self, ctx: ExtractionContext) -> None:
        from app.pipeline.formulation_extractor import extract_formulations
        from app.models.orm.formulation import Formulation, FormulationComponent

        for form in extract_formulations(ctx.chunk.text):
            form_orm = Formulation(
                formulation_type=form.formulation_type,
                description=form.description[:500] if form.description else None,
                document_id=ctx.document_id,
                chunk_id=ctx.chunk_orm.id if ctx.chunk_orm else None,
                namespace=ctx.namespace,
            )
            ctx.db.add(form_orm)
            ctx.db.flush()
            for comp in form.components:
                ctx.db.add(FormulationComponent(
                    formulation_id=form_orm.id,
                    component_name=comp.component_name,
                    mass_fraction=comp.mass_fraction,
                    role=comp.role,
                ))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class ExtractionPipeline:
    """Runs all extraction stages on a single chunk.

    Stages are run in order; each stage may read ctx.chunk._extracted_* set
    by a previous stage (e.g. LLMExtractionStage reads RulesExtractionStage output).

    Default stage order:
      1. RulesExtractionStage   — regex/dict entity+property extraction
      2. LLMExtractionStage     — LLM NER to supplement rules
      3. FormulationStage       — chemistry formula/composition extraction
      4. ChunkSummaryStage      — LLM summary+keywords for parent chunks (navigation memory)
    """

    def __init__(self, stages: list[ExtractionStage] | None = None):
        from app.pipeline.summary_stage import ChunkSummaryStage
        self.stages: list[ExtractionStage] = stages or [
            RulesExtractionStage(),
            LLMExtractionStage(),
            FormulationStage(),
            ChunkSummaryStage(),
        ]

    def run(self, ctx: ExtractionContext) -> None:
        """Apply all stages to the given context. Non-fatal: logs and continues on error."""
        for stage in self.stages:
            try:
                stage.extract(ctx)
            except Exception as e:
                logger.warning(f"ExtractionStage {type(stage).__name__} failed (non-fatal): {e}")
