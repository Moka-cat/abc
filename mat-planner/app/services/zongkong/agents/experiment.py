"""ExperimentAgent — design_experiment → check_safety → structured synthesis.

Flow:
  1. DesignExperimentTool  (goal → candidate protocols, num_candidates=3)
  2. CheckSafetyTool       (all protocols, per-protocol progress messages)
  3. If ALL blocked → auto-retry with modified goal (avoid blocked components)
  4. LLM structured synthesis → streaming tokens (with history context)

Safety handling:
  - approved / needs_review → include in report
  - blocked → still report, marked 🚫; if ALL blocked → one auto-retry
"""
from __future__ import annotations

import json
import logging
import time
from typing import Generator

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.llm_client import get_llm_client
from app.services.zongkong.streaming import stream_synthesis
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_SYNTHESIS_SYSTEM = """\
你是含能材料实验设计专家。根据实验设计结果、文献检索证据和安全评估，生成详实的结构化报告。

报告格式（严格按照以下结构）：

## 候选方案对比

对每个候选方案（方案1、方案2…）分别输出：

### 方案 N — [安全状态: ✅ approved / ⚠️ needs_review / 🚫 blocked]

**配方组成**

| 组分 | 质量分数 | 粒径（μm） | 作用 |
|------|---------|-----------|------|
| ...  | xx%     | xx        | 氧化剂/粘合剂/燃料/... |

**预测性能**

| 性能指标 | 预测值 | 单位 | 置信度 | 依据 |
|---------|--------|------|--------|------|
| 燃速    | xx     | mm/s | 中/高  | 文献/计算 |
| 密度    | xx     | g/cm³| ...    | ... |

**安全评估**：一句话说明安全状态和主要风险点。

---

## 综合推荐

说明推荐哪个方案，从性能、安全性、工艺可行性三个维度给出理由（3-5 句）。
引用文献证据支持关键判断（如"据检索资料，AP/HTPB 体系在此粒径下燃速约为 XX mm/s"）。

## 关键工艺要点

1. 混合工艺（混合顺序、温度控制）
2. 固化条件（温度/时间）
3. 粒径与燃速的关系
4. 安全注意事项（最重要的 1-2 条）

## 与目标的差距分析

说明哪些目标已达成、哪些未达成，以及提升建议（2-3 条）。

---

行文专业，数值精确；有文献证据时引用；blocked 方案仍列出并注明不可直接用于实验。
"""


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _normalise_formulation(formulation_raw: dict) -> dict[str, float]:
    """Convert various formulation formats to {component: fraction}."""
    frac: dict[str, float] = {}
    for k, v in formulation_raw.items():
        if isinstance(v, dict):
            frac[k] = float(v.get("fraction", 0))
        else:
            try:
                frac[k] = float(v)
            except (TypeError, ValueError):
                pass
    return frac


def _run_safety_on_protocols(
    protocols: list,
    safety_tool,
    namespace: str,
) -> Generator[str, None, list]:
    """Yield per-protocol progress SSE events; return evaluated_protocols list."""
    # This is a generator that also returns a value — use the send/throw pattern
    # via a wrapper. Instead we use a simpler approach: accumulate in a list.
    raise NotImplementedError  # Not used directly — see run_stream


def _check_all_protocols(
    protocols: list,
    safety_tool,
    namespace: str,
    sse_fn,
) -> tuple[list[dict], list[str]]:
    """Run safety check on every protocol. Returns (evaluated_protocols, blocked_components)."""
    evaluated: list[dict] = []
    blocked_components: set[str] = set()

    for i, proto in enumerate(protocols):
        frac = _normalise_formulation(proto.get("formulation", {}))
        safety_data: dict = {}
        if frac:
            safety_r = safety_tool.run(
                formulation=frac,
                steps=proto.get("steps", []),
                namespace=namespace,
            )
            if safety_r.success and safety_r.data:
                safety_data = safety_r.data
                if safety_data.get("status") == "blocked":
                    for issue in safety_data.get("issues", []):
                        for comp in issue.get("affected_components", []):
                            blocked_components.add(comp)
                logger.info(
                    f"[ExperimentAgent] protocol {i+1} safety → {safety_data.get('status')}"
                )
        evaluated.append({
            "index": i + 1,
            "protocol": proto,
            "safety": safety_data,
        })

    return evaluated, list(blocked_components)


