"""Instant data extraction endpoint.

POST /extract/file   — upload a PDF or JSON file, returns structured extraction immediately
                       (synchronous, no DB write, no background task)

Extracted fields:
  entities    — list of {name, type, properties: [{property, value, unit, condition, quote, confidence}]}
  tables      — list of {caption, rows: [[str, ...]], headers: [str, ...]}
  text_stats  — {char_count, entity_count, property_count, table_count}
  preview     — first ~800 chars of extracted text
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

# ── Response schemas ──────────────────────────────────────────────────────────

class ExtractedPropertyOut(BaseModel):
    property:   str
    value:      str | float | None = None
    unit:       str | None = None
    condition:  dict | None = None
    quote:      str | None = None
    confidence: float | None = None   # 0–1; None = unknown

class ExtractedEntityOut(BaseModel):
    name:        str
    entity_type: str
    aliases:     list[str] = []
    properties:  list[ExtractedPropertyOut] = []

class ExtractedTableOut(BaseModel):
    caption: str = ""
    headers: list[str] = []
    rows:    list[list[str]] = []

class ExtractionResult(BaseModel):
    filename:   str
    file_type:  str
    entities:   list[ExtractedEntityOut]
    tables:     list[ExtractedTableOut]
    preview:    str
    text_stats: dict[str, int]

# ── Table helpers ─────────────────────────────────────────────────────────────

def _parse_pipe_table(text: str) -> list[list[str]]:
    """Convert pipe-delimited text lines into a list of rows (separator lines removed)."""
    rows: list[list[str]] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Separator line: only pipes, dashes, colons, spaces
        if not re.sub(r"[|\-: ]", "", stripped):
            continue
        if "|" in stripped:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if any(cells):
                rows.append(cells)
    return rows


def _row_key(row: list[str]) -> tuple[str, ...]:
    """Normalised tuple used to compare rows (e.g. for header dedup)."""
    return tuple(c.strip().lower() for c in row)


def _parse_table_num(caption: str) -> int | None:
    """Extract the leading table number from a caption like 'Table 6. ...'."""
    m = re.match(r'(?:Table|表)\s*(\d+)', caption, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _is_caption_row(row: list[str]) -> bool:
    """True if the row looks like a table title rather than a column-header row.

    Heuristics:
    - Only one non-empty cell, OR
    - Cell text matches patterns like "表1", "Table 1.", "TABLE I" etc.
    """
    non_empty = [c for c in row if c.strip()]
    if len(non_empty) == 1:
        return True
    text = " ".join(non_empty)
    return bool(re.match(r"^(表|table|tab\.?)\s*[\dIVXivx]+", text, re.IGNORECASE))


def _merge_table_segments(
    segments: list[tuple[str, list[str], list[list[str]]]]
) -> list[ExtractedTableOut]:
    """Merge table segments that share the same column header signature.

    Each segment is (caption, headers, data_rows).
    Segments with identical normalised headers are concatenated in order.
    """
    # Ordered dict: header_key → (caption, headers, accumulated_rows)
    merged: dict[tuple[str, ...], tuple[str, list[str], list[list[str]]]] = {}
    order: list[tuple[str, ...]] = []

    for caption, headers, data_rows in segments:
        key = _row_key(headers)
        if key in merged:
            # Append rows to existing table
            merged[key][2].extend(data_rows)
            # Keep first non-empty caption
            if not merged[key][0] and caption:
                merged[key] = (caption, merged[key][1], merged[key][2])
        else:
            merged[key] = (caption, headers, list(data_rows))
            order.append(key)

    result: list[ExtractedTableOut] = []
    for key in order:
        caption, headers, rows = merged[key]
        if rows:  # skip empty tables
            result.append(ExtractedTableOut(caption=caption, headers=headers, rows=rows))
    return result


def _parse_block_as_table(
    text: str,
) -> tuple[str, list[str], list[list[str]]] | None:
    """Try to interpret a text block as a table.

    Returns (caption, headers, data_rows) or None if the block has fewer than
    2 pipe-delimited rows after stripping.
    """
    rows = _parse_pipe_table(text)
    if not rows:
        return None

    caption = ""
    # Detect optional caption row at the top
    if _is_caption_row(rows[0]):
        caption = " ".join(c for c in rows[0] if c.strip())
        rows = rows[1:]

    if not rows:
        return None

    headers   = rows[0]
    data_rows = rows[1:]

    header_key = _row_key(headers)
    # Drop repeated header rows (same content as header — common in multi-page tables)
    data_rows = [r for r in data_rows if _row_key(r) != header_key]

    # Normalise column count: pad short rows, trim long rows
    ncols = len(headers)
    normalised: list[list[str]] = []
    for row in data_rows:
        if len(row) < ncols:
            row = row + [""] * (ncols - len(row))
        elif len(row) > ncols:
            row = row[:ncols]
        normalised.append(row)

    return caption, headers, normalised


# ── PDF extraction ────────────────────────────────────────────────────────────

def _extract_from_pdf(path: Path) -> tuple[str, list[ExtractedTableOut]]:
    """Parse PDF → (full_text, tables).

    Strategy:
      1. Parse all blocks via PdfParser.
      2. For every block that looks like a pipe-table, extract
         (caption, headers, data_rows).
      3. After processing all blocks, merge segments that share the same
         column headers — this handles tables split across pages regardless
         of whether non-table blocks appear between them.
    """
    from app.pipeline.parsers.pdf import PdfParser  # type: ignore[import]
    result = PdfParser().parse(path)

    texts:    list[str] = []
    segments: list[tuple[str, list[str], list[list[str]]]] = []

    for block in result.blocks:
        texts.append(block.text)
        if block.text.count("|") > 3:
            parsed = _parse_block_as_table(block.text)
            if parsed is not None:
                segments.append(parsed)

    tables = _merge_table_segments(segments)
    return "\n".join(texts), tables


# ── JSON extraction ───────────────────────────────────────────────────────────

def _html_table_to_text_rows(html: str) -> list[list[str]]:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    result = []
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        clean = [re.sub(r"<[^>]+>", " ", c).strip() for c in cells]
        clean = [re.sub(r"\s+", " ", c) for c in clean]
        if any(clean):
            result.append(clean)
    return result


def _flatten_json(obj: Any, depth: int = 0, max_depth: int = 5) -> str:
    if depth > max_depth:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float)):
        return str(obj)
    if isinstance(obj, list):
        return "\n".join(_flatten_json(v, depth + 1) for v in obj[:50])
    if isinstance(obj, dict):
        parts = []
        for k, v in obj.items():
            flat_v = _flatten_json(v, depth + 1)
            if flat_v.strip():
                parts.append(f"{k}: {flat_v}")
        return "\n".join(parts)
    return str(obj)


def _detect_json_tables(obj: Any, parent_key: str = "") -> list[ExtractedTableOut]:
    tables: list[ExtractedTableOut] = []
    if isinstance(obj, list) and len(obj) >= 2:
        if all(isinstance(row, dict) for row in obj[:5]):
            headers = list(obj[0].keys())
            rows = [[str(row.get(h, "")) for h in headers] for row in obj[:100]]
            tables.append(ExtractedTableOut(caption=parent_key or "数据表", headers=headers, rows=rows[1:]))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            tables.extend(_detect_json_tables(v, k))
    return tables


def _extract_from_json(path: Path) -> tuple[str, list[ExtractedTableOut]]:
    """Parse JSON → (flat_text, tables).

    Handles:
      - MinerU format (content_list with type/text/table items)
      - Generic JSON: flatten all strings + detect array-of-dicts tables

    Merging rule (MinerU):
      A table item with an empty caption is treated as a continuation of the
      most recently seen table with the same column count.  This fixes two
      common MinerU artefacts:
        1. A large table split into consecutive HTML <table> elements.
        2. A page-spanning table whose second half loses its caption.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    tables: list[ExtractedTableOut] = []
    texts:  list[str] = []

    content_list = data.get("content_list") if isinstance(data, dict) else None
    if content_list and isinstance(content_list, list):
        # ── Pre-scan: build table-number → title map from ALL text blocks ───────
        # MinerU sometimes captures table captions as plain text (e.g. from TOC)
        # rather than as table_caption fields.  We collect every "Table N. ..."
        # occurrence here and use it to fill missing captions via look-back.
        _tbl_title_map: dict[int, str] = {}
        _tbl_ref_pat = re.compile(
            r'(?:^|[\s(])Table\s+(\d+)[.\s]\s*([A-Z][^\n]{5,120})',
            re.IGNORECASE,
        )
        # Also build a per-index map so we can look back from a table's position
        _text_by_cl_idx: dict[int, str] = {}
        for _ci, _item in enumerate(content_list):
            if _item.get("type") == "text":
                _txt = _item.get("text", "")
                _text_by_cl_idx[_ci] = _txt
                for _m in _tbl_ref_pat.finditer(_txt):
                    _num = int(_m.group(1))
                    _title = re.sub(r'\s+\d+\s*$', '', _m.group(2)).strip()
                    if _num not in _tbl_title_map and len(_title) > 5:
                        _tbl_title_map[_num] = _title

        def _infer_caption_lookback(start_idx: int, lookback: int = 30) -> str:
            """Search backward from start_idx for the nearest 'Table N.' reference
            that hasn't already been assigned to another table."""
            assigned = {_parse_table_num(tbl.caption) for tbl in tables}
            for i in range(start_idx - 1, max(-1, start_idx - lookback - 1), -1):
                txt = _text_by_cl_idx.get(i, "")
                for m in _tbl_ref_pat.finditer(txt):
                    num = int(m.group(1))
                    if num not in assigned and num in _tbl_title_map:
                        return f"Table {num}. {_tbl_title_map[num]}"
            return ""

        # ── Single pass: parse and merge only adjacent table items ────────────
        prev_cl_idx: int | None = None   # content_list position of last table
        last_by_cols: dict[int, int] = {}  # col_count → index in `tables`

        for cl_idx, item in enumerate(content_list):
            t = item.get("type")
            if t == "text":
                txt = item.get("text", "")
                if txt:
                    texts.append(txt)
                continue
            if t != "table":
                continue

            html = item.get("table_body", "")
            caption_raw = item.get("table_caption", [])
            if isinstance(caption_raw, list):
                caption = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in caption_raw
                ).strip()
            else:
                caption = str(caption_raw).strip()

            rows = _html_table_to_text_rows(html)
            if not rows:
                prev_cl_idx = cl_idx
                continue

            # Promote first-row table title to caption
            if _is_caption_row(rows[0]):
                if not caption:
                    caption = " ".join(c for c in rows[0] if c.strip())
                rows = rows[1:]
            if not rows:
                prev_cl_idx = cl_idx
                continue

            headers   = rows[0]
            data_rows = rows[1:]
            ncols     = len(headers)

            # Merge only if immediately adjacent AND no caption
            is_adjacent = (prev_cl_idx is not None and cl_idx == prev_cl_idx + 1)
            if not caption and is_adjacent and ncols in last_by_cols:
                target = tables[last_by_cols[ncols]]
                if _row_key(headers) != _row_key(target.headers):
                    target.rows.append(headers)
                target.rows.extend(data_rows)
                prev_cl_idx = cl_idx
                continue

            # Infer caption by looking back through preceding text blocks
            if not caption:
                caption = _infer_caption_lookback(cl_idx)

            last_by_cols[ncols] = len(tables)
            tables.append(ExtractedTableOut(
                caption=caption,
                headers=headers,
                rows=data_rows,
            ))
            prev_cl_idx = cl_idx

        title    = data.get("title", "")
        abstract = data.get("abstract", "")
        if title:    texts.insert(0, f"# {title}")
        if abstract: texts.insert(1, abstract)
    else:
        texts.append(_flatten_json(data))
        tables.extend(_detect_json_tables(data))

    return "\n\n".join(t for t in texts if t.strip()), tables


