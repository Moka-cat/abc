"""Formulation / Composition ORM models.

A Formulation represents a specific composition of materials (e.g., a composite
propellant, explosive mixture, or pyrotechnic charge) found in literature.
"""
import uuid
from datetime import datetime
from sqlalchemy import String, Float, Text, ForeignKey, Index, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Formulation(Base):
    __tablename__ = "formulations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)  # e.g. "Composition B", "PBXN-109"
    formulation_type: Mapped[str] = mapped_column(String(64), default="general")
    # Types: composite_propellant, plastic_explosive, melt_cast, binary, pyrotechnic, general
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    namespace: Mapped[str] = mapped_column(String(128), default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    components: Mapped[list["FormulationComponent"]] = relationship(
        back_populates="formulation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_formulations_name", "name"),
        Index("idx_formulations_type", "formulation_type"),
        Index("idx_formulations_doc", "document_id"),
    )


class FormulationComponent(Base):
    __tablename__ = "formulation_components"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    formulation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("formulations.id", ondelete="CASCADE")
    )
    component_name: Mapped[str] = mapped_column(String(256))  # canonical entity name
    mass_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0–100 wt%
    volume_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0–100 vol%
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # roles: oxidizer, binder, fuel, explosive, plasticizer, catalyst, additive

    formulation: Mapped["Formulation"] = relationship(back_populates="components")

    __table_args__ = (
        Index("idx_form_comp_formulation", "formulation_id"),
        Index("idx_form_comp_name", "component_name"),
    )
