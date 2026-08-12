"""In-memory + optional DB-backed job store for async ingestion tracking.

SQLite deployments: purely in-memory (jobs lost on restart — acceptable for local use).
PostgreSQL deployments: persists every create/update to `ingestion_jobs` table so jobs
survive server restarts and are visible across multiple process instances.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC

from loguru import logger

from app.core.config import settings


class JobStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class IngestionJob:
    job_id: str
    path: str
    namespace: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    document_id: str | None = None
    error: str | None = None
    progress: int = 0          # 0-100
    message: str | None = None  # human-readable stage description


class JobStore:
    """Thread-safe job store.

    When running on PostgreSQL, every mutation is also written to the
    `ingestion_jobs` DB table via a short-lived SessionLocal() session.
    Reads always go to the in-memory dict for speed.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, IngestionJob] = {}
        self._lock = threading.Lock()
        self._use_db = settings.is_postgres
        if self._use_db:
            self._load_from_db()

    # ── DB helpers ────────────────────────────────────────────────

    def _load_from_db(self) -> None:
        """Warm in-memory cache from DB on startup (PostgreSQL only)."""
        try:
            from app.core.database import SessionLocal
            from app.models.orm.job import IngestionJobRecord
            db = SessionLocal()
            try:
                records = db.query(IngestionJobRecord).all()
                for r in records:
                    self._jobs[r.job_id] = IngestionJob(
                        job_id=r.job_id,
                        path=r.path,
                        namespace=r.namespace,
                        title=r.title,
                        status=r.status,
                        created_at=r.created_at,
                        updated_at=r.updated_at,
                        document_id=r.document_id,
                        error=r.error,
                    )
                logger.info(f"[JobStore] Loaded {len(records)} jobs from DB")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[JobStore] Could not load jobs from DB (table may not exist yet): {e}")

    def _upsert_db(self, job: IngestionJob) -> None:
        """Write job state to DB (PostgreSQL only). Non-fatal on error."""
        if not self._use_db:
            return
        try:
            from app.core.database import SessionLocal
            from app.models.orm.job import IngestionJobRecord
            db = SessionLocal()
            try:
                record = db.get(IngestionJobRecord, job.job_id)
                if record is None:
                    record = IngestionJobRecord(
                        job_id=job.job_id,
                        path=job.path,
                        namespace=job.namespace,
                        title=job.title,
                        status=job.status,
                        created_at=job.created_at,
                        updated_at=job.updated_at,
                        document_id=job.document_id,
                        error=job.error,
                    )
                    db.add(record)
                else:
                    record.status = job.status
                    record.updated_at = job.updated_at
                    record.document_id = job.document_id
                    record.error = job.error
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[JobStore] DB upsert failed (non-fatal): {e}")

    # ── Public API ────────────────────────────────────────────────

    def create(self, path: str, namespace: str, title: str | None) -> IngestionJob:
        now = datetime.now(UTC)
        job = IngestionJob(
            job_id=str(uuid.uuid4()),
            path=path,
            namespace=namespace,
            title=title,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._upsert_db(job)
        return job

    def get(self, job_id: str) -> IngestionJob | None:
        return self._jobs.get(job_id)

    def list_all(self) -> list[IngestionJob]:
        return list(self._jobs.values())

    def update(self, job_id: str, **kwargs) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                kwargs["updated_at"] = datetime.now(UTC)
                for k, v in kwargs.items():
                    setattr(job, k, v)
        if job := self._jobs.get(job_id):
            self._upsert_db(job)


# Module-level singleton shared across all requests
job_store = JobStore()
