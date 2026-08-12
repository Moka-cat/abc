"""Task Memory: RetrievalRun / RetrievalStep"""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query: Mapped[str] = mapped_column(Text)
    namespace: Mapped[str] = mapped_column(String(128), default="default")
    top_k: Mapped[int] = mapped_column(Integer, default=10)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    steps: Mapped[list["RetrievalStep"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RetrievalStep(Base):
    __tablename__ = "retrieval_steps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("retrieval_runs.id", ondelete="CASCADE"))
    step_type: Mapped[str] = mapped_column(String(64))   # search, filter, rank, tool_call
    action: Mapped[str] = mapped_column(String(256))
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["RetrievalRun"] = relationship(back_populates="steps")