def run_stream(
    query: str,
    db: Session,
    namespace: str = "default",
    t_start: float | None = None,
    history: list[dict] | None = None,
) -> Generator[str, None, None]:
    if t_start is None:
        t_start = time.monotonic()
    history = history or []

    # ── Plan event ────────────────────────────────────────────────
    yield _sse({
        "type": "plan",
        "plan": [
            {"step_id": 1, "tool": "search_memory",
             "goal": "检索相关文献配方和性能数据作为设计依据", "args": {}},
            {"step_id": 2, "tool": "design_experiment",
             "goal": "依据研究目标和文献证据生成候选配方（3个）", "args": {}},
            {"step_id": 3, "tool": "check_safety",
             "goal": "对全部候选配方进行安全门控评估", "args": {}},
        ],
        "tool_names": ["search_memory", "design_experiment", "check_safety"],
    })

    design_tool  = ToolRegistry.create("design_experiment", db)
    safety_tool  = ToolRegistry.create("check_safety",       db)
    search_tool  = ToolRegistry.create("search_memory",      db)

    # ── Step 1: RAG — retrieve relevant literature ────────────────
    yield _sse({"type": "progress", "phase": "literature",
                "message": "正在检索相关配方文献数据…"})

    lit_context = ""
    lit_sources: list[dict] = []
    try:
        search_r = search_tool.run(query=query, namespace=namespace, top_k=8)
        if search_r.success and search_r.data:
            evidence = search_r.data.get("evidence_text") or ""
            results  = search_r.data.get("results") or []
            lit_context = evidence[:3000]  # trim to avoid context overflow
            for chunk in results[:6]:
                lit_sources.append({
                    "title":   chunk.get("document_title") or "文档",
                    "snippet": (chunk.get("text") or chunk.get("context") or "")[:120],
                    "kind":    "chunk",
                    "doc_id":  chunk.get("document_id"),
                })
            logger.info(f"[ExperimentAgent] literature retrieval: {len(results)} chunks")
    except Exception as exc:
        logger.warning(f"[ExperimentAgent] literature retrieval failed: {exc}")

    if lit_sources:
        yield _sse({"type": "sources", "sources": lit_sources})

    # ── Step 2: Design experiment ─────────────────────────────────
    yield _sse({"type": "progress", "phase": "designing",
                "message": "正在生成 3 个候选实验方案，预计 30-60 秒…"})

    design_r = design_tool.run(goal=query, namespace=namespace, num_candidates=3)
    protocols: list = []
    if design_r.success and design_r.data:
        protocols = design_r.data.get("protocols", [])
        logger.info(f"[ExperimentAgent] design_experiment ok, {len(protocols)} protocols")
    else:
        logger.warning(f"[ExperimentAgent] design_experiment failed: {design_r.error}")

    if not protocols:
        yield _sse({"type": "token",
                    "content": "实验设计失败，请重新描述研究目标或检查后端服务。"})
        yield _sse({"type": "done", "elapsed_secs": int(time.monotonic() - t_start)})
        return

    # ── Step 2: Safety check (per-protocol progress) ──────────────
    yield _sse({"type": "progress", "phase": "safety",
                "message": f"正在对 {len(protocols)} 个候选方案执行安全审查…"})

    for i in range(len(protocols)):
        yield _sse({"type": "progress", "phase": "safety",
                    "message": f"审查方案 {i+1}/{len(protocols)}…"})

    evaluated_protocols, blocked_components = _check_all_protocols(
        protocols, safety_tool, namespace, _sse
    )

    # ── Step 3: Auto-retry if ALL protocols are blocked ───────────
    all_blocked = (
        len(evaluated_protocols) > 0
        and all(
            p["safety"].get("status") == "blocked"
            for p in evaluated_protocols
            if p["safety"]
        )
    )

    if all_blocked:
        logger.info(
            f"[ExperimentAgent] all {len(protocols)} protocols blocked, "
            f"retrying with avoided components: {blocked_components}"
        )
        if blocked_components:
            avoid_note = f"\n（安全约束：请避免使用以下高风险组分：{', '.join(blocked_components)}）"
        else:
            avoid_note = "\n（安全约束：所有候选方案均被安全门控拦截，请改用感度更低的替代组分）"

        retry_goal = query + avoid_note
        yield _sse({"type": "progress", "phase": "retry",
                    "message": "⚠️ 全部方案被安全门控拦截，正在重新设计低感度替代方案…"})

        retry_r = design_tool.run(goal=retry_goal, namespace=namespace, num_candidates=3)
        if retry_r.success and retry_r.data:
            retry_protocols = retry_r.data.get("protocols", [])
            if retry_protocols:
                logger.info(f"[ExperimentAgent] retry ok, {len(retry_protocols)} new protocols")
                yield _sse({"type": "progress", "phase": "safety",
                            "message": f"正在审查重新设计的 {len(retry_protocols)} 个方案…"})
                retry_evaluated, _ = _check_all_protocols(
                    retry_protocols, safety_tool, namespace, _sse
                )
                # Merge: show original blocked ones + new retry results
                evaluated_protocols = evaluated_protocols + [
                    {**ep, "index": len(evaluated_protocols) + ep["index"],
                     "note": "重试方案"}
                    for ep in retry_evaluated
                ]

    # ── Step 4: LLM synthesis (all candidates + history) ─────────
    yield _sse({"type": "progress", "phase": "generating",
                "message": "正在生成综合方案报告…"})

    user_content = f"研究目标：{query}\n\n"
    if lit_context:
        user_content += f"【文献检索证据】（请在报告中引用相关数据）：\n{lit_context}\n\n"
    user_content += (
        f"候选方案及安全评估（共 {len(evaluated_protocols)} 个）：\n"
        f"{json.dumps(evaluated_protocols, ensure_ascii=False, indent=2)}"
    )
    if all_blocked:
        user_content += (
            "\n\n注意：原始方案全部被安全门控拦截，以上包含自动重试后的替代方案，"
            "请在报告中说明这一情况。"
        )

    # Build messages: system + trimmed history (last 6 turns) + current
    messages: list[dict] = [{"role": "system", "content": _SYNTHESIS_SYSTEM}]
    for turn in history[-6:]:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_content})

    yield from stream_synthesis(
        messages=messages,
        temperature=0.2,
        log_prefix="[ExperimentAgent]",
        error_msg="方案报告生成出错，请重试。",
    )

    yield _sse({"type": "done", "elapsed_secs": int(time.monotonic() - t_start)})
