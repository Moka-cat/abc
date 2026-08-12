"""SafetyAgent — dedicated safety assessment for materials and formulations.

Handles:
  - "X 物质的安全风险是什么？"
  - "AP 和 HMX 能混合使用吗？"
  - "这个配方（AP 68%, HTPB 20%, Al 12%）安全吗？"
  - "如何安全储存 RDX？"

Flow:
  1. Parse formulation from query text (if any percentages found)
  2. Resolve entity names → get_entity_card (sensitivity, decomposition data)
  3. search_memory for safety-related literature
  4. check_safety if parseable formulation present
  5. LLM synthesis → structured safety report (with 3-stage retry)
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.zongkong.agents.data import _resolve_entity_name
from app.services.zongkong.streaming import stream_synthesis
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_SAFETY_SYSTEM = """\
你是含能材料安全专家。根据提供的实体数据、安全审查结果和文献检索，生成结构化安全评估报告。

报告格式：
## 安全评估结论
**综合风险等级**：低 / 中 / 高 / 极高
**使用建议**：一句话结论

## 主要风险点
（逐条列出，每条说明：风险类型、触发条件、危害程度）

## 敏感性数据
（如有实体卡数据，列出撞击感度、摩擦感度、ESD 感度、热分解温度等）

## 相容性
（如有多个组分，说明已知的不相容对）

## 安全操作建议
（储存、处理、混合、废弃物处置等，不超过 5 条）

