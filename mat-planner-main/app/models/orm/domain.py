"""Domain Memory: DomainEntity / PropertyValue / EntityAlias"""
import uuid
from datetime import datetime
from sqlalchemy import String, Float, Text, ForeignKey, Index, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class DomainEntity(Base):
    __tablename__ = "domain_entities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    canonical_name: Mapped[str] = mapped_column(String(256), unique=True)
    entity_type: Mapped[str] = mapped_column(String(64))  # compound, propellant, ingredient, material
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    namespace: Mapped[str] = mapped_column(String(128), default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    aliases: Mapped[list["EntityAlias"]] = relationship(back_populates="entity", cascade="all, delete-orphan")
    properties: Mapped[list["PropertyValue"]] = relationship(back_populates="entity", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_entities_canonical_name", "canonical_name"),
        Index("idx_entities_type", "entity_type"),
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(String(36), ForeignKey("domain_entities.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(256))

    entity: Mapped["DomainEntity"] = relationship(back_populates="aliases")

    __table_args__ = (
        Index("idx_aliases_alias", "alias"),
    )


class PropertyValue(Base):
    __tablename__ = "property_values"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(String(36), ForeignKey("domain_entities.id", ondelete="CASCADE"))
    property_name: Mapped[str] = mapped_column(String(128))   # density, burning_rate, detonation_velocity
    value_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    condition_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    entity: Mapped["DomainEntity"] = relationship(back_populates="properties")

    __table_args__ = (
        Index("idx_propvals_entity_property", "entity_id", "property_name"),
    )
