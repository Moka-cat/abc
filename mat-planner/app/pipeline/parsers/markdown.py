import re
from pathlib import Path
from .base import BaseParser, ParseResult, TextBlock


class MarkdownParser(BaseParser):
    supported_extensions = [".md", ".markdown"]

    def parse(self, path: Path) -> ParseResult:
        text = path.read_text(encoding="utf-8")
        blocks = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            # heading
            m = re.match(r'^(#{1,6})\s+(.*)', line)
            if m:
                blocks.append(TextBlock(
                    text=m.group(2).strip(),
                    block_type="heading",
                    level=len(m.group(1)),
                    raw=line,
                ))
                i += 1
                continue
            # table（连续 | 行）
            if line.strip().startswith("|"):
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                blocks.append(TextBlock(
                    text="\n".join(table_lines),
                    block_type="table",
                    raw="\n".join(table_lines),
                ))
                continue
            # empty line
            if not line.strip():
                i += 1
                continue
            # paragraph（合并连续非空行）
            para_lines = []
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("#") and not lines[i].strip().startswith("|"):
                para_lines.append(lines[i])
                i += 1
            if para_lines:
                blocks.append(TextBlock(
                    text=" ".join(para_lines).strip(),
                    block_type="paragraph",
                    raw="\n".join(para_lines),
                ))

        return ParseResult(blocks=blocks, source_path=str(path), file_type="md")
