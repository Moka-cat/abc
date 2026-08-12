"""Knowledge-base document management endpoints.

GET  /documents                        — list documents (paginated, filterable)
GET  /documents/{id}                   — document detail with stats
GET  /documents/{id}/sections          — section tree for a document
GET  /documents/{id}/chunks            — chunks with filtering and pagination
DELETE /documents/{id}                 — remove document and all derived data
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.orm.document import Document, DocumentSection, DocumentChunk
from app.models.schemas.document import DocumentResponse, SectionResponse, ChunkResponse

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _doc_stats(db: Session, doc_id: str) -> tuple[int, int]:
    """Return (chunk_count, section_count) for a document — two fast aggregations."""
    chunk_count = (
        db.query(func.count(DocumentChunk.id))
        .filter(DocumentChunk.document_id == doc_id,
                DocumentChunk.chunk_type != "parent")
        .scalar() or 0
    )
    section_count = (
        db.query(func.count(DocumentSection.id))
        .filter(DocumentSection.document_id == doc_id)
        .scalar() or 0
    )
    return int(chunk_count), int(section_count)


def _to_doc_response(db: Session, doc: Document) -> DocumentResponse:
    chunk_count, section_count = _doc_stats(db, doc.id)
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        source_path=doc.source_path,
        file_type=doc.file_type,
        namespace=doc.namespace,
        status=doc.status,
        error_message=doc.error_message,
        summary=doc.summary,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        chunk_count=chunk_count,
        section_count=section_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[DocumentResponse])
def list_documents(
    namespace: str = Query("default", description="Namespace filter; use '__all__' to list every namespace"),
    status: str | None = Query(None, description="Filter by status: pending|processing|done|failed"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[DocumentResponse]:
    """List ingested documents with stats.

    - `namespace=__all__` returns documents from every namespace.
    - `status` filters by processing status.
    - Supports pagination via `offset` / `limit`.
    """
    q = db.query(Document)
    if namespace != "__all__":
        q = q.filter(Document.namespace == namespace)
    if status:
        q = q.filter(Document.status == status)

    docs = q.order_by(Document.created_at.desc()).offset(offset).limit(limit).all()
    return [_to_doc_response(db, d) for d in docs]


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, db: Session = Depends(get_db)) -> DocumentResponse:
    """Detailed document info including summary and stats."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _to_doc_response(db, doc)


@router.get("/{document_id}/sections", response_model=list[SectionResponse])
def list_sections(
    document_id: str,
    db: Session = Depends(get_db),
) -> list[SectionResponse]:
    """Return the complete section tree for a document (ordered by section_path)."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    sections = (
        db.query(DocumentSection)
        .filter(DocumentSection.document_id == document_id)
        .order_by(DocumentSection.section_path)
        .all()
    )

    # Attach per-section chunk counts (excluding parent chunks)
    result: list[SectionResponse] = []
    for sec in sections:
        chunk_count = (
            db.query(func.count(DocumentChunk.id))
            .filter(DocumentChunk.section_id == sec.id,
                    DocumentChunk.chunk_type != "parent")
            .scalar() or 0
        )
        result.append(SectionResponse(
            id=sec.id,
            document_id=sec.document_id,
            parent_id=sec.parent_id,
            section_path=sec.section_path,
            level=sec.level,
            title=sec.title,
            page_start=sec.page_start,
            page_end=sec.page_end,
            chunk_count=int(chunk_count),
        ))
    return result


@router.get("/{document_id}/chunks", response_model=list[ChunkResponse])
def list_chunks(
    document_id: str,
    chunk_type: str | None = Query(None, description="Filter: text|table|figure|formula|parent"),
    section_path: str | None = Query(None, description="Filter by section_path prefix"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ChunkResponse]:
    """List chunks for a document with optional type and section filters."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    q = db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id)
    if chunk_type:
        q = q.filter(DocumentChunk.chunk_type == chunk_type)
    if section_path:
        q = q.filter(DocumentChunk.section_path.startswith(section_path))

    chunks = q.order_by(DocumentChunk.section_path, DocumentChunk.chunk_index).offset(offset).limit(limit).all()

    return [
        ChunkResponse(
            id=c.id,
            document_id=c.document_id,
            section_id=c.section_id,
            chunk_type=c.chunk_type,
            chunk_text=c.chunk_text,
            chunk_index=c.chunk_index,
            section_path=c.section_path,
            page_start=c.page_start,
            page_end=c.page_end,
            parent_chunk_id=c.parent_chunk_id,
            retrieval_hit_count=c.retrieval_hit_count or 0,
            has_embedding=c.embedding is not None,
        )
        for c in chunks
    ]


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, db: Session = Depends(get_db)) -> None:
    """Delete a document and all its derived data (sections, chunks, assets, entities).

    Cascade is handled by ORM relationships (all, delete-orphan).
    Also invalidates the embedding cache so deleted chunks are excluded
    from future retrieval.
    """
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    db.delete(doc)
    db.commit()

    # Invalidate the in-process embedding cache
    try:
        from app.services.retrieval import invalidate_embedding_cache
        invalidate_embedding_cache()
    except Exception:
        pass  # non-fatal
