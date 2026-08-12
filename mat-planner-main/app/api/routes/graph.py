"""Knowledge graph API.

GET  /graph/nodes         — list graph nodes (filterable by type, label)
GET  /graph/edges         — list graph edges (filterable by type, source/target)
GET  /graph/neighbors/{label} — nodes connected to a given entity label
GET  /graph/stats         — graph summary statistics
POST /graph/rebuild       — trigger full graph rebuild from current DB

Designed for frontend visualization (e.g. D3.js force-graph, Cytoscape.js).
"""
from __future__ import annotations

import json
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.orm.graph import MemoryGraphNode, MemoryGraphEdge

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class NodeResponse(BaseModel):
    id: str
    node_type: str
    label: str
    ref_id: str | None = None
    properties: dict | None = None


class EdgeResponse(BaseModel):
    id: str
    source_id: str
    target_id: str
    source_label: str = ""
    target_label: str = ""
    edge_type: str
    weight: float


class GraphStatsResponse(BaseModel):
    node_count: int
    edge_count: int
    node_types: dict[str, int]
    edge_types: dict[str, int]
    top_connected: list[dict]   # [{label, degree}]


class RebuildResponse(BaseModel):
    status: str
    nodes: int = 0
    edges: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/nodes", response_model=list[NodeResponse])
