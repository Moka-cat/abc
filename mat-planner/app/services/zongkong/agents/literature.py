"""LiteratureAgent — thin wrapper around the existing run_agent_stream.

Responsibilities:
  - Skip the sub-agent's own `start` event (orchestrator already sent one).
  - Add intent-classification overhead to elapsed_secs in the `done` event
    so total wall-clock time is accurate.
  - Pass through all other events unchanged.
"""
from __future__ import annotations

import json
import logging
from typing import Generator

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def run_stream(
    query: str,
    db: Session,
    history: list[dict],
    namespace: str = "default",
    classify_secs: int = 0,
) -> Generator[str, None, None]:
    from app.services.agent import run_agent_stream

    skip_next_start = True   # suppress the first `start` from sub-agent

    for chunk in run_agent_stream(query, db, history=history, namespace=namespace):
        raw = chunk.removeprefix("data: ").strip()
        try:
            data = json.loads(raw)
        except Exception:
            yield chunk
            continue

        evt = data.get("type")

        # Drop duplicate start
        if evt == "start" and skip_next_start:
            skip_next_start = False
            continue
        skip_next_start = False

        # Adjust elapsed_secs to include classification overhead
        if evt == "done" and classify_secs > 0:
            data["elapsed_secs"] = (data.get("elapsed_secs") or 0) + classify_secs
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            continue

        yield chunk
