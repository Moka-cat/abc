"""Document Memory: Document → Section → Chunk 三层结构"""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, Index, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(512))
    source_path: Mapped[str] = mapped_column(String(1024))
    file_type: Mapped[str] = mapped_column(String(32))   # md, txt, pdf, docx
    namespace: Mapped[str] = mapped_column(String(128), default="default")
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending/processing/done/failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Document-level summary (LLM-generated, ~512 tokens) for cross-document recall
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    sections: Mapped[list["DocumentSection"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentSection(Base):
    __tablename__ = "document_sections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"))
    parent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("document_sections.id"), nullable=True)
    section_path: Mapped[str] = mapped_column(String(1024))  # e.g. "2 / 2.1 / 2.1.1"
    level: Mapped[int] = mapped_column(Integer)              # heading level 1-6
    title: Mapped[str] = mapped_column(String(512))
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # LLM-generated section summary — used by agentic retrieval navigator as outline items
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="sections")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="section")

    __table_args__ = (
        Index("idx_sections_document_id", "document_id"),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"))
    section_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("document_sections.id"), nullable=True)
    # chunk_type: text|table|figure|formula for leaf chunks; "parent" for context-only parent chunks
    chunk_type: Mapped[str] = mapped_column(String(32), default="text")
    chunk_text: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)  # order within section
    section_path: Mapped[str] = mapped_column(String(1024), default="")  # denormalized for search
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Hierarchical chunking: child chunks point to a parent (section-level context chunk)
    parent_chunk_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_chunks.id"), nullable=True
    )
    # bge-m3 float32 vector, 1024-dim × 4 bytes = 4096 bytes per chunk
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Retrieval-hit boosting: incremented each time this chunk is used in an answer
    retrieval_hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Per-chunk memory: LLM-generated summary and extracted keywords
    # Used by agentic navigator for outline display and by retrieval for coarser-grained matching
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)  # semicolon-separated

    document: Mapped["Document"] = relationship(back_populates="chunks")
    section: Mapped["DocumentSection | None"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("idx_chunks_document_id", "document_id"),
        Index("idx_chunks_section_id", "section_id"),
        Index("idx_chunks_parent_chunk_id", "parent_chunk_id"),
    )
