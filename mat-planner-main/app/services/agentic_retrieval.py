"""AgenticRetrievalService — 仿照 knowhere Agentic Retrieval 的三阶段导航检索。

核心设计（对照 knowhere）：
  Phase 1 Discovery  — 用现有混合 RRF 找候选文档（不是最终内容）
  Phase 2 Navigation — LLM 读章节大纲（title + summary），逐步决定钻入哪里，
                       标记需要取内容的路径（collector pattern）
  Phase 3 Assembly   — 批量 hydrate 选中路径的 chunk，组装层次化证据文本

与 flat retrieval 的关键区别：
  flat  → 向量匹配找 chunk → 直接返回（不管文档结构）
  agentic → 向量找候选文档 → LLM 导航章节树 → 取选中章节的全部内容
             结果更连贯，不会漏掉同一章节里相邻的关键段落

Token Budget（简化自 knowhere 三池模型）：
  planning_tokens — 导航 LLM 调用消耗（估算每步 ~200 token）
  context_tokens  — 最终证据文本容量（约 5000 token ≈ 3000 中文字）
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.orm.document import Document, DocumentSection, DocumentChunk


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DocTreeNode:
    """单个文档的层次化证据树（对应 knowhere DocTreeNode）。"""
    doc_id: str
    doc_title: str
    scope_path: str | None          # None = 文档根节点
    chunks: list[dict] = field(default_factory=list)   # 本节点 hydrate 的 chunk
    children: dict[str, "DocTreeNode"] = field(default_factory=dict)
    confidence: float = 1.0

    def flatten_chunks(self) -> list[dict]:
        """按文档顺序返回所有 chunk（深度优先）。"""
        result = list(self.chunks)
        for child in sorted(self.children.values(), key=lambda n: n.scope_path or ""):
            result.extend(child.flatten_chunks())
        return result

    def to_evidence_text(self, indent: int = 0) -> str:
        """渲染为层次化 Markdown 证据文本。"""
        lines: list[str] = []
        prefix = "  " * indent
        if self.scope_path:
            lines.append(f"{prefix}### {self.scope_path}")
        for c in self.chunks:
            text = c.get("text", "")[:800]
            lines.append(f"{prefix}{text}\n")
        for child in sorted(self.children.values(), key=lambda n: n.scope_path or ""):
            lines.append(child.to_evidence_text(indent + 1))
        return "\n".join(lines)


@dataclass
class NavigationState:
    """单文档导航的可变状态。"""
    current_path: str | None = None          # 当前所在章节路径（None = 根）
    visited_paths: set[str] = field(default_factory=set)
    collected_paths: list[tuple[str, float]] = field(default_factory=list)  # (path, confidence)
    step_count: int = 0


@dataclass
class RetrievalBudget:
    """Token 预算（简化自 knowhere 三池模型）。"""
    planning_tokens: int = 3000
    context_tokens: int = 5000
    _planning_used: int = 0
    _context_used: int = 0

    def can_plan(self) -> bool:
        return self._planning_used < self.planning_tokens

    def consume_planning(self, estimated: int = 200) -> None:
        self._planning_used = min(self._planning_used + estimated, self.planning_tokens)

    def consume_context(self, chars: int) -> bool:
        """尝试消耗 context 预算。返回 False 表示已满，不应再加入内容。"""
        tokens_est = chars // 4  # 粗估：4 字符 ≈ 1 token
        if self._context_used + tokens_est > self.context_tokens:
            return False
        self._context_used += tokens_est
        return True

    @property
    def planning_remaining(self) -> int:
        return max(0, self.planning_tokens - self._planning_used)

    @property
    def context_remaining(self) -> int:
        return max(0, self.context_tokens - self._context_used)


@dataclass
class DecisionTraceStep:
    """导航决策记录（对应 knowhere DecisionTraceStep）。"""
    step: int
    doc_id: str
    phase: str                      # "discover" | "navigate" | "collect" | "finish"
    observation: dict[str, Any]     # LLM 看到了什么
    decision: dict[str, Any]        # LLM 做了什么决定
    elapsed_ms: int = 0


@dataclass
class AgenticRetrievalResult:
    evidence_text: str
    referenced_chunks: list[dict]           # [{chunk_id, doc_id, section_path, score}]
    decision_trace: list[DecisionTraceStep]
    stop_reason: str                         # "finished" | "budget" | "max_steps" | "error"
    doc_trees: dict[str, DocTreeNode]        # doc_id → DocTreeNode（供调试）


# ─────────────────────────────────────────────────────────────────────────────
# 导航 LLM Prompt
# ─────────────────────────────────────────────────────────────────────────────

_NAV_SYSTEM = """You are navigating a scientific document section tree to find evidence for a research query.
At each step you see the CHILDREN of the current section (title + summary + page range).
Choose what to do next:

