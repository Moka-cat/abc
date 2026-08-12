"""Chat endpoints — POST /chat  and  POST /chat/stream."""
import json
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.agent import run_agent
from app.services.zongkong.orchestrator import run_stream as _orchestrator_stream
from app.services.session import create_session, get_history, append_turn, clear_session, _sessions

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None   # None = stateless (old behavior)
    namespace: str = "default"       # document namespace to search within


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    plan: list[dict]
    iterations: int
    tool_calls_made: list[dict]
    confidence: str = "medium"         # high | medium | low | none
    confidence_reason: str = ""


# ── Standard (non-streaming) endpoint ────────────────────────────

@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    sid = req.session_id or create_session()
    history = get_history(sid) if req.session_id else []

    result = run_agent(req.question, db, history=history, namespace=req.namespace)
    append_turn(sid, req.question, result.answer)

    return ChatResponse(
        answer=result.answer,
        session_id=sid,
        plan=[
            {"step_id": s.step_id, "tool": s.tool, "goal": s.goal, "args": s.args}
            for s in result.plan
        ],
        iterations=result.iterations,
        tool_calls_made=result.tool_calls_made,
        confidence=result.confidence,
        confidence_reason=result.confidence_reason,
    )


# ── Streaming endpoint (SSE) ──────────────────────────────────────
#
# SSE event format (each line is a JSON object):
#   {"type": "plan",  "plan": [...], "tool_names": [...]}  → emitted once, before answer
#   {"type": "token", "content": "..."}                    → emitted per token
#   {"type": "done"}                                        → final event
#
# curl example:
#   curl -N -X POST http://localhost:8000/chat/stream \
#     -H "Content-Type: application/json" \
#     -d '{"question": "RDX的密度是多少？"}'

@router.post("/stream")
def chat_stream(req: ChatRequest, db: Session = Depends(get_db)):
    sid = req.session_id or create_session()
    history = get_history(sid) if req.session_id else []
    full_answer: list[str] = []

    def _event_stream():
        for event in _orchestrator_stream(req.question, db, history=history, namespace=req.namespace):
            # Capture tokens to save to session history after streaming
            try:
                data = json.loads(event.removeprefix("data: ").strip())
                if data.get("type") == "token":
                    full_answer.append(data.get("content", ""))
                elif data.get("type") == "done":
                    # Save completed answer to session
                    answer = "".join(full_answer)
                    append_turn(sid, req.question, answer)
                    # Augment done event with session_id, preserving timing fields
                    augmented = {**data, "session_id": sid}
                    yield f"data: {json.dumps(augmented, ensure_ascii=False)}\n\n"
                    return
            except Exception:
                pass
            yield event

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


# ── Session management ────────────────────────────────────────────

@router.get("/sessions", response_model=list[dict])
def list_sessions() -> list[dict]:
    """List all active session IDs with message counts."""
    from app.services.session import _sessions, _lock
    with _lock:
        return [
            {"session_id": sid, "message_count": len(msgs)}
            for sid, msgs in _sessions.items()
        ]


@router.get("/sessions/{session_id}", response_model=list[dict])
def get_session_history(session_id: str) -> list[dict]:
    """Retrieve the full message history for a session.

    Returns list of {role: 'user'|'assistant'|'system', content: str}.
    """
    history = get_history(session_id)
    if not history:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found or empty")
    return history


@router.delete("/{session_id}")
def delete_session(session_id: str):
    clear_session(session_id)
    return {"deleted": session_id}
