"""Context utilities — history compression for long conversations.

When a conversation accumulates many turns, the history passed to sub-agents
grows too large, consuming tokens and slowing synthesis.

compress_history():
  - Triggered when total character count > COMPRESS_THRESHOLD (≈ 8000 chars ≈ 2000 tokens)
  - Uses LLM to summarize older turns into a compact system message
  - Returns: [{"role": "system", "content": "[历史摘要]\n..."}] + last KEEP_RECENT turns
  - Falls back to simple truncation if LLM call fails
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

COMPRESS_THRESHOLD = 8_000   # chars — compress when history text exceeds this
KEEP_RECENT        = 6       # number of most-recent turns (messages) to preserve verbatim

_COMPRESS_SYSTEM = """\
你是对话摘要助手。将下方的含能材料研究对话历史压缩成一段简洁摘要（不超过 300 字）。

摘要要求：
- 保留用户的核心研究目标和关键物质/配方名称
- 保留助手给出的重要结论、数值、方案名称
- 用第三人称：「用户询问了…，助手指出…」
- 不需要保留问候语、重复内容、过渡语
"""


def _total_chars(history: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in history)


def compress_history(history: list[dict]) -> list[dict]:
    """Return a compressed version of history if it exceeds the threshold.

    Always returns a valid history list safe to pass to LLM synthesis.
    """
    if not history or _total_chars(history) <= COMPRESS_THRESHOLD:
        return history

    # Split: older part to summarize, recent part to keep verbatim
    keep    = history[-KEEP_RECENT:]
    to_sum  = history[:-KEEP_RECENT]

    if not to_sum:
        return history  # nothing to compress

    logger.info(
        f"[context] compressing {len(to_sum)} turns "
        f"({_total_chars(to_sum)} chars) into summary"
    )

    # Build a plain-text transcript of the older turns
    lines: list[str] = []
    for msg in to_sum:
        role    = msg.get("role", "unknown")
        content = (msg.get("content", "") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        prefix = "用户：" if role == "user" else "助手："
        lines.append(f"{prefix}{content[:400]}")   # cap per turn to save tokens

    transcript = "\n\n".join(lines)

    # LLM summary
    summary_text = ""
    try:
        from app.core.config import settings
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL.rstrip("/") + "/v1",
            max_retries=1,
            timeout=30.0,
        )
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {"role": "user",   "content": f"对话历史：\n{transcript}"},
            ],
            max_tokens=400,
            temperature=0.0,
        )
        summary_text = (resp.choices[0].message.content or "").strip()
        logger.info(f"[context] summary generated ({len(summary_text)} chars)")
    except Exception as exc:
        logger.warning(f"[context] LLM compression failed ({exc!r}), using truncation")
        # Fallback: use the last 200 chars of the transcript as a rough summary
        summary_text = f"（早期对话已截断）上下文节选：\n{transcript[-500:]}"

    if not summary_text:
        return keep

    summary_msg = {
        "role":    "system",
        "content": f"[历史对话摘要]\n{summary_text}",
    }
    return [summary_msg] + keep
