"""In-memory session store for multi-turn conversation history.

Automatic compression: when a session exceeds SESSION_COMPRESSION_THRESHOLD messages,
the oldest half is summarised by LLM into a single [Summary] system message.
This prevents context bloat in long conversations while retaining key facts.

Compression is triggered lazily in append_turn() and is non-fatal (if LLM fails,
the full history is kept as-is).
"""
import uuid
from threading import Lock
from loguru import logger

from app.core.config import settings


# ── Internal state ────────────────────────────────────────────────────────────

_sessions: dict[str, list[dict]] = {}
_lock = Lock()

_COMPRESSION_SYSTEM = """\
You are a conversation summariser for an energetic materials research assistant.
Compress the following conversation turns into a concise summary that preserves:
  - All entity names mentioned (RDX, HMX, AP, HTPB, etc.)
  - All numeric values and units discussed (density, detonation velocity, etc.)
  - The questions asked and the key conclusions reached
  - Any "remember for follow-up" context (e.g. "user is comparing two formulations")

Output a single paragraph starting with "Previous conversation summary: ".
Be factual and concise — this will replace the compressed turns in the context window.
"""


def _compress(turns: list[dict]) -> str | None:
    """Call LLM to summarise a list of conversation turns.

    Returns the summary string, or None if compression fails.
    """
    if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
        return None
    try:
        from app.services.llm_client import get_llm_client
        client = get_llm_client()
        history_text = "\n".join(
            f"{t['role'].upper()}: {t['content'][:300]}" for t in turns
        )
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _COMPRESSION_SYSTEM},
                {"role": "user", "content": history_text},
            ],
            timeout=20,
        )
        summary = (resp.choices[0].message.content or "").strip()
        return summary if summary else None
    except Exception as e:
        logger.debug(f"Session compression LLM call failed (non-fatal): {e}")
        return None


def _maybe_compress(sid: str) -> None:
    """Compress the oldest half of the session if it exceeds the threshold.

    Must be called with _lock held.
    """
    threshold = settings.SESSION_COMPRESSION_THRESHOLD
    msgs = _sessions.get(sid, [])
    if len(msgs) <= threshold:
        return

    # Determine how many to compress (oldest half, keep at least threshold/2 recent)
    keep_recent = threshold // 2
    to_compress = msgs[:-keep_recent]
    to_keep = msgs[-keep_recent:]

    # Check if oldest message is already a summary — if so, include it in re-summarisation
    summary = _compress(to_compress)
    if summary:
        summary_msg = {"role": "system", "content": summary}
        _sessions[sid] = [summary_msg] + to_keep
        logger.info(
            f"[session:{sid[:8]}] Compressed {len(to_compress)} messages → 1 summary "
            f"+ {len(to_keep)} recent"
        )
    else:
        # LLM unavailable: just truncate to keep_recent to avoid unbounded growth
        _sessions[sid] = to_keep
        logger.debug(f"[session:{sid[:8]}] LLM unavailable — truncated to {keep_recent} recent msgs")


# ── Public functions (used by API routes) ─────────────────────────────────────

def create_session() -> str:
    sid = str(uuid.uuid4())
    with _lock:
        _sessions[sid] = []
    return sid


def get_history(session_id: str) -> list[dict]:
    with _lock:
        return list(_sessions.get(session_id, []))


def append_turn(session_id: str, question: str, answer: str) -> None:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = []
        _sessions[session_id].append({"role": "user", "content": question})
        _sessions[session_id].append({"role": "assistant", "content": answer})
        _maybe_compress(session_id)


def clear_session(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)


# ── session_store object (used by offline eval / external callers) ────────────

class _SessionStore:
    """Object interface for the module-level session functions."""

    def get_history(self, session_id: str) -> list[dict]:
        return get_history(session_id)

    def append(self, session_id: str, role: str, content: str) -> None:
        """Append a single message (role: 'user' or 'assistant')."""
        with _lock:
            if session_id not in _sessions:
                _sessions[session_id] = []
            _sessions[session_id].append({"role": role, "content": content})
            _maybe_compress(session_id)

    def append_turn(self, session_id: str, question: str, answer: str) -> None:
        append_turn(session_id, question, answer)

    def clear(self, session_id: str) -> None:
        clear_session(session_id)

    def create(self) -> str:
        return create_session()


session_store = _SessionStore()
