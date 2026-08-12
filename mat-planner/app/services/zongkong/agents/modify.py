"""ModifyAgent — iterative experiment refinement based on previous result.

Flow:
  1. Extract the previous experiment report from history (last assistant turn)
  2. Build an enriched goal: modification request + previous protocol context
  3. Delegate to ExperimentAgent with enriched goal
     (ExperimentAgent runs design_experiment + check_safety + synthesis)

The key difference from ExperimentAgent called directly:
  - design_experiment receives the previous formulation as part of the goal text,
    so the LLM starts from the existing formulation rather than scratch
  - Safety check still runs on all new candidates
"""
from __future__ import annotations

import json
import logging
import time
from typing import Generator

from sqlalchemy.orm import Session

from app.services.zongkong.agents import experiment as experiment_agent

logger = logging.getLogger(__name__)


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _extract_previous_result(history: list[dict]) -> str:
    """Return the last assistant message from history, or empty string."""
    for turn in reversed(history):
        if turn.get("role") == "assistant" and turn.get("content", "").strip():
            return turn["content"].strip()
    return ""


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

    previous_result = _extract_previous_result(history)

    if previous_result:
        logger.info(
            f"[ModifyAgent] found previous result ({len(previous_result)} chars), "
            f"building enriched goal"
        )
        enriched_goal = (
            f"修改要求：{query}\n\n"
            f"[上一次实验方案（在此基础上修改，尽量保留合理部分，只调整被明确指出的参数）]\n"
            f"{previous_result}"
        )
        yield _sse({"type": "progress", "phase": "modify",
                    "message": "✏️ 已定位上一次方案，正在按要求修改…"})
    else:
        logger.info("[ModifyAgent] no previous result found, treating as fresh experiment")
        enriched_goal = query
        yield _sse({"type": "progress", "phase": "modify",
                    "message": "✏️ 未找到上一次方案，按新需求设计…"})

    # Delegate to ExperimentAgent with enriched goal
    yield from experiment_agent.run_stream(
        query=enriched_goal,
        db=db,
        namespace=namespace,
        t_start=t_start,
        history=history,
    )
