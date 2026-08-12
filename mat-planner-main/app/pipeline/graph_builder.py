"""Knowledge graph builder.

Populates MemoryGraphNode / MemoryGraphEdge from the existing DomainEntity,
PropertyValue, and DocumentChunk tables.

Node type:
  • entity_type value from DomainEntity (e.g. "compound", "ingredient")

Edge types:
  • CO_OCCURS_WITH   — two entities mentioned in the same chunk
                       weight = co-occurrence count (capped at 50)
  • COMPARED_BY_{prop} — both entities have a measured value for {prop}
                         weight = value similarity (0.0–1.0, 1.0 = same value)

The builder is FULLY IDEMPOTENT: it clears all existing graph data before
rebuilding. Call it after any significant ingest batch.
"""
from __future__ import annotations

import json
from collections import defaultdict

from loguru import logger
from sqlalchemy.orm import Session

from app.models.orm.domain import DomainEntity, PropertyValue
from app.models.orm.document import DocumentChunk
from app.models.orm.graph import MemoryGraphNode, MemoryGraphEdge
from app.pipeline.domain_extractor import KNOWN_ENTITIES


# Build entity-name → canonical_name lookup once (same as llm_ner.py)
def _build_name_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, info in KNOWN_ENTITIES.items():
        lookup[canonical.lower()] = canonical
        for alias in info["aliases"]:
            lookup[alias.lower()] = canonical
    return lookup


_NAME_LOOKUP = _build_name_lookup()


def _entities_in_text(text: str) -> set[str]:
    """Return set of canonical entity names mentioned in text."""
    text_lower = text.lower()
    found: set[str] = set()
    for name_lower, canonical in _NAME_LOOKUP.items():
        if name_lower in text_lower:
            found.add(canonical)
    return found


def build_graph(db: Session) -> dict:
    """Rebuild the entire knowledge graph from scratch.

    Returns a summary dict with counts of nodes and edges created.
    """
    # ── 1. Clear existing graph ────────────────────────────────────────
    deleted_edges = db.query(MemoryGraphEdge).delete()
    deleted_nodes = db.query(MemoryGraphNode).delete()
    db.flush()
    logger.info(f"Graph cleared: {deleted_nodes} nodes, {deleted_edges} edges deleted")

    # ── 2. Create entity nodes ─────────────────────────────────────────
    entities = db.query(DomainEntity).all()
    if not entities:
        db.commit()
        logger.warning("No DomainEntity rows found — graph is empty")
        return {"nodes": 0, "edges": 0}

    entity_node_map: dict[str, str] = {}   # canonical_name → node.id

    for entity in entities:
        aliases = [a.alias for a in entity.aliases]
        prop_count = len(entity.properties)
        node = MemoryGraphNode(
            node_type=entity.entity_type,
            ref_id=entity.id,
            ref_table="domain_entities",
            label=entity.canonical_name,
            properties_json=json.dumps({
                "aliases": aliases,
                "property_count": prop_count,
            }),
        )
        db.add(node)
        db.flush()
        entity_node_map[entity.canonical_name] = node.id

    logger.info(f"Created {len(entity_node_map)} entity nodes")

    # ── 3. CO_OCCURS_WITH edges ────────────────────────────────────────
    # For efficiency, only scan text/table chunks (not parent chunks)
    chunks = (
        db.query(DocumentChunk.chunk_text)
        .filter(DocumentChunk.chunk_type.in_(["text", "table"]))
        .all()
    )

    co_occur: dict[tuple[str, str], int] = defaultdict(int)
    for (chunk_text,) in chunks:
        mentioned = _entities_in_text(chunk_text) & set(entity_node_map.keys())
        if len(mentioned) < 2:
            continue
        sorted_entities = sorted(mentioned)
        for i, a in enumerate(sorted_entities):
            for b in sorted_entities[i + 1:]:
                co_occur[(a, b)] += 1

    co_edge_count = 0
    for (a, b), count in co_occur.items():
        weight = min(float(count), 50.0) / 50.0  # normalise 0–1
        # Store bidirectional as two directed edges
        for src, tgt in [(a, b), (b, a)]:
            db.add(MemoryGraphEdge(
                source_id=entity_node_map[src],
                target_id=entity_node_map[tgt],
                edge_type="CO_OCCURS_WITH",
                weight=weight,
            ))
        co_edge_count += 2

    logger.info(f"Created {co_edge_count} CO_OCCURS_WITH edges")

    # ── 4. COMPARED_BY_{property} edges ───────────────────────────────
    # For each property, gather (entity, avg_value) — only numeric values
    prop_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for pv in db.query(PropertyValue).all():
        if pv.value_numeric is None:
            continue
        entity = db.get(DomainEntity, pv.entity_id)
        if entity and entity.canonical_name in entity_node_map:
            prop_values[pv.property_name][entity.canonical_name].append(pv.value_numeric)

    comp_edge_count = 0
    for prop_name, entity_vals in prop_values.items():
        # Average values per entity
        avg_vals: dict[str, float] = {
            ent: sum(vals) / len(vals)
            for ent, vals in entity_vals.items()
            if vals
        }
        if len(avg_vals) < 2:
            continue

        entities_with_prop = sorted(avg_vals.keys())
        max_val = max(avg_vals.values())
        if max_val == 0:
            continue

        for i, a in enumerate(entities_with_prop):
            for b in entities_with_prop[i + 1:]:
                v_a, v_b = avg_vals[a], avg_vals[b]
                # Similarity: 1 - relative difference, clamped [0, 1]
                similarity = max(0.0, 1.0 - abs(v_a - v_b) / max_val)
                edge_type = f"COMPARED_BY_{prop_name}"
                for src, tgt in [(a, b), (b, a)]:
                    db.add(MemoryGraphEdge(
                        source_id=entity_node_map[src],
                        target_id=entity_node_map[tgt],
                        edge_type=edge_type,
                        weight=round(similarity, 4),
                    ))
                comp_edge_count += 2

    logger.info(f"Created {comp_edge_count} COMPARED_BY_* edges")

    db.commit()
    total_edges = co_edge_count + comp_edge_count
    logger.info(
        f"Graph build complete: {len(entity_node_map)} nodes, {total_edges} edges"
    )
    return {"nodes": len(entity_node_map), "edges": total_edges}
