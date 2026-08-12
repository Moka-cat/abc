from pydantic import BaseModel


class RetrievalQueryRequest(BaseModel):
    query: str
    namespace: str = "default"
    top_k: int = 10
    channels: list[str] = ["content", "term"]


class RetrievalResult(BaseModel):
    document_id: str
    document_title: str = ""
    chunk_id: str
    section_path: str
    page_start: int | None
    page_end: int | None
    text: str           # matched child chunk text (for display / evidence quote)
    context: str = ""  # parent section text fed to LLM (empty for legacy flat chunks)
    score: float


class RetrievalQueryResponse(BaseModel):
    query: str
    namespace: str
    results: list[RetrievalResult]
    evidence_text: str
    run_id: str
