"""Pipeline base types for the document ingestion pipeline.

Pattern (inspired by knowhere's worker/services/document_parser):
  - PipelineContext: shared dataclass passed through all stages
  - ExtractionStage: ABC for per-chunk extraction steps
  - ExtractionPipeline: ordered list of stages, runs them all on each chunk

Adding a new extraction step:
  1. Create a class inheriting ExtractionStage
  2. Implement extract(ctx) → None  (mutates ctx or writes to DB)
  3. Add an instance to ExtractionPipeline._stages
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.orm.document import DocumentChunk, DocumentSection
    from app.pipeline.chunk_builder import Chunk


@dataclass
class ExtractionContext:
    """All shared state needed by extraction stages for one chunk."""
    chunk: "Chunk"                          # pipeline Chunk (pre-ORM)
    chunk_orm: "DocumentChunk | None"       # flushed ORM object (has .id)
    section_orm: "DocumentSection | None"
    document_id: str
    namespace: str
    db: "Session"


class ExtractionStage(ABC):
    """Base class for a single extraction step within the ingestion pipeline."""

    @abstractmethod
    def extract(self, ctx: ExtractionContext) -> None:
        """Process one chunk. Write results directly to ctx.db (flush, not commit)."""
        ...
