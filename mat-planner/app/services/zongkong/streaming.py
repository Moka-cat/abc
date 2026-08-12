"""Shared streaming utility — 3-stage retry for sub-agent LLM synthesis.

Stages:
  1. Stream normally (max_retries=2 for transient errors)
  2. If 0 tokens received: sleep 3s → retry stream
  3. If still 0 tokens: sleep 2s → non-streaming fallback (max_retries=3, timeout=360s)
     → simulate streaming by chunking the response at 80 chars

Usage:
    from app.services.zongkong.streaming import stream_synthesis
    yield from stream_synthesis(messages, temperature=0.1, log_prefix="[DataAgent]")
"""
from __future__ import annotations

import logging
import time
from typing import Generator

from app.core.config import settings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 80   # chars per simulated-stream chunk in fallback


def stream_synthesis(
    messages: list[dict],
    temperature: float = 0.1,
    log_prefix: str = "[Synthesis]",
    error_msg: str = "抱歉，生成回答失败，请重试。",
) -> Generator[str, None, None]:
    """Yield SSE token strings with 3-stage fallback.

    Yields strings of the form: 'data: {"type":"token","content":"..."}\n\n'
    Caller is responsible for emitting the final 'done' event.
    """
    import json
    from openai import OpenAI

    def _sse_token(content: str) -> str:
        return f"data: {json.dumps({'type': 'token', 'content': content}, ensure_ascii=False)}\n\n"

    base_url = settings.LLM_BASE_URL.rstrip("/") + "/v1"
    model = settings.LLM_MODEL
    tokens: list[str] = []

    # ── Attempt 1: streaming ──────────────────────────────────────
    stream_ok = False
    try:
        client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=base_url,
            max_retries=2,
            timeout=None,
        )
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            temperature=temperature,
        )
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if delta:
                tokens.append(delta)
                yield _sse_token(delta)
        stream_ok = True
        logger.info(f"{log_prefix} stream ok, {len(tokens)} chunks")
    except Exception as exc:
        logger.warning(f"{log_prefix} stream error (tokens so far: {len(tokens)}): {exc!r}")

    # ── Attempt 2: retry stream after 3s ─────────────────────────
    if not stream_ok and not tokens:
        logger.info(f"{log_prefix} retrying stream after 3s…")
        time.sleep(3)
        try:
            client2 = OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=base_url,
                max_retries=2,
                timeout=None,
            )
            stream2 = client2.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                temperature=temperature,
            )
            for chunk in stream2:
                delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if delta:
                    tokens.append(delta)
                    yield _sse_token(delta)
            stream_ok = True
            logger.info(f"{log_prefix} stream retry ok, {len(tokens)} chunks")
        except Exception as exc2:
            logger.warning(f"{log_prefix} stream retry error: {exc2!r}")

    # ── Attempt 3: non-streaming fallback ─────────────────────────
    if not stream_ok and not tokens:
        logger.info(f"{log_prefix} falling back to non-streaming…")
        time.sleep(2)
        try:
            fb_client = OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=base_url,
                max_retries=3,
                timeout=360.0,
            )
            resp = fb_client.chat.completions.create(
                model=model,
                messages=messages,
                stream=False,
                temperature=temperature,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content:
                logger.info(f"{log_prefix} fallback got {len(content)} chars, simulating stream")
                for i in range(0, len(content), _CHUNK_SIZE):
                    piece = content[i:i + _CHUNK_SIZE]
                    tokens.append(piece)
                    yield _sse_token(piece)
            else:
                logger.error(f"{log_prefix} fallback returned empty content")
                yield _sse_token(error_msg)
        except Exception as exc3:
            logger.error(f"{log_prefix} all 3 attempts failed: {exc3!r}")
            yield _sse_token(error_msg)
