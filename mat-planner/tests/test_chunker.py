from app.pipeline.parsers.base import TextBlock
from app.pipeline.section_builder import SectionNode
from app.pipeline.chunk_builder import build_chunks


def make_section():
    s = SectionNode(title="Results", level=1, section_path="Results")
    s.blocks = [
        TextBlock(text="Long paragraph. " * 50, block_type="paragraph"),
        TextBlock(text="| Col | Val |\n| --- | --- |\n| A | 1 |", block_type="table"),
        TextBlock(text="Short text.", block_type="paragraph"),
    ]
    return s


def test_table_chunk_type():
    section = make_section()
    chunks = build_chunks(section)
    table_chunks = [c for c in chunks if c.chunk_type == "table"]
    assert len(table_chunks) == 1
    assert "Col" in table_chunks[0].text


def test_section_path_preserved():
    section = make_section()
    chunks = build_chunks(section)
    for chunk in chunks:
        assert chunk.section_path == "Results"


def test_chunk_index_increments():
    section = make_section()
    chunks = build_chunks(section)
    indexes = [c.chunk_index for c in chunks]
    assert indexes == sorted(indexes)
