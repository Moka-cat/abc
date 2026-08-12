from pydantic import BaseModel


class EntityPropertyResponse(BaseModel):
    property: str
    value_text: str | None
    value_numeric: float | None
    unit: str | None
    condition: dict | None
    evidence_id: str | None


class EntityCardResponse(BaseModel):
    id: str
    canonical_name: str
    entity_type: str
    aliases: list[str]
    properties: list[EntityPropertyResponse]


class EvidenceResponse(BaseModel):
    id: str
    document_id: str
    section_path: str
    page: int | None
    chunk_id: str | None
    quote: str | None                  # 精确匹配句（原文）
    span_start: int | None = None      # quote 在 chunk 中的字符起始偏移
    span_end: int | None = None        # 字符结束偏移（exclusive）
    context_before: str = ""           # quote 前一句
    context_after: str = ""            # quote 后一句
    highlighted: str = ""              # markdown: …前文 **[quote]** 后文…
