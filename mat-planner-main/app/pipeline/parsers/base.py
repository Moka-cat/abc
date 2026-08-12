from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TextBlock:
    """解析器输出的基本单元"""
    text: str
    block_type: str = "paragraph"  # heading, paragraph, table, figure, code, formula
    level: int = 0                  # heading level（仅 block_type=heading 时有效）
    page: int | None = None
    raw: str = ""                   # 原始内容


@dataclass
class ParseResult:
    """解析器输出"""
    blocks: list[TextBlock] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    source_path: str = ""
    file_type: str = ""


class BaseParser:
    """所有解析器的基类"""
    supported_extensions: list[str] = []

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.supported_extensions

    def parse(self, path: Path) -> ParseResult:
        raise NotImplementedError