def list_nodes(
    node_type: str | None = Query(None, description="Filter by node_type (e.g. 'compound')"),
    label: str | None = Query(None, description="Substring filter on label (case-insensitive)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[NodeResponse]:
    """List knowledge graph nodes."""
    q = db.query(MemoryGraphNode)
    if node_type:
        q = q.filter(MemoryGraphNode.node_type == node_type)
    if label:
        q = q.filter(MemoryGraphNode.label.ilike(f"%{label}%"))
    nodes = q.order_by(MemoryGraphNode.label).offset(offset).limit(limit).all()
    return [
        NodeResponse(
            id=n.id,
            node_type=n.node_type,
            label=n.label,
            ref_id=n.ref_id,
            properties=json.loads(n.properties_json) if n.properties_json else None,
        )
        for n in nodes
    ]


@router.get("/edges", response_model=list[EdgeResponse])
def list_edges(
    edge_type: str | None = Query(None, description="Filter by edge_type (e.g. 'CO_OCCURS_WITH')"),
    source_label: str | None = Query(None, description="Filter edges from this node label"),
    min_weight: float = Query(0.0, ge=0.0),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[EdgeResponse]:
    """List knowledge graph edges with optional filtering."""
    q = db.query(MemoryGraphEdge).filter(MemoryGraphEdge.weight >= min_weight)
    if edge_type:
        q = q.filter(MemoryGraphEdge.edge_type == edge_type)

    if source_label:
        node = (
            db.query(MemoryGraphNode)
            .filter(MemoryGraphNode.label.ilike(source_label))
            .first()
        )
        if not node:
            return []
        q = q.filter(MemoryGraphEdge.source_id == node.id)

    edges = q.order_by(MemoryGraphEdge.weight.desc()).offset(offset).limit(limit).all()

    # Bulk-load node labels for source/target IDs
    node_ids = {e.source_id for e in edges} | {e.target_id for e in edges}
    label_map: dict[str, str] = {}
    if node_ids:
        for n in db.query(MemoryGraphNode).filter(MemoryGraphNode.id.in_(node_ids)).all():
            label_map[n.id] = n.label

    return [
        EdgeResponse(
            id=e.id,
            source_id=e.source_id,
            target_id=e.target_id,
            source_label=label_map.get(e.source_id, ""),
            target_label=label_map.get(e.target_id, ""),
            edge_type=e.edge_type,
            weight=e.weight,
        )
        for e in edges
    ]


@router.get("/neighbors/{label}", response_model=list[EdgeResponse])
def get_neighbors(
    label: str,
    edge_type: str | None = Query(None),
    min_weight: float = Query(0.0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[EdgeResponse]:
    """Return all edges connected to the node with the given label (outgoing + incoming)."""
    node = (
        db.query(MemoryGraphNode)
        .filter(MemoryGraphNode.label.ilike(label))
        .first()
    )
    if not node:
        raise HTTPException(status_code=404, detail=f"Node with label {label!r} not found")

    q_out = db.query(MemoryGraphEdge).filter(
        MemoryGraphEdge.source_id == node.id,
        MemoryGraphEdge.weight >= min_weight,
    )
    q_in = db.query(MemoryGraphEdge).filter(
        MemoryGraphEdge.target_id == node.id,
        MemoryGraphEdge.weight >= min_weight,
    )
    if edge_type:
        q_out = q_out.filter(MemoryGraphEdge.edge_type == edge_type)
        q_in = q_in.filter(MemoryGraphEdge.edge_type == edge_type)

    edges = (
        q_out.union(q_in)
        .order_by(MemoryGraphEdge.weight.desc())
        .limit(limit)
        .all()
    )

    node_ids = {e.source_id for e in edges} | {e.target_id for e in edges}
    label_map: dict[str, str] = {}
    if node_ids:
        for n in db.query(MemoryGraphNode).filter(MemoryGraphNode.id.in_(node_ids)).all():
            label_map[n.id] = n.label

    return [
        EdgeResponse(
            id=e.id,
            source_id=e.source_id,
            target_id=e.target_id,
            source_label=label_map.get(e.source_id, ""),
            target_label=label_map.get(e.target_id, ""),
            edge_type=e.edge_type,
            weight=e.weight,
        )
        for e in edges
    ]


@router.get("/stats", response_model=GraphStatsResponse)
def get_graph_stats(db: Session = Depends(get_db)) -> GraphStatsResponse:
    """Return summary statistics about the knowledge graph."""
    node_count = db.query(func.count(MemoryGraphNode.id)).scalar() or 0
    edge_count = db.query(func.count(MemoryGraphEdge.id)).scalar() or 0

    # Node type distribution
    node_type_rows = (
        db.query(MemoryGraphNode.node_type, func.count(MemoryGraphNode.id))
        .group_by(MemoryGraphNode.node_type)
        .all()
    )
    node_types = {row[0]: row[1] for row in node_type_rows}

    # Edge type distribution
    edge_type_rows = (
        db.query(MemoryGraphEdge.edge_type, func.count(MemoryGraphEdge.id))
        .group_by(MemoryGraphEdge.edge_type)
        .all()
    )
    edge_types = {row[0]: row[1] for row in edge_type_rows}

    # Top-connected nodes (highest out-degree)
    degree_rows = (
        db.query(MemoryGraphEdge.source_id, func.count(MemoryGraphEdge.id).label("degree"))
        .group_by(MemoryGraphEdge.source_id)
        .order_by(func.count(MemoryGraphEdge.id).desc())
        .limit(10)
        .all()
    )
    top_ids = [r[0] for r in degree_rows]
    degree_map = {r[0]: r[1] for r in degree_rows}
    top_nodes = db.query(MemoryGraphNode).filter(MemoryGraphNode.id.in_(top_ids)).all()
    node_label_map = {n.id: n.label for n in top_nodes}
    top_connected = [
        {"label": node_label_map.get(nid, nid), "degree": degree_map[nid]}
        for nid in top_ids
        if nid in node_label_map
    ]

    return GraphStatsResponse(
        node_count=int(node_count),
        edge_count=int(edge_count),
        node_types=node_types,
        edge_types=edge_types,
        top_connected=top_connected,
    )


@router.post("/rebuild", response_model=RebuildResponse, status_code=202)
def rebuild_graph(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> RebuildResponse:
    """Trigger a full knowledge graph rebuild in the background.

    The rebuild is idempotent — it clears and recreates all nodes and edges
    from the current DomainEntity, PropertyValue, and DocumentChunk tables.
    Returns immediately; the rebuild runs asynchronously.
    """
    def _do_rebuild() -> None:
        from app.core.database import SessionLocal
        from app.pipeline.graph_builder import build_graph
        _db = SessionLocal()
        try:
            stats = build_graph(_db)
            from loguru import logger as _logger
            _logger.info(f"[graph/rebuild] Done: {stats['nodes']} nodes, {stats['edges']} edges")
        except Exception as e:
            from loguru import logger as _logger
            _logger.error(f"[graph/rebuild] Failed: {e}")
        finally:
            _db.close()

    background_tasks.add_task(_do_rebuild)
    return RebuildResponse(status="rebuilding")
