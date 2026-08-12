"""ZongkongOrchestrator — Master Control Agent.

Entry point that replaces run_agent_stream in the API layer.

SSE event sequence emitted:
  start
  progress  (routing)
  route     {"agent", "icon", "intent", "reason"}   ← new event type
  progress  (agent started)
  ...       (delegated to sub-agent)
  done      {elapsed_secs, [phase1_secs]}

Intent routing:
  data       → DataAgent  (fast property lookup)
  experiment → ExperimentAgent  (design + safety)
  hybrid     → DataAgent (collect) → ExperimentAgent (enriched query)
  literature → LiteratureAgent  (full RAG pipeline)
  modify     → ModifyAgent  (history-aware incremental edit)
  safety     → SafetyAgent  (risk assessment)

Coreference resolution (lightweight, no extra LLM call):
  Pronouns like "它/该物质/该配方" in the query are resolved against
  entity names found in recent history turns before intent classification.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Generator

from sqlalchemy.orm import Session

from app.services.zongkong.intent import classify_intent, IntentType, AGENT_META
from app.services.zongkong.context import compress_history
from app.services.zongkong.coreference import resolve_query
from app.services.zongkong.agents import data       as data_agent
from app.services.zongkong.agents import experiment as experiment_agent
from app.services.zongkong.agents import literature as literature_agent
from app.services.zongkong.agents import modify     as modify_agent
from app.services.zongkong.agents import safety     as safety_agent

logger = logging.getLogger(__name__)


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def run_stream(
    question: str,
    db: Session,
    history: list[dict] | None = None,
    namespace: str = "default",
) -> Generator[str, None, None]:
    """Top-level SSE generator.  Drop-in replacement for run_agent_stream."""
    t_start = time.monotonic()
    history = compress_history(history or [])

    # ── start ─────────────────────────────────────────────────────
    yield _sse({"type": "start"})
    yield _sse({"type": "progress", "phase": "routing",
                "message": "总控正在分析请求类型…"})

    # ── Coreference resolution (pronoun → entity, no LLM call) ────
    resolved_question = resolve_query(question, history)
    if resolved_question != question:
        logger.info(
            f"[Zongkong] coreference resolved: {question!r} → {resolved_question!r}"
        )

    # ── Intent classification ─────────────────────────────────────
    intent = classify_intent(resolved_question)
    classify_secs = int(time.monotonic() - t_start)
    meta = AGENT_META[intent.type]

    logger.info(
        f"[Zongkong] intent={intent.type.value!r}  "
        f"entity={intent.entity!r}  classify={classify_secs}s  "
        f"reason={intent.reason!r}"
    )

    # ── route event (frontend shows agent badge) ──────────────────
    yield _sse({
        "type":   "route",
        "agent":  meta["label"],
        "icon":   meta["icon"],
        "intent": intent.type.value,
        "reason": intent.reason,
    })
    yield _sse({"type": "progress", "phase": "routing",
                "message": f"{meta['icon']} 已路由至{meta['label']}"})

    # ── Delegate to sub-agent (use resolved query throughout) ────────
    q = resolved_question   # short alias

    if intent.type == IntentType.DATA:
        yield from data_agent.run_stream(
            query=q,
            entity=intent.entity,
            db=db,
            namespace=namespace,
            t_start=t_start,
            history=history,
        )

    elif intent.type == IntentType.EXPERIMENT:
        yield from experiment_agent.run_stream(
            query=q,
            db=db,
            namespace=namespace,
            t_start=t_start,
            history=history,
        )

    elif intent.type == IntentType.SAFETY:
        yield from safety_agent.run_stream(
            query=q,
            db=db,
            namespace=namespace,
            t_start=t_start,
            history=history,
        )

    elif intent.type == IntentType.MODIFY:
        yield from modify_agent.run_stream(
            query=q,
            db=db,
            namespace=namespace,
            t_start=t_start,
            history=history,
        )

    elif intent.type == IntentType.HYBRID:
        yield from _run_hybrid(
            question=q,
            entity=intent.entity,
            db=db,
            namespace=namespace,
            t_start=t_start,
            history=history,
        )

    else:  # LITERATURE — full RAG pipeline
        yield from literature_agent.run_stream(
            query=q,
            db=db,
            history=history,
            namespace=namespace,
            classify_secs=classify_secs,
        )


def _run_hybrid(
    question: str,
    entity: str,
    db: Session,
    namespace: str,
    t_start: float,
    history: list[dict],
) -> Generator[str, None, None]:
    """Chain: DataAgent (data lookup) → ExperimentAgent (design with data context).

    - Phase 1: stream DataAgent output, collect tokens, suppress its `done` event
    - Emit a separator progress event
    - Phase 2: run ExperimentAgent with enriched query (original + data summary)
    """
    yield _sse({"type": "progress", "phase": "hybrid_data",
                "message": "📊 阶段一：查询相关数据…"})

    # ── Phase 1: DataAgent — stream output, capture tokens ────────
    data_tokens: list[str] = []
    for chunk in data_agent.run_stream(
        query=question,
        entity=entity,
        db=db,
        namespace=namespace,
        t_start=t_start,
        history=history,
    ):
        raw = chunk.removeprefix("data: ").strip()
        try:
            evt = json.loads(raw)
        except Exception:
            yield chunk
            continue

        if evt.get("type") == "done":
            # Suppress DataAgent's done — orchestrator will emit final done
            continue

        if evt.get("type") == "token":
            data_tokens.append(evt.get("content", ""))

        yield chunk

    data_summary = "".join(data_tokens)

    # ── Separator + badge switch to ExperimentAgent ──────────────
    exp_meta = AGENT_META[IntentType.EXPERIMENT]
    yield _sse({
        "type":   "route",
        "agent":  exp_meta["label"],
        "icon":   exp_meta["icon"],
        "intent": IntentType.EXPERIMENT.value,
        "reason": "链式阶段二：基于数据进行实验设计",
    })
    yield _sse({"type": "progress", "phase": "hybrid_experiment",
                "message": "🔬 阶段二：基于以上数据进行实验设计…"})

    # ── Phase 2: ExperimentAgent with data context injected ───────
    enriched_query = question
    if data_summary:
        enriched_query = (
            f"{question}\n\n"
            f"[参考数据 — 阶段一查询结果]\n{data_summary}"
        )

    yield from experiment_agent.run_stream(
        query=enriched_query,
        db=db,
        namespace=namespace,
        t_start=t_start,
        history=history,
    )
