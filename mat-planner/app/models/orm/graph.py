"""Light Graph Memory: MemoryGraphNode / MemoryGraphEdge"""
import uuid
from sqlalchemy import String, Float, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class MemoryGraphNode(Base):
    __tablename__ = "memory_graph_nodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    node_type: Mapped[str] = mapped_column(String(64))   # Paper, Section, Chunk, Table, Compound, Propellant, ...
    ref_id: Mapped[str | None] = mapped_column(String(36), nullable=True)   # FK to relevant table
    ref_table: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str] = mapped_column(String(512))
    properties_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_graph_nodes_type", "node_type"),
        Index("idx_graph_nodes_ref", "ref_id", "ref_table"),
    )


class MemoryGraphEdge(Base):
    __tablename__ = "memory_graph_edges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("memory_graph_nodes.id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(String(36), ForeignKey("memory_graph_nodes.id", ondelete="CASCADE"))
    edge_type: Mapped[str] = mapped_column(String(64))   # CONTAINS, MENTIONS, HAS_PROPERTY, SUPPORTED_BY, ...
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    __table_args__ = (
        Index("idx_graph_edges_source", "source_id"),
        Index("idx_graph_edges_target", "target_id"),
        Index("idx_graph_edges_type", "edge_type"),
    )
