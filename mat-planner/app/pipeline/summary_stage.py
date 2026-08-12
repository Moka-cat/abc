"""ChunkSummaryStage — 为 parent chunk 和 DocumentSection 生成 LLM 摘要与关键词。

设计动机（借鉴 knowhere page_memory）：
  knowhere 在摄取阶段为每页生成 summary + keywords，
  导航时 LLM 读摘要大纲而非全文，大幅降低 token 消耗。

  本模块将同样的思想应用于 mat-planner 的 parent chunk（章节级），
  生成的摘要存储在 DocumentChunk.summary 和 DocumentSection.summary，
  供 AgenticRetrievalService 在导航阶段构建 outline。

只处理 parent chunk（章节级 ~4000字），不处理 leaf chunk，
控制 LLM 调用次数在合理范围内（通常每文档 5-20 次）。
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.orm.document import DocumentChunk, DocumentSection
from app.pipeline.base import ExtractionContext, ExtractionStage
from app.pipeline.domain_extractor import extract_entities_from_text


_SUMMARY_SYSTEM = (
    "You are summarizing a section of an energetic materials research paper.\n"
    "Write 1-2 sentences covering: what substance(s) are discussed, "
    "what property or experiment is described, and any key numerical findings.\n"
    "Be dense and factual. Output ONLY the summary text, no preamble."
)


def _llm_summarize(text: str) -> str | None:
    """Call LLM to generate a section summary. Returns None on failure."""
    if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
        return None
    try:
        from app.services.llm_client import get_llm_client
        client = get_llm_client()
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": text[:3000]},
            ],
            max_tokens=settings.CHUNK_SUMMARY_MAX_TOKENS,
            timeout=12,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as e:
        logger.debug(f"Section summary LLM call failed (non-fatal): {e}")
        return None


def _extract_keywords(text: str) -> str:
    """Extract entity names + simple domain keywords as semicolon-separated string."""
    entities = extract_entities_from_text(text)
    entity_names = [e.canonical_name for e in entities]
    # Add simple domain terms that appear in text (not already in entity names)
    domain_terms = [
        "burning rate", "detonation velocity", "density", "detonation pressure",
        "heat of explosion", "melting point", "sensitivity", "formulation",
        "propellant", "explosive", "oxidizer", "binder", "particle size",
    ]
    extra = [t for t in domain_terms if t in text.lower()]
    all_kw = list(dict.fromkeys(entity_names + extra))  # deduplicate, preserve order
    return ";".join(all_kw[:20])


class ChunkSummaryStage(ExtractionStage):
    """Generate LLM summary and keywords for parent (section-level) chunks.

    Only runs on chunk_type == 'parent'. For each parent chunk:
    1. Generates a short LLM summary (fallback: first 200 chars of text).
    2. Extracts keywords (entity names + domain terms).
    3. Stores both on the DocumentChunk row.
    4. Also propagates the summary to the owning DocumentSection.
    """

    def extract(self, ctx: ExtractionContext) -> None:
        # Only process parent chunks (section-level context, ~4000 chars)
        if ctx.chunk.chunk_type != "parent" or ctx.chunk_orm is None:
            return

        text = ctx.chunk.text
        if not text or len(text) < 50:
            return

        # Generate summary
        if settings.CHUNK_SUMMARY_ENABLED:
            summary = _llm_summarize(text)
            if not summary:
                summary = text[:200].replace("\n", " ").strip()
        else:
            summary = text[:200].replace("\n", " ").strip()

        keywords = _extract_keywords(text)

        # Store on chunk
        ctx.chunk_orm.summary = summary
        ctx.chunk_orm.keywords = keywords
        ctx.db.flush()

        # Propagate summary to DocumentSection (enables navigator to read section outlines)
        if ctx.section_orm is not None and ctx.section_orm.summary is None:
            ctx.section_orm.summary = summary
            ctx.db.flush()

        logger.debug(
            f"Section summary generated: '{ctx.chunk.section_path}' "
            f"({len(summary)} chars, {len(keywords.split(';'))} keywords)"
        )
