from pathlib import Path
from .parsers.base import BaseParser, ParseResult
from .parsers.markdown import MarkdownParser
from .parsers.mineru_json import MinerUJsonParser
from .parsers.pdf import PdfParser
from .parsers.text import TextParser

_PARSERS: list[BaseParser] = [
    MarkdownParser(),
    PdfParser(),
    MinerUJsonParser(),
    TextParser(),
]


def get_parser(path: Path) -> BaseParser | None:
    for p in _PARSERS:
        if p.can_parse(path):
            return p
    return None


def parse_file(path: Path) -> ParseResult:
    parser = get_parser(path)
    if parser is None:
        raise ValueError(f"No parser for: {path.suffix}")
    return parser.parse(path)