行文简洁专业。若数据不足，明确标注"数据不足"，不得凭空编造风险等级。
"""


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _parse_formulation(query: str) -> dict[str, float]:
    """Try to extract {component: fraction} from query text.

    Recognises patterns like:
      "AP 68%", "68% AP", "AP：68%", "AP/HTPB/Al = 68/20/12"
    """
    result: dict[str, float] = {}

    # "组分 数字%" or "数字% 组分"
    pat1 = re.compile(r'([A-Za-z\u4e00-\u9fa5][\w\-]*)\s*[：:＝=]?\s*(\d+\.?\d*)\s*%')
    pat2 = re.compile(r'(\d+\.?\d*)\s*%\s*([A-Za-z\u4e00-\u9fa5][\w\-]*)')

    for m in pat1.finditer(query):
        comp, frac = m.group(1), float(m.group(2))
        result[comp] = frac / 100.0

    for m in pat2.finditer(query):
        frac, comp = float(m.group(1)), m.group(2)
        result.setdefault(comp, frac / 100.0)

    # "A/B/C = x/y/z" format
    slash_match = re.search(
        r'([A-Za-z\d\-]+(?:/[A-Za-z\d\-]+)+)\s*[=＝]\s*([\d.]+(?:/[\d.]+)+)', query
    )
    if slash_match and not result:
        comps = slash_match.group(1).split('/')
        fracs = [float(f) for f in slash_match.group(2).split('/')]
        total = sum(fracs) or 1.0
        for c, f in zip(comps, fracs):
            result[c] = f / total

    return result


def _extract_entities(query: str) -> list[str]:
    """Heuristic extraction of material names (uppercase acronyms + known names)."""
    known = {
        "RDX", "HMX", "TATB", "PETN", "TNT", "CL-20", "FOX-7",
        "AP", "AN", "HTPB", "CTPB", "GAP", "PEG",
        "铝粉", "铝", "镁粉", "硼粉",
    }
    found = []
    for name in known:
        if name in query:
            found.append(name)
    # Also match uppercase 2-5 letter tokens
    for m in re.finditer(r'\b([A-Z]{2,5}(?:-\d+)?)\b', query):
        token = m.group(1)
        if token not in found:
            found.append(token)
    return found[:5]


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

    # ── Determine what we have ────────────────────────────────────
    formulation = _parse_formulation(query)
    entities    = _extract_entities(query)
    if formulation:
        resolved_entities = list(formulation.keys())
    else:
        resolved_entities = [_resolve_entity_name(e, db) for e in entities[:3]]

    # ── Plan event ────────────────────────────────────────────────
    plan_steps = [{"step_id": 1, "tool": "search_memory",
                   "goal": "检索安全相关文献", "args": {}}]
    tool_names = ["search_memory"]
    if resolved_entities:
        plan_steps.insert(0, {"step_id": 0, "tool": "get_entity_card",
                               "goal": "获取实体感度和热分解数据", "args": {}})
        tool_names.insert(0, "get_entity_card")
    if formulation:
        plan_steps.append({"step_id": len(plan_steps), "tool": "check_safety",
                            "goal": "执行配方安全门控审查", "args": {}})
        tool_names.append("check_safety")
    plan_steps.append({"step_id": len(plan_steps), "tool": "llm_synthesis",
                        "goal": "生成结构化安全评估报告", "args": {}})
    tool_names.append("llm_synthesis")

    # Re-number step_ids
    for i, s in enumerate(plan_steps):
        s["step_id"] = i + 1

    yield _sse({"type": "plan", "plan": plan_steps, "tool_names": tool_names})
    yield _sse({"type": "progress", "phase": "safety_lookup",
                "message": f"正在评估 {', '.join(resolved_entities) or '目标物质'} 的安全性…"})

    # ── Step 1: Entity cards (concurrent) ─────────────────────────
    entity_cards: dict[str, dict] = {}
    if resolved_entities:
        def _fetch_card(name: str) -> tuple[str, dict | None]:
            tool = ToolRegistry.create("get_entity_card", db)
            r = tool.run(name=name)
            return name, (r.data if r.success and r.data else None)

        with ThreadPoolExecutor(max_workers=min(len(resolved_entities), 4)) as pool:
            futures = {pool.submit(_fetch_card, e): e for e in resolved_entities}
            for fut in as_completed(futures):
                name, card = fut.result()
                if card:
                    entity_cards[name] = card
                    logger.info(f"[SafetyAgent] entity_card ok: {name}")

    # ── Step 2: Safety literature search ─────────────────────────
    yield _sse({"type": "progress", "phase": "safety_lookup",
                "message": "正在检索安全文献…"})
    mem_tool = ToolRegistry.create("search_memory", db)
    safety_query = f"{query} 安全 感度 风险 储存"
    mem_r = mem_tool.run(query=safety_query, namespace=namespace, top_k=6)
    search_data = mem_r.data if mem_r.success and mem_r.data else {}

    # ── Step 3: Safety check (if formulation present) ─────────────
    safety_report: dict = {}
    if formulation:
        yield _sse({"type": "progress", "phase": "safety_check",
                    "message": "正在执行配方安全门控审查…"})
        check_tool = ToolRegistry.create("check_safety", db)
        check_r = check_tool.run(
            formulation={k: {"fraction": v, "role": ""} for k, v in formulation.items()},
            namespace=namespace,
        )
        if check_r.success and check_r.data:
            safety_report = check_r.data
            logger.info(f"[SafetyAgent] check_safety → {safety_report.get('status')}")

    # ── Step 4: LLM synthesis ─────────────────────────────────────
    yield _sse({"type": "progress", "phase": "generating",
                "message": "正在生成安全评估报告…"})

    context_parts: list[str] = [f"用户安全查询：{query}"]
    if entity_cards:
        context_parts.append(
            f"实体安全数据：\n{json.dumps(entity_cards, ensure_ascii=False, indent=2)}"
        )
    if formulation:
        context_parts.append(
            f"检测到的配方：\n{json.dumps(formulation, ensure_ascii=False)}"
        )
    if safety_report:
        context_parts.append(
            f"安全门控审查结果：\n{json.dumps(safety_report, ensure_ascii=False, indent=2)}"
        )
    if search_data:
        evidence = search_data.get("evidence_text", "")[:2000]
        if evidence:
            context_parts.append(f"安全文献参考（节选）：\n{evidence}")

    user_content = "\n\n".join(context_parts)

    messages: list[dict] = [{"role": "system", "content": _SAFETY_SYSTEM}]
    for turn in history[-4:]:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_content})

    yield from stream_synthesis(
        messages=messages,
        temperature=0.1,
        log_prefix="[SafetyAgent]",
        error_msg="安全评估报告生成出错，请重试。",
    )

    yield _sse({"type": "done", "elapsed_secs": int(time.monotonic() - t_start)})
