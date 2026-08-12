from pathlib import Path
from .base import BaseParser, ParseResult, TextBlock


class TextParser(BaseParser):
    supported_extensions = [".txt"]

    def parse(self, path: Path) -> ParseResult:
        text = path.read_text(encoding="utf-8")
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        blocks = [TextBlock(text=p, block_type="paragraph", raw=p) for p in paragraphs]
        return ParseResult(blocks=blocks, source_path=str(path), file_type="txt")
