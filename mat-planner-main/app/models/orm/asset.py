"""Asset Memory: Table/Figure/Formula/Caption"""
import uuid
from sqlalchemy import String, Integer, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DocumentAsset(Base):
    __tablename__ = "document_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"))
    section_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("document_sections.id"), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(32))  # table, figure, formula
    content: Mapped[str] = mapped_column(Text)            # raw content (markdown table / caption / formula)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_index: Mapped[int] = mapped_column(Integer, default=0)
    section_path: Mapped[str] = mapped_column(String(1024), default="")

    __table_args__ = (
        Index("idx_assets_document_id", "document_id"),
        Index("idx_assets_type", "asset_type"),
    )
