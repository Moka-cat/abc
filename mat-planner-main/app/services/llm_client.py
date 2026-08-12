"""Thin wrapper around the OpenAI-compatible LLM client."""
import httpx
from openai import OpenAI

from app.core.config import settings

# Explicit per-phase timeouts to prevent silent hangs:
#   connect: fail fast if the proxy is unreachable
#   read:    30 s with no data → ReadTimeout (prevents indefinite stall)
#   write:   request upload shouldn't take long
#   pool:    don't wait long for a connection slot
_PHASE1_TIMEOUT = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=5.0)


def get_llm_client() -> OpenAI:
    return OpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL.rstrip("/") + "/v1",
        max_retries=1,
        http_client=httpx.Client(timeout=_PHASE1_TIMEOUT),
    )
