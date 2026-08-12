import pytest
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService


def test_retrieval_finds_result(db, sample_md_file):
    ingest = IngestionService(db)
    doc = ingest.ingest_local_file(str(sample_md_file), namespace="test")
    assert doc.status == "done"

    retrieval = RetrievalService(db)
    result = retrieval.query("HTPB AP burning rate", namespace="test", top_k=5)

    assert len(result.results) > 0
    assert result.evidence_text


def test_retrieval_returns_section_path(db, sample_md_file):
    ingest = IngestionService(db)
    ingest.ingest_local_file(str(sample_md_file), namespace="test2")

    retrieval = RetrievalService(db)
    result = retrieval.query("burning rate", namespace="test2")

    for r in result.results:
        assert r.section_path is not None


def test_retrieval_run_recorded(db, sample_md_file):
    from app.models.orm.retrieval import RetrievalRun

    ingest = IngestionService(db)
    ingest.ingest_local_file(str(sample_md_file), namespace="test3")

    retrieval = RetrievalService(db)
    result = retrieval.query("RDX density", namespace="test3")

    run = db.query(RetrievalRun).filter_by(id=result.run_id).first()
    assert run is not None
    assert run.query == "RDX density"
