"""Coreference resolution for multi-turn conversations.

When a user writes "那它的爆速呢？" or "该物质的感度是多少？", the pronoun
("它"/"该物质") refers to an entity mentioned in the previous turn.  The intent
classifier receives only the bare query string, so it cannot extract a useful
entity name from a pronoun — and the DataAgent gets an empty entity list.

resolve_query() enriches the current query by:
  1. Detecting pronoun / deictic expressions in the query.
  2. Extracting the most-recent entity names from conversation history.
  3. Replacing the pronoun with the resolved entity name (inline substitution),
     OR appending a context hint "[指代: X]" when direct substitution is unclear.

This is intentionally heuristic and lightweight — no extra LLM call.
It runs synchronously in the orchestrator before intent classification.

Pronoun patterns handled:
  它 / 它们 / 该物质 / 该材料 / 该配方 / 该化合物 / 这个 / 这种 / 上述 / 前述
  以上配方 / 上面 / 该药剂 / 这种材料 / 这种配方
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# Pronoun / deictic expressions that indicate coreference
_PRONOUN_PATTERNS = re.compile(
    r"(它们?|该物质|该材料|该配方|该化合物|该药剂|该炸药|该推进剂|"
    r"这个物质|这种物质|这种材料|这种配方|这种炸药|这种推进剂|"
    r"这个|这种|上述配方|上述物质|上述材料|前述|以上配方|上面提到的?)"
)

# Known energetic material names (for extraction from history)
_KNOWN_MATERIALS = {
    "RDX", "HMX", "TATB", "PETN", "TNT", "CL-20", "FOX-7", "NTO", "TKX-50",
    "AP", "AN", "ADN", "DNAN", "NG", "NC", "DNTF", "Al",
    "HTPB", "CTPB", "GAP", "PEG", "TPEB",
    "铝粉", "铝", "镁粉", "硼粉",
    "奥克托今", "黑索金", "太安", "梯恩梯",
}

# Regex to find uppercase acronyms (2-6 chars, optionally with dash+digits)
_ACRONYM_RE = re.compile(r'\b([A-Z]{3,6}(?:-\d+)?)\b')


def _extract_entities_from_text(text: str) -> list[str]:
    """Pull entity names from a single text string."""
    found: list[str] = []
    seen: set[str] = set()

    # Known material names
    for name in _KNOWN_MATERIALS:
        if name in text and name not in seen:
            found.append(name)
            seen.add(name)

    # Uppercase acronyms
    for m in _ACRONYM_RE.finditer(text):
        token = m.group(1)
        if token not in seen and len(token) >= 2:
            found.append(token)
            seen.add(token)

    return found


def _extract_entities_from_history(history: list[dict]) -> list[str]:
    """Return entity names mentioned in the most-recent history turns (newest first)."""
    entities_by_turn: list[list[str]] = []

    for msg in reversed(history):
        role    = msg.get("role", "")
        content = msg.get("content", "") or ""
        if role not in ("user", "assistant") or not content:
            continue
        ents = _extract_entities_from_text(content)
        if ents:
            entities_by_turn.append(ents)
        if len(entities_by_turn) >= 3:   # look at most 3 turns back
            break

    # Flatten, preserving recency order, dedup
    seen: set[str] = set()
    result: list[str] = []
    for turn_ents in entities_by_turn:
        for e in turn_ents:
            if e not in seen:
                result.append(e)
                seen.add(e)

    return result


def _has_pronoun(query: str) -> bool:
    return bool(_PRONOUN_PATTERNS.search(query))


def resolve_query(query: str, history: list[dict] | None) -> str:
    """Return an enriched query string with coreference resolved.

    If no pronoun is detected, or if no entity can be found in history,
    the original query is returned unchanged.

    Examples:
      history: [user: "RDX的密度", assistant: "RDX 密度为 1.82 g/cm³"]
      query:   "那它的爆速呢？"
      result:  "那RDX的爆速呢？"

      history: [user: "设计AP/HTPB/Al配方", assistant: "..."]
      query:   "该配方安全吗？"
      result:  "该配方安全吗？ [指代: AP, HTPB, Al]"
    """
    if not history:
        return query

    if not _has_pronoun(query):
        return query

    entities = _extract_entities_from_history(history)
    if not entities:
        return query

    # For a single entity: inline substitution in common pronoun slots
    if len(entities) == 1:
        entity = entities[0]
        # "它" / "它们" — no \b needed, Chinese chars have no word boundary issue
        substituted = re.sub(r'它们?', entity, query)
        # "该XXX" patterns
        substituted = re.sub(
            r'(该物质|该材料|该化合物|该药剂|该炸药|该推进剂)',
            entity,
            substituted,
        )
        if substituted != query:
            logger.info(
                f"[coreference] inline substitution: {query!r} → {substituted!r}"
            )
            return substituted

    # For multiple entities or ambiguous pronouns: append a context hint
    hint = ", ".join(entities[:4])
    enriched = f"{query} [指代: {hint}]"
    logger.info(
        f"[coreference] hint appended: {query!r} → {enriched!r}"
    )
    return enriched
