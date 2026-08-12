import pytest
from app.services.ingestion import IngestionService
from app.tools import (
    SearchMemoryTool,
    GetEntityCardTool,
    TraceEvidenceTool,
    FindTablesTool,
    CompareValuesTool,
    GetSourceSnippetTool,
)


@pytest.fixture
def ingested_db(db, sample_md_file):
    service = IngestionService(db)
    service.ingest_local_file(str(sample_md_file), namespace="tools_test")
    return db


def test_search_memory_tool(ingested_db):
    tool = SearchMemoryTool(ingested_db)
    result = tool.run(query="burning rate", namespace="tools_test")
    assert result.success
    assert len(result.data["results"]) > 0


def test_get_entity_card_tool(ingested_db):
    tool = GetEntityCardTool(ingested_db)
    result = tool.run(name="RDX")
    assert result.success
    assert result.data["canonical_name"] == "RDX"


def test_get_entity_card_not_found(ingested_db):
    tool = GetEntityCardTool(ingested_db)
    result = tool.run(name="NONEXISTENT_XYZ")
    assert not result.success


def test_trace_evidence_tool(ingested_db):
    from app.models.orm.evidence import EvidenceLink

    ev = ingested_db.query(EvidenceLink).first()
    if ev:
        tool = TraceEvidenceTool(ingested_db)
        result = tool.run(evidence_id=ev.id)
        assert result.success
        assert result.data["document_id"]


def test_find_tables_tool(ingested_db):
    tool = FindTablesTool(ingested_db)
    result = tool.run(query="burning rate AP", namespace="tools_test")
    assert result.success
    assert "tables" in result.data


def test_compare_values_tool(ingested_db):
    tool = CompareValuesTool(ingested_db)
    result = tool.run(property_name="density")
    assert result.success
    assert "comparisons" in result.data


def test_get_source_snippet_tool(ingested_db):
    from app.models.orm.document import DocumentChunk

    chunk = ingested_db.query(DocumentChunk).first()
    if chunk:
        tool = GetSourceSnippetTool(ingested_db)
        result = tool.run(chunk_id=chunk.id)
        assert result.success
        assert result.data["text"]
