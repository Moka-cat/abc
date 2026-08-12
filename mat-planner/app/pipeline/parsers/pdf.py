import re
from pathlib import Path
import fitz  # PyMuPDF
from .base import BaseParser, ParseResult, TextBlock
from app.pipeline.vlm_captioner import vlm_describe_image


# 常见标题字号阈值（正文一般 10-12pt，标题会更大）
_HEADING_FONT_SIZE_THRESHOLD = 13.0
# 最短有效行（过滤页眉页脚噪声）
_MIN_LINE_CHARS = 4


def _is_likely_heading(span_flags: int, span_size: float, line_text: str) -> bool:
    """根据字体大小和加粗标志判断是否是标题"""
    is_bold = bool(span_flags & 2**4)  # bold flag in PyMuPDF
    is_large = span_size >= _HEADING_FONT_SIZE_THRESHOLD
    # 短文本 + 加粗或大字号 → 标题
    return (is_bold or is_large) and len(line_text.strip()) < 120


def _detect_heading_level(size: float, bold: bool) -> int:
    """粗略按字号估算标题级别"""
    if size >= 18:
        return 1
    if size >= 15:
        return 2
    if size >= 13:
        return 3
    return 4


def _clean_text(text: str) -> str:
    # 合并连字符断行（PDF 常见）
    text = re.sub(r'-\s*\n\s*', '', text)
    # 合并行内多余空白
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


class PdfParser(BaseParser):
    supported_extensions = [".pdf"]

    def parse(self, path: Path) -> ParseResult:
        doc = fitz.open(str(path))
        blocks: list[TextBlock] = []

        for page_num, page in enumerate(doc, start=1):
            page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

            for block in page_dict.get("blocks", []):
                block_type = block.get("type")

                # 图片块（type=1）：尝试 VLM 描述
                if block_type == 1:
                    try:
                        img_bytes: bytes | None = None
                        # block["image"] contains raw image bytes in newer PyMuPDF
                        raw_img = block.get("image")
                        if raw_img:
                            img_bytes = bytes(raw_img)
                        else:
                            # Fallback: render clip bbox as pixmap
                            clip = fitz.Rect(block["bbox"])
                            pix = page.get_pixmap(clip=clip, dpi=150)
                            img_bytes = pix.tobytes("png")

                        if img_bytes:
                            caption = vlm_describe_image(img_bytes)
                            if caption:
                                blocks.append(TextBlock(
                                    text=caption,
                                    block_type="figure",
                                    page=page_num,
                                    raw=caption,
                                ))
                    except Exception:
                        pass
                    continue

                # 只处理文字块（type=0）
                if block_type != 0:
                    continue

                lines_text: list[str] = []
                block_max_size: float = 0.0
                block_bold: bool = False
                is_heading_block = False

                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue

                    line_text = "".join(s.get("text", "") for s in spans).strip()
                    if len(line_text) < _MIN_LINE_CHARS:
                        continue

                    # 取该行最大字号和加粗标志
                    max_size = max(s.get("size", 0) for s in spans)
                    bold = any(bool(s.get("flags", 0) & 2**4) for s in spans)

                    if _is_likely_heading(bold, max_size, line_text):
                        # 先把之前积累的段落输出
                        if lines_text:
                            para = _clean_text(" ".join(lines_text))
                            if para:
                                blocks.append(TextBlock(
                                    text=para,
                                    block_type="paragraph",
                                    page=page_num,
                                    raw=para,
                                ))
                            lines_text = []

                        level = _detect_heading_level(max_size, bold)
                        blocks.append(TextBlock(
                            text=line_text,
                            block_type="heading",
                            level=level,
                            page=page_num,
                            raw=line_text,
                        ))
                        is_heading_block = True
                    else:
                        lines_text.append(line_text)
                        block_max_size = max(block_max_size, max_size)
                        block_bold = block_bold or bold

                # 输出剩余段落文本
                if lines_text:
                    para = _clean_text(" ".join(lines_text))
                    if para:
                        # 简单启发：整块都是粗体/大字 → 仍视为标题
                        if block_bold and block_max_size >= _HEADING_FONT_SIZE_THRESHOLD and len(para) < 120:
                            level = _detect_heading_level(block_max_size, block_bold)
                            blocks.append(TextBlock(
                                text=para,
                                block_type="heading",
                                level=level,
                                page=page_num,
                                raw=para,
                            ))
                        else:
                            blocks.append(TextBlock(
                                text=para,
                                block_type="paragraph",
                                page=page_num,
                                raw=para,
                            ))

        doc.close()
        return ParseResult(blocks=blocks, source_path=str(path), file_type="pdf")