Return JSON with this structure:
{
  "collect": ["section/path/A", "section/path/B"],  // sections to retrieve full content from (can be empty)
  "expand": "section/path/C",   // ONE section to drill into next (or null to stay)
  "finish": false,               // true = stop navigation for this document
  "reason": "brief explanation"
}

Rules:
- collect paths that clearly contain relevant data (methods, results, tables with values)
- expand into sections that MIGHT contain relevant subsections worth exploring
- finish when you have enough collected paths or the document seems irrelevant
- you may collect AND finish in the same step
- prefer depth over breadth: drill into the most promising branch"""


def _build_nav_prompt(
    query: str,
    doc_title: str,
    current_path: str | None,
    outline_items: list[dict],
    collected_so_far: list[str],
    steps_remaining: int,
    budget_remaining: int,
) -> str:
    outline_text = "\n".join(
        f"  [{i+1}] path={item['path']!r}  title={item['title']!r}"
        + (f"  pages={item['pages']}" if item.get("pages") else "")
        + (f"\n       summary: {item['summary']}" if item.get("summary") else "")
        for i, item in enumerate(outline_items)
    )
    collected_text = (
        "\n".join(f"  - {p}" for p in collected_so_far) if collected_so_far else "  (none yet)"
    )
    return (
        f"Query: {query}\n\n"
        f"Document: {doc_title!r}\n"
        f"Current location: {current_path or 'document root'}\n\n"
        f"Children sections:\n{outline_text or '  (no children — this is a leaf section)'}\n\n"
        f"Already collected:\n{collected_text}\n\n"
        f"Steps remaining: {steps_remaining}  |  Context budget remaining: ~{budget_remaining} tokens\n\n"
        "Choose your action (JSON):"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AgenticRetrievalService
# ─────────────────────────────────────────────────────────────────────────────

class AgenticRetrievalService:
    """三阶段导航检索服务。

    Phase 1 — Discovery:  混合 RRF 找到最相关的 AGENTIC_MAX_DOCS 个文档
    Phase 2 — Navigation: 每个文档独立运行 LLM 导航，在章节树上决策
    Phase 3 — Assembly:   batch hydrate 收集到的路径，组装层次化证据文本
    """

    def __init__(self, db: Session):
        self.db = db
        self._client = None  # lazy init

    def _get_client(self):
        if self._client is None:
            from app.services.llm_client import get_llm_client
            self._client = get_llm_client()
        return self._client

    # ── Phase 1: Discovery ────────────────────────────────────────────────────

    def _discover_candidate_docs(
        self, query: str, namespace: str
    ) -> list[tuple[str, str]]:
        """返回 [(doc_id, doc_title), ...] 按相关度排序。

        使用现有 RetrievalService 快速检索，提取出现最多的 top-N 文档。
        """
        from app.services.retrieval import RetrievalService
        result = RetrievalService(self.db).query(query, namespace, top_k=20)

        # 统计各文档的出现次数（按结果排名加权）
        doc_scores: dict[str, float] = {}
        for i, r in enumerate(result.results):
            weight = 1.0 / (i + 1)  # 排名越高权重越大
            doc_scores[r.document_id] = doc_scores.get(r.document_id, 0.0) + weight

        ranked_doc_ids = sorted(doc_scores, key=doc_scores.get, reverse=True)
        ranked_doc_ids = ranked_doc_ids[:settings.AGENTIC_MAX_DOCS]

        if not ranked_doc_ids:
            return []

        docs = self.db.query(Document).filter(Document.id.in_(ranked_doc_ids)).all()
        doc_map = {d.id: d.title for d in docs}
        return [(did, doc_map.get(did, "Unknown")) for did in ranked_doc_ids]

    # ── Section outline helpers ───────────────────────────────────────────────

    def _get_children_outline(
        self, doc_id: str, parent_path: str | None
    ) -> list[dict]:
        """获取当前节点的直接子章节大纲（用于导航 prompt）。"""
        q = self.db.query(DocumentSection).filter(
            DocumentSection.document_id == doc_id
        )
        if parent_path is None:
            # 根节点：返回顶层章节（level=1 或 parent_id IS NULL）
            q = q.filter(DocumentSection.parent_id.is_(None))
        else:
            # 查找 section_path 匹配父路径的章节，再取其子
            parent_sec = (
                q.filter(DocumentSection.section_path == parent_path).first()
            )
            if not parent_sec:
                return []
            q = self.db.query(DocumentSection).filter(
                DocumentSection.document_id == doc_id,
                DocumentSection.parent_id == parent_sec.id,
            )

        sections = q.order_by(DocumentSection.page_start).all()
        items = []
        for s in sections:
            page_str = ""
            if s.page_start is not None:
                page_str = (
                    f"p.{s.page_start}-{s.page_end}"
                    if s.page_end and s.page_end != s.page_start
                    else f"p.{s.page_start}"
                )
            items.append({
                "path": s.section_path,
                "title": s.title,
                "pages": page_str,
                "summary": s.summary,  # may be None if not yet generated
            })
        return items

    def _get_root_outline(self, doc_id: str) -> list[dict]:
        """文档根节点大纲（所有顶层章节）。"""
        return self._get_children_outline(doc_id, None)

    # ── Navigation LLM call ───────────────────────────────────────────────────

    def _llm_navigate_step(
        self,
        query: str,
        doc_title: str,
        state: NavigationState,
        outline_items: list[dict],
        budget: RetrievalBudget,
    ) -> dict:
        """单步导航决策，返回 {collect, expand, finish, reason}。"""
        if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            # No LLM: 收集所有可见路径，直接结束
            return {
                "collect": [item["path"] for item in outline_items[:3]],
                "expand": None,
                "finish": True,
                "reason": "LLM not configured, collecting top sections",
            }

        steps_left = settings.AGENTIC_MAX_NAV_STEPS - state.step_count
        collected_paths = [p for p, _ in state.collected_paths]
        prompt = _build_nav_prompt(
            query=query,
            doc_title=doc_title,
            current_path=state.current_path,
            outline_items=outline_items,
            collected_so_far=collected_paths,
            steps_remaining=steps_left,
            budget_remaining=budget.context_remaining,
        )

        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": _NAV_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=200,
                timeout=10,
            )
            raw = resp.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            budget.consume_planning(200)
            return {
                "collect": parsed.get("collect") or [],
                "expand": parsed.get("expand"),
                "finish": bool(parsed.get("finish", False)),
                "reason": parsed.get("reason", ""),
            }
        except Exception as e:
            logger.debug(f"Nav LLM call failed (non-fatal): {e}")
            # Fallback: collect current outline, finish
            budget.consume_planning(200)
            return {
                "collect": [item["path"] for item in outline_items[:2]],
                "expand": None,
                "finish": True,
                "reason": f"LLM error ({e}), collecting visible sections",
            }

    # ── Phase 2: Navigation ───────────────────────────────────────────────────

    def _navigate_document(
        self,
        doc_id: str,
        doc_title: str,
        query: str,
        budget: RetrievalBudget,
    ) -> tuple[NavigationState, list[DecisionTraceStep]]:
        """在单个文档上运行 LLM 导航，返回 NavigationState（含 collected_paths）。"""
        state = NavigationState()
        trace: list[DecisionTraceStep] = []
        step_global = 0

        while state.step_count < settings.AGENTIC_MAX_NAV_STEPS:
            if not budget.can_plan():
                stop_reason = "budget"
                logger.debug(f"[agentic] Doc {doc_id[:8]} nav stopped: planning budget exhausted")
                break

            t0 = time.monotonic()
            outline = self._get_children_outline(doc_id, state.current_path)

            # 没有子节点：说明是叶章节，直接收集并结束
            if not outline:
                if state.current_path and state.current_path not in {p for p, _ in state.collected_paths}:
                    state.collected_paths.append((state.current_path, 0.9))
                break

            decision = self._llm_navigate_step(query, doc_title, state, outline, budget)
            elapsed = int((time.monotonic() - t0) * 1000)

            # 记录决策
            trace.append(DecisionTraceStep(
                step=step_global,
                doc_id=doc_id,
                phase="navigate",
                observation={"current_path": state.current_path, "outline_count": len(outline)},
                decision=decision,
                elapsed_ms=elapsed,
            ))
            step_global += 1
            state.step_count += 1

            # 执行收集
            for path in (decision.get("collect") or []):
                if path not in {p for p, _ in state.collected_paths}:
                    state.collected_paths.append((path, 0.85))

            # 执行扩展（钻入子章节）
            expand_path = decision.get("expand")
            if expand_path and expand_path not in state.visited_paths:
                state.visited_paths.add(expand_path)
                state.current_path = expand_path
            else:
                # 没有可扩展目标：结束
                decision["finish"] = True

            if decision.get("finish"):
                break

        # 保底：如果一个路径都没收集到，取根节点前 3 个章节
        if not state.collected_paths:
            root_outline = self._get_root_outline(doc_id)
            for item in root_outline[:3]:
                state.collected_paths.append((item["path"], 0.5))
            logger.debug(f"[agentic] Doc {doc_id[:8]}: no paths collected, using top-3 root sections")

        return state, trace

    # ── Phase 3: Assembly ─────────────────────────────────────────────────────

    def _hydrate_path(self, doc_id: str, section_path: str) -> list[dict]:
        """获取某章节路径下的所有 chunk（包含该路径及其子路径的所有 chunk）。"""
        chunks = (
            self.db.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id == doc_id,
                DocumentChunk.section_path.like(f"{section_path}%"),
                DocumentChunk.chunk_type != "parent",
            )
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        return [
            {
                "chunk_id": c.id,
                "doc_id": doc_id,
                "section_path": c.section_path,
                "text": c.chunk_text,
                "page_start": c.page_start,
                "score": 1.0,
            }
            for c in chunks
        ]

    def _build_doc_tree(
        self,
        doc_id: str,
        doc_title: str,
        collected_paths: list[tuple[str, float]],
        budget: RetrievalBudget,
    ) -> DocTreeNode:
        """Hydrate 选中路径，组装 DocTreeNode。"""
        root = DocTreeNode(doc_id=doc_id, doc_title=doc_title, scope_path=None)

        for path, confidence in collected_paths:
            chunks = self._hydrate_path(doc_id, path)
            node = DocTreeNode(
                doc_id=doc_id,
                doc_title=doc_title,
                scope_path=path,
                chunks=chunks,
                confidence=confidence,
            )
            root.children[path] = node

            # 检查 context budget
            total_chars = sum(len(c["text"]) for c in chunks)
            if not budget.consume_context(total_chars):
                logger.debug(f"[agentic] Context budget reached at path {path!r}")
                break

        return root

    def _assemble_evidence(
        self, doc_trees: dict[str, DocTreeNode]
    ) -> tuple[str, list[dict]]:
        """把所有文档树渲染为证据文本，收集 referenced_chunks。"""
        parts: list[str] = []
        referenced: list[dict] = []

        for doc_id, tree in doc_trees.items():
            parts.append(f"## {tree.doc_title}\n")
            parts.append(tree.to_evidence_text())
            parts.append("")
            for c in tree.flatten_chunks():
                referenced.append({
                    "chunk_id": c["chunk_id"],
                    "doc_id": c["doc_id"],
                    "section_path": c["section_path"],
                    "score": c.get("score", 1.0),
                })

        return "\n".join(parts), referenced

    # ── Public entry point ────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        namespace: str = "default",
    ) -> AgenticRetrievalResult:
        """执行三阶段导航检索。

        Returns AgenticRetrievalResult，包含：
          - evidence_text: 层次化 Markdown 证据文本
          - referenced_chunks: 所有引用的 chunk（用于命中计数）
          - decision_trace: 完整导航决策记录
          - stop_reason: 停止原因
          - doc_trees: 每个文档的 DocTreeNode（供调试）
        """
        budget = RetrievalBudget(
            planning_tokens=settings.AGENTIC_PLANNING_TOKENS,
            context_tokens=settings.AGENTIC_CONTEXT_TOKENS,
        )
        all_trace: list[DecisionTraceStep] = []
        doc_trees: dict[str, DocTreeNode] = {}
        stop_reason = "finished"

        # ── Phase 1: Discovery ────────────────────────────────────────────────
        t0 = time.monotonic()
        candidates = self._discover_candidate_docs(query, namespace)
        logger.info(
            f"[agentic] Discovery: {len(candidates)} candidate docs "
            f"({int((time.monotonic()-t0)*1000)}ms)"
        )

        if not candidates:
            return AgenticRetrievalResult(
                evidence_text="",
                referenced_chunks=[],
                decision_trace=[],
                stop_reason="no_candidates",
                doc_trees={},
            )

        # ── Phase 2: Navigation ───────────────────────────────────────────────
        for doc_id, doc_title in candidates:
            if not budget.can_plan():
                stop_reason = "budget"
                break

            logger.info(f"[agentic] Navigating doc {doc_id[:8]} ({doc_title!r})")
            nav_state, nav_trace = self._navigate_document(doc_id, doc_title, query, budget)
            all_trace.extend(nav_trace)

            logger.info(
                f"[agentic] Doc {doc_id[:8]}: {len(nav_state.collected_paths)} paths collected "
                f"in {nav_state.step_count} steps"
            )

            # ── Phase 3: Hydrate + assemble per doc ──────────────────────────
            tree = self._build_doc_tree(doc_id, doc_title, nav_state.collected_paths, budget)
            doc_trees[doc_id] = tree

        evidence_text, referenced = self._assemble_evidence(doc_trees)

        logger.info(
            f"[agentic] Done: {len(referenced)} chunks from {len(doc_trees)} docs, "
            f"planning_used={budget._planning_used}/{budget.planning_tokens}, "
            f"context_used={budget._context_used}/{budget.context_tokens}"
        )

        return AgenticRetrievalResult(
            evidence_text=evidence_text,
            referenced_chunks=referenced,
            decision_trace=all_trace,
            stop_reason=stop_reason,
            doc_trees=doc_trees,
        )
