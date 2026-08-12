"""Hierarchical chunk builder.

For each section, produces:
  • One  "parent"  chunk  — full section text (≤MAX_PARENT_CHARS), chunk_type="parent".
    Not embedded; used only as rich context for the LLM after a child is retrieved.
  • N    "child"   chunks — variable-size splits based on content type:

    block_type  | child strategy
    ------------|---------------------------------------------------------------
    text        | sentence-aware split ≤MAX_CHILD_CHARS (300), overlap at boundaries
    table       | one child per data row (≤ MAX_TABLE_ROW_CHARS=600) for precision
    figure      | single child = full VLM caption (kept whole, usually < 500 chars)
    formula     | single child = full formula (kept whole, < 200 chars typically)
    heading     | merged into adjacent prose (no standalone child)

Each child carries a `parent_key` resolved to parent_chunk_id in ingestion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .section_builder import SectionNode

MAX_PARENT_CHARS = 4000    # section-level context window fed to the LLM
MAX_CHILD_CHARS = 300      # prose retrieval unit
MAX_TABLE_ROW_CHARS = 600  # one table row child (header + data row)
MIN_CHILD_CHARS = 30       # skip trivially short fragments


@dataclass
class Chunk:
    text: str
    chunk_type: str        # "parent" | "text" | "table" | "figure" | "formula"
    section_path: str
    chunk_index: int
    page_start: int | None = None
    page_end: int | None = None
    raw: str = ""
    # Set for children only; value is "<section_path>:parent" (resolved in ingestion)
    parent_key: str | None = None


def _split_prose(text: str, max_chars: int = MAX_CHILD_CHARS) -> list[str]:
    """Split prose text into sentence-aware chunks of ≤ max_chars.

    Splits on ". " or "\\n" boundaries; never cuts mid-sentence.
    Adjacent short fragments are merged until the limit is reached.
    """
    # Split on sentence boundaries: ". ", "! ", "? ", or blank lines
    sentences = re.split(r'(?<=[.!?])\s+|\n{2,}', text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        sent_len = len(sent)
        if current_len + sent_len + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sent)
        current_len += sent_len + 1

    if current:
        chunks.append(" ".join(current))

    return [c for c in chunks if len(c) >= MIN_CHILD_CHARS]


def _split_table_rows(text: str) -> list[str]:
    """Split a pipe-delimited table into per-row chunks with header prepended.

    Each child = "header_row\\n---\\ndata_row" for precise cell-level matching.
    Non-table text is returned as a single chunk.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or "|" not in lines[0]:
        return [text] if len(text) >= MIN_CHILD_CHARS else []

    # First pipe-delimited line = header
    header = lines[0]
    data_lines = [
        ln for ln in lines[1:]
        if "|" in ln and not re.match(r"^\s*[\|:\-]+\s*$", ln)
    ]

    if not data_lines:
        return [text[:MAX_TABLE_ROW_CHARS]] if len(text) >= MIN_CHILD_CHARS else []

    chunks: list[str] = []
    for row in data_lines:
        row_chunk = f"{header}\n{row}"
        # Cap at MAX_TABLE_ROW_CHARS
        if len(row_chunk) > MAX_TABLE_ROW_CHARS:
            row_chunk = row_chunk[:MAX_TABLE_ROW_CHARS]
        if len(row_chunk) >= MIN_CHILD_CHARS:
            chunks.append(row_chunk)

    # If no rows produced valid chunks, fall back to whole table
    return chunks if chunks else ([text[:MAX_TABLE_ROW_CHARS]] if len(text) >= MIN_CHILD_CHARS else [])


def build_chunks(section: SectionNode) -> list[Chunk]:
    """Return [parent_chunk, *child_chunks] for a section.

    The first element is always chunk_type="parent" and contains the full section
    text (capped at MAX_PARENT_CHARS). Children follow with their individual content.
    If the section has no usable text the list may contain only the parent (empty text
    is filtered by ingestion).
    """
    all_prose: list[str] = []   # accumulate for parent text
    child_items: list[tuple[str, str, int | None, str]] = []
    # ^ (text, chunk_type, page, raw)

    for block in section.blocks:
        btype = block.block_type
        text = block.text.strip()
        page = block.page
        raw = getattr(block, "raw", "")

        if btype == "heading":
            # Headings fold into prose for parent; no standalone child
            all_prose.append(text)

        elif btype == "table":
            all_prose.append(text)
            # Row-level children for precise cell matching
            for row_chunk in _split_table_rows(text):
                child_items.append((row_chunk, "table", page, raw))

        elif btype in ("figure", "formula"):
            # Keep whole — VLM captions / formulas are compact and meaningful as units
            all_prose.append(text)
            if len(text) >= MIN_CHILD_CHARS:
                child_items.append((text, btype, page, raw))

        else:
            # prose / paragraph — sentence-aware split
            all_prose.append(text)
            for prose_chunk in _split_prose(text):
                child_items.append((prose_chunk, "text", page, ""))

    # ── Build parent chunk ─────────────────────────────────────────
    parent_text = " ".join(all_prose).strip()[:MAX_PARENT_CHARS]
    parent_key = f"{section.section_path}:parent"

    chunks: list[Chunk] = []
    if parent_text:
        chunks.append(Chunk(
            text=parent_text,
            chunk_type="parent",
            section_path=section.section_path,
            chunk_index=0,
        ))

    # ── Build child chunks ─────────────────────────────────────────
    for idx, (text, ctype, page, raw) in enumerate(child_items, start=1):
        text = text.strip()
        if not text:
            continue
        chunks.append(Chunk(
            text=text,
            chunk_type=ctype,
            section_path=section.section_path,
            chunk_index=idx,
            page_start=page,
            raw=raw,
            parent_key=parent_key,
        ))

    return chunks
