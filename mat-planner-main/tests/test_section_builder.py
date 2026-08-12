from app.pipeline.parsers.base import TextBlock
from app.pipeline.section_builder import build_section_tree, flatten_sections


def make_blocks():
    return [
        TextBlock(text="Abstract", block_type="heading", level=1),
        TextBlock(text="Introduction text.", block_type="paragraph"),
        TextBlock(text="1 Introduction", block_type="heading", level=1),
        TextBlock(text="Body text.", block_type="paragraph"),
        TextBlock(text="1.1 Materials", block_type="heading", level=2),
        TextBlock(text="Materials description.", block_type="paragraph"),
    ]


def test_section_tree_structure():
    blocks = make_blocks()
    roots = build_section_tree(blocks)
    assert len(roots) == 2  # Abstract, 1 Introduction
    intro = roots[1]
    assert len(intro.children) == 1
    assert intro.children[0].title == "1.1 Materials"


def test_section_path():
    blocks = make_blocks()
    roots = build_section_tree(blocks)
    sections_flat = flatten_sections(roots)
    paths = [s.section_path for s in sections_flat]
    assert "1 Introduction" in paths
    assert "1 Introduction / 1.1 Materials" in paths


def test_section_blocks_assigned():
    blocks = make_blocks()
    roots = build_section_tree(blocks)
    abstract = roots[0]
    assert len(abstract.blocks) == 1
    assert abstract.blocks[0].text == "Introduction text."
