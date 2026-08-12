"""Retrieval endpoints.

POST /retrieval/query     — one-shot retrieval (existing)
GET  /retrieval/runs      — recent retrieval run history
GET  /retrieval/stats     — aggregated usage analytics
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.orm.retrieval import RetrievalRun, RetrievalStep
from app.models.schemas.retrieval import RetrievalQueryRequest, RetrievalQueryResponse
from app.services.retrieval import RetrievalService

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class RetrievalRunResponse(BaseModel):
    id: str
    query: str
    namespace: str
    top_k: int
    result_count: int
    created_at: str


class RetrievalStatsResponse(BaseModel):
    total_runs: int
    avg_result_count: float
    namespaces: dict[str, int]          # namespace → run count
    zero_result_queries: list[str]      # recent queries that returned 0 results
    top_queries: list[dict]             # [{query, count}] most-repeated queries
    result_count_distribution: dict[str, int]  # "0", "1-3", "4-7", "8-10", "10+" → count


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/query", response_model=RetrievalQueryResponse)
def query(req: RetrievalQueryRequest, db: Session = Depends(get_db)):
    service = RetrievalService(db)
    return service.query(req.query, req.namespace, req.top_k)


@router.get("/runs", response_model=list[RetrievalRunResponse])
def list_runs(
    namespace: str | None = Query(None),
    min_results: int = Query(0, ge=0, description="Only return runs with at least this many results"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RetrievalRunResponse]:
    """List recent retrieval runs (most recent first)."""
    q = db.query(RetrievalRun)
    if namespace:
        q = q.filter(RetrievalRun.namespace == namespace)
    if min_results > 0:
        q = q.filter(RetrievalRun.result_count >= min_results)

    runs = q.order_by(RetrievalRun.created_at.desc()).offset(offset).limit(limit).all()
    return [
        RetrievalRunResponse(
            id=r.id,
            query=r.query,
            namespace=r.namespace,
            top_k=r.top_k,
            result_count=r.result_count,
            created_at=r.created_at.isoformat(),
        )
        for r in runs
    ]


@router.get("/stats", response_model=RetrievalStatsResponse)
def retrieval_stats(
    namespace: str | None = Query(None, description="Filter by namespace; None = all"),
    last_n: int = Query(500, ge=10, le=10000, description="Analyse the N most recent runs"),
    db: Session = Depends(get_db),
) -> RetrievalStatsResponse:
    """Aggregated analytics over recent retrieval runs.

    Useful for understanding:
    - Which queries return no results (knowledge gaps)
    - Which queries are asked most often (high-priority topics)
    - How result counts are distributed (retrieval quality indicator)
    """
    q = db.query(RetrievalRun)
    if namespace:
        q = q.filter(RetrievalRun.namespace == namespace)
    runs = q.order_by(RetrievalRun.created_at.desc()).limit(last_n).all()

    if not runs:
        return RetrievalStatsResponse(
            total_runs=0,
            avg_result_count=0.0,
            namespaces={},
            zero_result_queries=[],
            top_queries=[],
            result_count_distribution={"0": 0, "1-3": 0, "4-7": 0, "8-10": 0, "10+": 0},
        )

    total = len(runs)
    avg_results = sum(r.result_count for r in runs) / total

    # Namespace distribution
    ns_counts: dict[str, int] = {}
    for r in runs:
        ns_counts[r.namespace] = ns_counts.get(r.namespace, 0) + 1

    # Zero-result queries (knowledge gaps) — most recent 20
    zero_qs = [r.query for r in runs if r.result_count == 0][:20]

    # Most frequent queries (case-insensitive, trimmed)
    query_freq: dict[str, int] = {}
    for r in runs:
        key = r.query.strip().lower()[:120]
        query_freq[key] = query_freq.get(key, 0) + 1
    top_queries = [
        {"query": q, "count": c}
        for q, c in sorted(query_freq.items(), key=lambda x: x[1], reverse=True)[:15]
        if c > 1
    ]

    # Result count distribution
    dist = {"0": 0, "1-3": 0, "4-7": 0, "8-10": 0, "10+": 0}
    for r in runs:
        n = r.result_count
        if n == 0:
            dist["0"] += 1
        elif n <= 3:
            dist["1-3"] += 1
        elif n <= 7:
            dist["4-7"] += 1
        elif n <= 10:
            dist["8-10"] += 1
        else:
            dist["10+"] += 1

    return RetrievalStatsResponse(
        total_runs=total,
        avg_result_count=round(avg_results, 2),
        namespaces=ns_counts,
        zero_result_queries=zero_qs,
        top_queries=top_queries,
        result_count_distribution=dist,
    )
