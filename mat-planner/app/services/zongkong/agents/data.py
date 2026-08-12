"""DataAgent — fast entity / property lookup, target < 60 s.

Flow:
  1. Determine retrieval tool based on entity count
  2. Resolve entity names via fuzzy DB lookup (ilike + alias fallback)
  3. Emit plan event so PlanBar shows steps
  4. GetEntityCardTool  (single entity)
     CompareValuesTool  (multi-entity comparison)
     SearchMemoryTool   (fallback / no entity)
  5. Quick LLM synthesis → streaming tokens (with history context)
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.llm_client import get_llm_client
from app.services.zongkong.streaming import stream_synthesis
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_SYNTHESIS_SYSTEM = """\
你是含能材料数据专家。根据检索到的实体数据，给出详实精确的回答。

回答要求：
- 直接报告数值，标明单位；有多个数据源时给出范围和常用典型值
- 重要数值加粗（Markdown **value**）
- 对比查询时，使用 Markdown 表格展示（列：物质 / 属性 / 单位，可多列）
- 说明数值的测试条件（温度、压力、粒径等）；条件不同时分行列出
- 数据来源于知识库时，在段末注明「（据知识库实体数据）」
- 若知识库数据不足，可补充化学知识给出典型文献值，并注明「（文献典型值）」；但必须区分，不得混淆
- 如无任何依据，明确说明"当前知识库暂无此数据"，不得编造数值
- 结尾可提供 1-2 条建议：下一步检索哪些性质、相关物质，或建议摄取哪类文献
"""

# 属性关键词 → 标准英文字段名（供 compare_values 使用）
_PROP_MAP = {
    "密度": "density",
    "爆速": "detonation_velocity",
    "爆压": "detonation_pressure",
    "分子量": "molecular_weight",
    "熔点": "melting_point",
    "燃速": "burning_rate",
    "比冲": "specific_impulse",
    "感度": "sensitivity",
    "氧平衡": "oxygen_balance",
    "分解温度": "decomposition_temperature",
}


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _extract_property(query: str) -> str:
    for zh, en in _PROP_MAP.items():
        if zh in query:
            return en
    return "density"


def _resolve_entity_name(name: str, db: Session) -> str:
    """Fuzzy-resolve an entity name to its DB canonical_name.

    Priority:
      1. Exact canonical_name match (case-insensitive via ilike)
      2. Exact alias match (ilike)
      3. Partial canonical_name match (%name%)
      4. Partial alias match (%name%)
      5. Original name as fallback
    """
    from app.models.orm.domain import DomainEntity, EntityAlias

    # 1. Case-insensitive canonical name
    ent = db.query(DomainEntity).filter(DomainEntity.canonical_name.ilike(name)).first()
    if ent:
        return ent.canonical_name

    # 2. Exact alias (case-insensitive)
    alias_row = db.query(EntityAlias).filter(EntityAlias.alias.ilike(name)).first()
    if alias_row:
        return alias_row.entity.canonical_name

    # 3. Partial canonical_name
    ent = db.query(DomainEntity).filter(DomainEntity.canonical_name.ilike(f"%{name}%")).first()
    if ent:
        return ent.canonical_name

    # 4. Partial alias
    alias_row = db.query(EntityAlias).filter(EntityAlias.alias.ilike(f"%{name}%")).first()
    if alias_row:
        return alias_row.entity.canonical_name

    return name  # unchanged fallback


def run_stream(
    query: str,
    entity: str,
    db: Session,
    namespace: str = "default",
    t_start: float | None = None,
    history: list[dict] | None = None,
) -> Generator[str, None, None]:
    if t_start is None:
        t_start = time.monotonic()
    history = history or []

    # ── Determine retrieval strategy ──────────────────────────────
    raw_entities = [e.strip() for e in entity.split("/") if e.strip()] if entity else []
    # Fuzzy-resolve each entity name to DB canonical name
    entities = [_resolve_entity_name(e, db) for e in raw_entities]
    if entities != raw_entities:
        logger.info(f"[DataAgent] entity resolved: {raw_entities} → {entities}")

    if len(entities) == 1:
        retrieval_tool = "get_entity_card"
        retrieval_goal = f"查询 {entities[0]} 的属性数据"
        progress_msg   = f"正在查询 {entities[0]} 实体数据…"
    elif len(entities) >= 2:
        prop_en = _extract_property(query)
        retrieval_tool = "compare_values"
        retrieval_goal = f"对比 {' / '.join(entities[:3])} 的属性"
        prop_zh = next((zh for zh, en in _PROP_MAP.items() if en == prop_en), prop_en)
        progress_msg   = f"正在对比 {' vs '.join(entities[:3])} 的 {prop_zh} 数据…"
    else:
        retrieval_tool = "search_memory"
        retrieval_goal = "语义检索相关数据"
        progress_msg   = "正在语义检索相关数据…"

    # ── Plan event ────────────────────────────────────────────────
    yield _sse({
        "type": "plan",
        "plan": [
            {"step_id": 1, "tool": retrieval_tool,
             "goal": retrieval_goal, "args": {}},
            {"step_id": 2, "tool": "llm_synthesis",
             "goal": "综合分析数据生成回答", "args": {}},
        ],
        "tool_names": [retrieval_tool, "llm_synthesis"],
    })

    yield _sse({"type": "progress", "phase": "data_lookup", "message": progress_msg})

    # ── Step 1: Retrieve (concurrent for multi-entity) ───────────
    entity_data: dict = {}

    if len(entities) == 1:
        tool = ToolRegistry.create("get_entity_card", db)
        r = tool.run(name=entities[0])
        if r.success and r.data:
            entity_data["entity_card"] = r.data
            logger.info(f"[DataAgent] entity_card hit for '{entities[0]}'")
        else:
            logger.info(f"[DataAgent] entity_card miss for '{entities[0]}' → fallback search_memory")

    elif len(entities) >= 2:
        prop = _extract_property(query)
        targets = entities[:3]

        # Run get_entity_card for each entity + compare_values concurrently
        def _fetch_card(name: str) -> tuple[str, dict | None]:
            t = ToolRegistry.create("get_entity_card", db)
            res = t.run(name=name)
            return name, (res.data if res.success and res.data else None)

        def _fetch_compare() -> dict | None:
            t = ToolRegistry.create("compare_values", db)
            res = t.run(property_name=prop, entity_names=targets, namespace=namespace)
            return res.data if res.success and res.data else None

        cards: dict[str, dict] = {}
        comparison: dict | None = None

        with ThreadPoolExecutor(max_workers=len(targets) + 1) as pool:
            card_futures = {pool.submit(_fetch_card, e): e for e in targets}
            cmp_future   = pool.submit(_fetch_compare)

            for fut in as_completed(list(card_futures) + [cmp_future]):
                if fut is cmp_future:
                    comparison = fut.result()
                    if comparison:
                        logger.info(f"[DataAgent] compare_values ok, prop={prop}")
                else:
                    name, card = fut.result()
                    if card:
                        cards[name] = card
                        logger.info(f"[DataAgent] entity_card ok: {name}")

        if cards:
            entity_data["entity_cards"] = cards
        if comparison:
            entity_data["comparison"] = comparison

    # Fallback / no entity → semantic search
    if not entity_data:
        yield _sse({"type": "progress", "phase": "data_lookup",
                    "message": "实体卡未命中，切换语义检索…"})
        tool = ToolRegistry.create("search_memory", db)
        r = tool.run(query=query, namespace=namespace, top_k=10)
        if r.success and r.data:
            entity_data["search_results"] = r.data
            logger.info("[DataAgent] fallback search_memory ok")

    # ── Emit sources event ────────────────────────────────────────
    sources: list[dict] = []
    if "entity_card" in entity_data:
        card = entity_data["entity_card"]
        sources.append({
            "title": card.get("canonical_name", "实体数据"),
            "snippet": f"类型: {card.get('entity_type', '')} · {len(card.get('properties', []))} 个属性",
            "kind": "entity",
        })
    if "entity_cards" in entity_data:
        for name, card in entity_data["entity_cards"].items():
            sources.append({
                "title": name,
                "snippet": f"类型: {card.get('entity_type', '')} · {len(card.get('properties', []))} 个属性",
                "kind": "entity",
            })
    if "search_results" in entity_data:
        for chunk in (entity_data["search_results"].get("results") or [])[:5]:
            sources.append({
                "title": chunk.get("document_title") or "文档",
                "snippet": (chunk.get("text") or chunk.get("context") or "")[:120],
                "kind": "chunk",
                "doc_id": chunk.get("document_id"),
            })
    if sources:
        yield _sse({"type": "sources", "sources": sources})

    if not entity_data:
        yield _sse({"type": "token", "content": "抱歉，未检索到相关数据，请尝试换一种表达方式。"})
        yield _sse({"type": "done", "elapsed_secs": int(time.monotonic() - t_start)})
        return

    # ── Step 2: LLM synthesis (with history, 3-stage retry) ──────
    yield _sse({"type": "progress", "phase": "synthesizing", "message": "正在综合分析…"})

    user_content = (
        f"用户问题：{query}\n\n"
        f"检索到的数据：\n{json.dumps(entity_data, ensure_ascii=False, indent=2)}"
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
        temperature=0.1,
        log_prefix="[DataAgent]",
        error_msg="数据检索完成，但生成回答时出错，请重试。",
    )

    yield _sse({"type": "done", "elapsed_secs": int(time.monotonic() - t_start)})