# ── LLM NER ───────────────────────────────────────────────────────────────────

def _run_llm_ner(text: str) -> list[ExtractedEntityOut]:
    """Run LLM NER on the combined text, return structured entities with confidence."""
    from app.pipeline.llm_ner import llm_extract_properties  # type: ignore[import]

    # Split into 3000-char chunks; process up to 3 chunks
    trimmed = text[:9000]
    chunks = [trimmed[i:i+3000] for i in range(0, len(trimmed), 3000)][:3]

    raw_props = []
    for i, chunk in enumerate(chunks):
        try:
            props = llm_extract_properties(chunk_text=chunk, chunk_id=f"extract_{i}")
            raw_props.extend(props)
        except Exception as exc:
            logger.warning(f"[extract] LLM NER chunk {i} failed: {exc}")

    # Group LLM-extracted properties by entity name
    # Confidence = 0.90 for LLM hits (passed entity validation + plausibility gate)
    entity_map: dict[str, list[ExtractedPropertyOut]] = {}
    for p in raw_props:
        name = p.entity_name
        if name not in entity_map:
            entity_map[name] = []
        entity_map[name].append(ExtractedPropertyOut(
            property=p.property_name,
            value=p.value_numeric if p.value_numeric is not None else p.value_text,
            unit=p.unit or None,
            condition=p.condition,
            quote=p.evidence_quote[:120] if p.evidence_quote else None,
            confidence=0.90,
        ))

    # Run rule-based entity extraction for type information
    from app.pipeline.domain_extractor import extract_entities_from_text  # type: ignore[import]
    rule_entities = extract_entities_from_text(text[:8000])
    entity_type_map = {e.canonical_name: (e.entity_type, e.aliases) for e in rule_entities}

    result: list[ExtractedEntityOut] = []
    for name, props in entity_map.items():
        etype, aliases = entity_type_map.get(name, ("compound", []))
        result.append(ExtractedEntityOut(name=name, entity_type=etype, aliases=aliases, properties=props))

    # Add rule-based-only entities (entity mention found but no numeric properties)
    # Confidence = 0.60 — entity recognised but no measured value confirmed
    seen = {e.name for e in result}
    for e in rule_entities:
        if e.canonical_name not in seen:
            result.append(ExtractedEntityOut(
                name=e.canonical_name,
                entity_type=e.entity_type,
                aliases=e.aliases,
                properties=[],
            ))
            seen.add(e.canonical_name)

    return result


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/file", response_model=ExtractionResult)
async def extract_file(file: UploadFile = File(...)) -> ExtractionResult:
    """Synchronously extract entities, properties, and tables from a PDF or JSON file."""
    filename = file.filename or "unknown"
    suffix   = Path(filename).suffix.lower()

    if suffix not in {".pdf", ".json"}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Only .pdf and .json are supported.",
        )

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        logger.info(f"[extract] processing {filename} ({len(content)} bytes)")

        if suffix == ".pdf":
            text, tables = _extract_from_pdf(tmp_path)
            file_type = "pdf"
        else:
            text, tables = _extract_from_json(tmp_path)
            file_type = "json"

        logger.info(f"[extract] text={len(text)} chars, tables={len(tables)}")

        entities   = _run_llm_ner(text)
        prop_count = sum(len(e.properties) for e in entities)
        logger.info(f"[extract] entities={len(entities)}, props={prop_count}")

        return ExtractionResult(
            filename=filename,
            file_type=file_type,
            entities=entities,
            tables=tables,
            preview=text[:800],
            text_stats={
                "char_count":    len(text),
                "entity_count":  len(entities),
                "property_count": prop_count,
                "table_count":   len(tables),
            },
        )
    finally:
        tmp_path.unlink(missing_ok=True)
