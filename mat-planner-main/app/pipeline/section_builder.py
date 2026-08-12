from dataclasses import dataclass, field
from .parsers.base import TextBlock


@dataclass
class SectionNode:
    title: str
    level: int
    section_path: str
    blocks: list[TextBlock] = field(default_factory=list)
    children: list["SectionNode"] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None


def build_section_tree(blocks: list[TextBlock]) -> list[SectionNode]:
    """把 TextBlock 列表构建成 section 树"""
    roots: list[SectionNode] = []
    stack: list[SectionNode] = []  # 当前路径

    for block in blocks:
        if block.block_type == "heading":
            node = SectionNode(
                title=block.text,
                level=block.level,
                section_path="",
                page_start=block.page,
            )
            # 弹出同级或更深的节点
            while stack and stack[-1].level >= block.level:
                stack.pop()

            if stack:
                parent = stack[-1]
                parent.children.append(node)
                node.section_path = parent.section_path + " / " + block.text
            else:
                roots.append(node)
                node.section_path = block.text

            stack.append(node)
        else:
            # 非标题 block 归属于当前最内层 section
            if stack:
                stack[-1].blocks.append(block)
            else:
                # 无标题的内容，放到虚拟根节点
                if not roots or roots[-1].level != 0:
                    root = SectionNode(title="__root__", level=0, section_path="")
                    roots.append(root)
                    stack = [root]
                roots[-1].blocks.append(block)

    return roots


def flatten_sections(roots: list[SectionNode]) -> list[SectionNode]:
    """展平 section 树为有序列表"""
    result = []

    def _traverse(node: SectionNode):
        result.append(node)
        for child in node.children:
            _traverse(child)

    for root in roots:
        _traverse(root)
    return result
