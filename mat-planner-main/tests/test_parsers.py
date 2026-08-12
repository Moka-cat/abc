import json
from pathlib import Path
from app.pipeline.parsers.markdown import MarkdownParser
from app.pipeline.parsers.mineru_json import MinerUJsonParser, _html_table_to_text
from app.pipeline.parsers.text import TextParser


def test_markdown_parser_headings(tmp_path):
    content = "# Title\n\n## Section 1\n\nParagraph text.\n\n### Sub\n\nMore text."
    f = tmp_path / "test.md"
    f.write_text(content)

    parser = MarkdownParser()
    result = parser.parse(f)

    headings = [b for b in result.blocks if b.block_type == "heading"]
    assert len(headings) == 3
    assert headings[0].text == "Title"
    assert headings[0].level == 1
    assert headings[1].text == "Section 1"
    assert headings[1].level == 2


def test_markdown_parser_table(tmp_path):
    content = "# Doc\n\n| Col1 | Col2 |\n|------|------|\n| A    | B    |\n"
    f = tmp_path / "test.md"
    f.write_text(content)

    parser = MarkdownParser()
    result = parser.parse(f)

    tables = [b for b in result.blocks if b.block_type == "table"]
    assert len(tables) == 1
    assert "Col1" in tables[0].text


def test_text_parser(tmp_path):
    content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    f = tmp_path / "test.txt"
    f.write_text(content)

    parser = TextParser()
    result = parser.parse(f)
    assert len(result.blocks) == 3
    assert result.blocks[0].text == "First paragraph."


def test_markdown_can_parse():
    parser = MarkdownParser()
    assert parser.can_parse(Path("test.md"))
    assert parser.can_parse(Path("test.markdown"))
    assert not parser.can_parse(Path("test.pdf"))


def test_mineru_json_parser_basic(tmp_path):
    data = {
        "title": "Test Paper",
        "doi": "10.1234/test",
        "content_list": [
            {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "This is body text.", "text_level": None, "page_idx": 0},
            {"type": "image", "img_path": "s3://...", "page_idx": 1},  # skipped
        ],
    }
    fp = tmp_path / "paper.json"
    fp.write_text(json.dumps(data))

    parser = MinerUJsonParser()
    assert parser.can_parse(fp)
    result = parser.parse(fp)

    headings = [b for b in result.blocks if b.block_type == "heading"]
    paras = [b for b in result.blocks if b.block_type == "paragraph"]
    assert len(headings) == 1
    assert headings[0].text == "Introduction"
    assert len(paras) == 1
    assert paras[0].text == "This is body text."
    assert result.metadata["title"] == "Test Paper"
    assert result.metadata["doi"] == "10.1234/test"


def test_mineru_json_parser_table(tmp_path):
    table_html = (
        "<table><tr><td>Compound</td><td>D, m/s</td><td>d, g/cm3</td></tr>"
        "<tr><td>RDX</td><td>8748</td><td>1.82</td></tr></table>"
    )
    data = {
        "content_list": [
            {
                "type": "table",
                "table_body": table_html,
                "table_caption": ["Table 1. Energetic materials"],
                "table_footnote": [],
                "page_idx": 2,
            }
        ]
    }
    fp = tmp_path / "paper.json"
    fp.write_text(json.dumps(data))

    parser = MinerUJsonParser()
    result = parser.parse(fp)
    tables = [b for b in result.blocks if b.block_type == "table"]
    assert len(tables) == 1
    assert "Table 1. Energetic materials" in tables[0].text
    assert "RDX" in tables[0].text
    assert "8748" in tables[0].text


def test_html_table_to_text():
    html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    result = _html_table_to_text(html)
    assert "A | B" in result
    assert "1 | 2" in result
