"""Evidence Memory: EvidenceLink — 每个 PropertyValue 对应原文溯源"""
import uuid
from sqlalchemy import String, Integer, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    property_value_id: Mapped[str] = mapped_column(String(36), ForeignKey("property_values.id", ondelete="CASCADE"))
    chunk_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("document_chunks.id"), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("document_assets.id"), nullable=True)
    document_id: Mapped[str] = mapped_column(String(36))
    section_path: Mapped[str] = mapped_column(String(1024), default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)   # 原文片段（精确匹配句）
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)  # quote 在 chunk 中的起始字符偏移
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)    # quote 结束偏移（exclusive）

    __table_args__ = (
        Index("idx_evidence_property_value_id", "property_value_id"),
        Index("idx_evidence_document_id", "document_id"),
    )
