"""API endpoint tests.

Tests are split into two groups:
  1. Job submission / polling — tests the async job API shape (POST /jobs, GET /jobs/{id})
  2. Data API tests — use IngestionService directly (same test session as the client)
     to avoid cross-session isolation issues with background tasks.
"""
import pytest
from app.services.ingestion import IngestionService


# ── Health ──────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Async job API ────────────────────────────────────────────────────────────

def test_ingest_job_submit_returns_202(client, sample_md_file):
    """POST /jobs returns 202 immediately with job_id."""
    r = client.post("/jobs", json={"path": str(sample_md_file), "namespace": "api_async_test"})
    assert r.status_code == 202
    data = r.json()
    assert "job_id" in data
    assert data["status"] in ("pending", "processing", "done")
    assert data["path"] == str(sample_md_file)


def test_job_status_poll(client, sample_md_file):
    """GET /jobs/{job_id} returns job status (with TestClient, task runs sync so status=done)."""
    r = client.post("/jobs", json={"path": str(sample_md_file), "namespace": "api_poll_test"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status_r = client.get(f"/jobs/{job_id}")
    assert status_r.status_code == 200
    data = status_r.json()
    assert data["job_id"] == job_id
    assert data["status"] in ("pending", "processing", "done", "failed")


def test_job_list(client, sample_md_file):
    """GET /jobs returns list of submitted jobs."""
    client.post("/jobs", json={"path": str(sample_md_file), "namespace": "api_list_jobs_test"})
    r = client.get("/jobs")
    assert r.status_code == 200
    jobs = r.json()
    assert isinstance(jobs, list)
    assert len(jobs) >= 1
    assert "job_id" in jobs[0]


def test_job_not_found(client):
    """GET /jobs/{bad_id} returns 404."""
    r = client.get("/jobs/nonexistent-job-id-xyz")
    assert r.status_code == 404


# ── Data API tests (ingest via IngestionService to share the test session) ──

@pytest.fixture
def ingested_doc(db, sample_md_file):
    """Ingest sample doc directly into the test session (no background tasks)."""
    service = IngestionService(db)
    return service.ingest_local_file(str(sample_md_file), namespace="api_data_test")


def test_list_documents(client, ingested_doc):
    r = client.get("/documents?namespace=api_data_test")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_get_chunks(client, ingested_doc):
    doc_id = ingested_doc.id
    r = client.get(f"/documents/{doc_id}/chunks")
    assert r.status_code == 200
    assert len(r.json()) > 0


def test_retrieval_query(client, ingested_doc):
    r = client.post(
        "/retrieval/query",
        json={"query": "burning rate AP", "namespace": "api_data_test"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert "evidence_text" in data


def test_entity_not_found(client):
    r = client.get("/entities/NONEXISTENT_COMPOUND_XYZ")
    assert r.status_code == 404


def test_list_entities_after_ingest(client, ingested_doc):
    r = client.get("/entities?namespace=api_data_test")
    assert r.status_code == 200
