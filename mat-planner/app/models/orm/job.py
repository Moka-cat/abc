"""IngestionJobRecord — persists async job state in the DB.

Used only when DATABASE_URL points to PostgreSQL.  On SQLite the in-memory
JobStore is sufficient (and avoids WAL contention from background threads).
"""
import uuid
from datetime import datetime, UTC

from sqlalchemy import String, DateTime, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IngestionJobRecord(Base):
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    path: Mapped[str] = mapped_column(String(1024))
    namespace: Mapped[str] = mapped_column(String(128), default="default")
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_created_at", "created_at"),
    )
