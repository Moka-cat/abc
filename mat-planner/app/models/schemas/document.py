from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DocumentCreate(BaseModel):
    title: str
    source_path: str
    file_type: str = "md"
    namespace: str = "default"


class DocumentResponse(BaseModel):
    """Compact document record — returned in list endpoints."""
    id: str
    title: str
    source_path: str
    file_type: str
    namespace: str
    status: str
    error_message: str | None = None
    summary: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # Computed stats (populated by route, not ORM directly)
    chunk_count: int = 0
    section_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class SectionResponse(BaseModel):
    """Document section (one node of the section tree)."""
    id: str
    document_id: str
    parent_id: str | None = None
    section_path: str
    level: int
    title: str
    page_start: int | None = None
    page_end: int | None = None
    chunk_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class ChunkResponse(BaseModel):
    id: str
    document_id: str
    section_id: str | None
    chunk_type: str
    chunk_text: str
    chunk_index: int = 0
    section_path: str
    page_start: int | None
    page_end: int | None
    parent_chunk_id: str | None = None
    retrieval_hit_count: int = 0
    has_embedding: bool = False
    model_config = ConfigDict(from_attributes=True)


class IngestLocalRequest(BaseModel):
    path: str
    namespace: str = "default"
    title: str | None = None
    force: bool = False   # if True, delete and re-ingest even if already present


class JobSubmitResponse(BaseModel):
    """Returned immediately when a job is submitted (202 Accepted)."""
    job_id: str
    status: str
    path: str


class BatchIngestRequest(BaseModel):
    """Submit multiple files or a directory for ingestion in one call.

    Provide either:
    - `paths`: explicit list of file paths, OR
    - `directory`: path to a folder; all matching files are submitted
    - `glob_pattern`: filter for directory scan (default "**/*")
    - `recursive`: scan subdirectories (default True)
    """
    paths: list[str] | None = None
    directory: str | None = None
    glob_pattern: str = "**/*"
    recursive: bool = True
    namespace: str = "default"


class BatchIngestResponse(BaseModel):
    """Summary of jobs submitted in a batch."""
    submitted: int
    skipped: int
    jobs: list[JobSubmitResponse]
    skipped_paths: list[str]


class JobStatusResponse(BaseModel):
    """Returned when polling GET /jobs/{job_id}."""
    job_id: str
    status: str   # pending | processing | done | failed
    path: str
    namespace: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    document_id: str | None = None
    error: str | None = None
    progress: int = 0
    message: str | None = None
