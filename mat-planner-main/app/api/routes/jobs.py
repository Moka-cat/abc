"""Ingestion job endpoints.

POST /jobs          → submit a new ingestion job, returns immediately (202)
POST /jobs/batch    → submit multiple files or a directory (202)
GET  /jobs          → list all jobs
GET  /jobs/{id}     → poll job status
WS   /jobs/{id}/ws  → real-time progress stream (WebSocket)
"""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger

from app.core.database import SessionLocal
from app.models.schemas.document import (
    BatchIngestRequest,
    BatchIngestResponse,
    IngestLocalRequest,
    JobSubmitResponse,
    JobStatusResponse,
)
from app.services.ingestion import IngestionService
from app.services.job_store import job_store, JobStatus

router = APIRouter()

_TERMINAL_STATUSES = {JobStatus.DONE, JobStatus.FAILED}


# ── Background worker ─────────────────────────────────────────────────────────

def _run_ingestion(
    job_id: str, path: str, namespace: str, title: str | None, force: bool = False
) -> None:
    """Runs in a background thread — creates its own DB session."""
    job_store.update(job_id, status=JobStatus.PROCESSING, message="Starting…", progress=0)
    db = SessionLocal()
    try:
        service = IngestionService(db, job_id=job_id)
        doc = service.ingest_local_file(path, namespace, title, force=force)
        job_store.update(job_id, status=JobStatus.DONE, document_id=doc.id, progress=100, message="Ingestion complete")
        logger.info(f"[job:{job_id[:8]}] Done — document {doc.id}")
    except FileNotFoundError as e:
        job_store.update(job_id, status=JobStatus.FAILED, error=str(e), message=str(e))
        logger.warning(f"[job:{job_id[:8]}] Failed (file not found): {e}")
    except Exception as e:
        job_store.update(job_id, status=JobStatus.FAILED, error=str(e), message=str(e))
        logger.error(f"[job:{job_id[:8]}] Failed: {e}")
    finally:
        db.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=JobSubmitResponse, status_code=202)
async def submit_ingest_job(
    req: IngestLocalRequest,
    background_tasks: BackgroundTasks,
) -> JobSubmitResponse:
    """Submit an ingestion job. Returns immediately; poll GET /jobs/{job_id} for status."""
    job = job_store.create(req.path, req.namespace, req.title)
    background_tasks.add_task(
        _run_ingestion, job.job_id, req.path, req.namespace, req.title, req.force
    )
    logger.info(f"[job:{job.job_id[:8]}] Queued ingestion for {req.path!r}")
    return JobSubmitResponse(job_id=job.job_id, status=job.status, path=job.path)


_SUPPORTED_EXTENSIONS = {
    ".pdf", ".md", ".txt", ".docx", ".rst", ".tex", ".html", ".htm",
}


@router.post("/batch", response_model=BatchIngestResponse, status_code=202)
async def batch_ingest(
    req: BatchIngestRequest,
    background_tasks: BackgroundTasks,
) -> BatchIngestResponse:
    """Submit multiple files for ingestion in one call.

    Two modes:
    - `paths`: explicit list of file paths (each file submitted as a separate job)
    - `directory` + optional `glob_pattern` + `recursive`: scan a folder and
      submit every file whose extension is in the supported set
      (.pdf .md .txt .docx .rst .tex .html)

    Already-ingested files (same path + namespace in the DB) are detected at
    job execution time and skipped automatically. The endpoint does NOT dedup
    here to avoid a DB round-trip per file — the IngestionService handles it.

    Returns the list of submitted JobSubmitResponse objects and a count of
    paths that were skipped (not a supported file type or not found).
    """
    # ── Collect candidate paths ───────────────────────────────────────
    candidates: list[Path] = []
    skipped_paths: list[str] = []

    if req.paths:
        for raw in req.paths:
            p = Path(raw)
            if not p.exists():
                skipped_paths.append(f"{raw} (not found)")
            elif p.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                skipped_paths.append(f"{raw} (unsupported type: {p.suffix})")
            else:
                candidates.append(p)

    if req.directory:
        d = Path(req.directory)
        if not d.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"directory {req.directory!r} does not exist or is not a directory",
            )
        pattern = req.glob_pattern or "**/*"
        all_files = list(d.glob(pattern)) if req.recursive else list(d.glob(pattern.lstrip("*/")))
        for p in sorted(all_files):
            if not p.is_file():
                continue
            if p.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                continue
            candidates.append(p)

    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="No supported files found. Check paths/directory and ensure files have supported extensions.",
        )

    # Deduplicate paths (in case paths and directory overlap)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in candidates:
        resolved = str(p.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(p)

    # ── Submit one job per file ───────────────────────────────────────
    submitted_jobs: list[JobSubmitResponse] = []
    for p in unique:
        job = job_store.create(str(p), req.namespace, None)
        background_tasks.add_task(
            _run_ingestion, job.job_id, str(p), req.namespace, None
        )
        submitted_jobs.append(
            JobSubmitResponse(job_id=job.job_id, status=job.status, path=str(p))
        )
        logger.info(f"[batch] Queued {p.name!r} as job {job.job_id[:8]}")

    logger.info(
        f"[batch] Submitted {len(submitted_jobs)} jobs, skipped {len(skipped_paths)} paths "
        f"(namespace={req.namespace!r})"
    )
    return BatchIngestResponse(
        submitted=len(submitted_jobs),
        skipped=len(skipped_paths),
        jobs=submitted_jobs,
        skipped_paths=skipped_paths,
    )


@router.get("", response_model=list[JobStatusResponse])
def list_jobs() -> list[JobStatusResponse]:
    """List all submitted ingestion jobs (most recent first)."""
    jobs = sorted(job_store.list_all(), key=lambda j: j.created_at, reverse=True)
    return [
        JobStatusResponse(
            job_id=j.job_id,
            status=j.status,
            path=j.path,
            namespace=j.namespace,
            title=j.title,
            created_at=j.created_at,
            updated_at=j.updated_at,
            document_id=j.document_id,
            error=j.error,
            progress=j.progress,
            message=j.message,
        )
        for j in jobs
    ]


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str) -> JobStatusResponse:
    """Poll the status of an ingestion job."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        path=job.path,
        namespace=job.namespace,
        title=job.title,
        created_at=job.created_at,
        updated_at=job.updated_at,
        document_id=job.document_id,
        error=job.error,
        progress=job.progress,
        message=job.message,
    )


@router.websocket("/{job_id}/ws")
async def job_progress_ws(websocket: WebSocket, job_id: str) -> None:
    """Stream real-time job progress over WebSocket.

    The server sends a JSON event every 0.5 s:
        {"job_id": "...", "status": "processing", "progress": 42, "message": "Embedding chunks…"}

    On DONE or FAILED the final event is sent and the connection is closed by the server.
    The client may also close the connection at any time.

    Example (browser):
        const ws = new WebSocket("ws://localhost:8000/api/v1/jobs/<id>/ws");
        ws.onmessage = e => console.log(JSON.parse(e.data));
    """
    job = job_store.get(job_id)
    if not job:
        await websocket.close(code=4004, reason=f"Job {job_id!r} not found")
        return

    await websocket.accept()
    try:
        while True:
            job = job_store.get(job_id)
            if not job:
                await websocket.send_text(json.dumps({"error": "job disappeared"}))
                break

            payload = {
                "job_id": job.job_id,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "document_id": job.document_id,
                "error": job.error,
            }
            await websocket.send_text(json.dumps(payload))

            if job.status in _TERMINAL_STATUSES:
                break

            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        logger.debug(f"[job:{job_id[:8]}] WS client disconnected")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
