"""
Parser for MinerU-parsed JSON papers.

JSON structure:
{
  "content_list": [
    {"type": "text", "text": "...", "text_level": 1|None, "page_idx": 0, ...},
    {"type": "table", "table_body": "<html>...</html>", "table_caption": [...], "page_idx": 1, ...},
    {"type": "image", ...}   ← skipped
  ],
  "title": "...",   # may be empty
  "doi": "...",     # may be empty
  "abstract": "...",
  ...
}

Tables use HTML in `table_body`. We convert them to pipe-delimited text.
"""

import json
import re
from pathlib import Path

from .base import BaseParser, ParseResult, TextBlock


def _html_table_to_text(html: str) -> str:
    """Convert an HTML table string to pipe-delimited plain text."""
    if not html:
        return ""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    lines = []
    for row in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        # Strip inner tags and normalise whitespace
        clean = [re.sub(r"<[^>]+>", " ", c).strip() for c in cells]
        clean = [re.sub(r"\s+", " ", c) for c in clean]
        if any(clean):
            lines.append(" | ".join(clean))
    return "\n".join(lines)


class MinerUJsonParser(BaseParser):
    """Parse MinerU-format JSON papers."""

    supported_extensions = [".json"]

    def parse(self, path: Path) -> ParseResult:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        blocks: list[TextBlock] = []
        metadata: dict = {}

        # Top-level metadata (may be absent / empty)
        for key in ("title", "doi", "abstract", "author", "pub_time", "keyword"):
            val = data.get(key) or ""
            if val:
                metadata[key] = val

        content_list = data.get("content_list") or []

        # Try to derive title from first heading block if absent in metadata
        derived_title = ""

        for block in content_list:
            btype = block.get("type", "")
            page = block.get("page_idx")

            if btype == "text":
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                level = block.get("text_level")

                if level == 1:
                    # Treat as heading
                    if not derived_title and len(text) > 10:
                        derived_title = text
                    blocks.append(
                        TextBlock(
                            text=text,
                            block_type="heading",
                            level=1,
                            page=page,
                            raw=text,
                        )
                    )
                else:
                    blocks.append(
                        TextBlock(
                            text=text,
                            block_type="paragraph",
                            level=0,
                            page=page,
                            raw=text,
                        )
                    )

            elif btype == "table":
                caption_list = block.get("table_caption") or []
                caption = " ".join(str(c) for c in caption_list).strip()
                footnote_list = block.get("table_footnote") or []
                footnote = " ".join(str(c) for c in footnote_list).strip()
                body_html = block.get("table_body") or ""

                table_text = _html_table_to_text(body_html)
                if not table_text and not caption:
                    continue

                full_text = "\n".join(
                    part for part in [caption, table_text, footnote] if part
                )
                blocks.append(
                    TextBlock(
                        text=full_text,
                        block_type="table",
                        level=0,
                        page=page,
                        raw=full_text,
                    )
                )

            # Skip "image" blocks — no usable text

        if not metadata.get("title") and derived_title:
            metadata["title"] = derived_title

        return ParseResult(
            blocks=blocks,
            metadata=metadata,
            source_path=str(path),
            file_type="json",
        )
