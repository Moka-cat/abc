from dataclasses import dataclass
from .chunk_builder import Chunk


@dataclass
class Asset:
    asset_type: str  # table, figure, formula
    content: str
    caption: str | None
    page: int | None
    section_path: str
    asset_index: int


def extract_assets(chunks: list[Chunk]) -> list[Asset]:
    assets = []
    idx = 0
    for chunk in chunks:
        if chunk.chunk_type in ("table", "figure", "formula"):
            assets.append(Asset(
                asset_type=chunk.chunk_type,
                content=chunk.text,
                caption=None,
                page=chunk.page_start,
                section_path=chunk.section_path,
                asset_index=idx,
            ))
            idx += 1
    return assets
