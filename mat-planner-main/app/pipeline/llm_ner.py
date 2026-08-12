"""LLM-assisted NER: supplements regex extraction with an LLM pass.

Only runs on chunks that:
  1. Contain at least one known entity name
  2. Contain numeric values (likely property data)

The LLM is asked to return a strict JSON array of {entity, property, value, unit, quote}.
Results are then unit-normalized, plausibility-filtered, and merged with regex output.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loguru import logger

from app.core.config import settings
from app.pipeline.domain_extractor import (
    KNOWN_ENTITIES,
    ExtractedProperty,
    normalize_unit,
    _PLAUSIBILITY,
)
from app.services.llm_client import get_llm_client

# ── Configuration ─────────────────────────────────────────────────
# Max characters to send to LLM per chunk (trim to avoid huge prompts)
_MAX_CHUNK_CHARS = 3000
# Min digits in a chunk to justify an LLM call (avoid calling on pure text)
_MIN_DIGITS = 3

# Canonical property names the LLM must use
_PROPERTIES = [
    "density",
    "detonation_velocity",
    "detonation_pressure",
    "melting_point",
    "burning_rate",
    "heat_of_explosion",
    "particle_size",
    "oxygen_balance",
    "impact_sensitivity",
    "friction_sensitivity",
    "specific_impulse",
    "burn_rate_exponent",
    "decomposition_temperature",
    "glass_transition_temperature",
]

# All entity names (canonical + aliases) for quick mention detection
_ENTITY_NAMES: list[str] = []
for _canonical, _info in KNOWN_ENTITIES.items():
    _ENTITY_NAMES.append(_canonical.lower())
    for _alias in _info["aliases"]:
        _ENTITY_NAMES.append(_alias.lower())

_SYSTEM_PROMPT = f"""You are a data extraction assistant for energetic materials science.
Extract all measurable property values from the provided text.

For each value found, output one JSON object:
{{
  "entity": "<compound or ingredient name>",
  "property": "<one of: {', '.join(_PROPERTIES)}>",
  "value": <number>,
  "unit": "<unit string>",
  "quote": "<exact text excerpt ≤80 chars containing the value>",
  "condition": {{"pressure_MPa": <number>}} or {{"temperature_C": <number>}} or null
}}

Rules:
- ONLY extract values that appear explicitly as numbers in the text.
- Do NOT infer, calculate, or hallucinate any values.
- For LaTeX values like \\(\\rho=1.82\\,\\mathrm{{g\\,cm}}^{{-3}}\\), extract 1.82 g/cm³.
- If the text uses Kelvin, output unit "K". If Celsius, "°C". If GPa, "GPa". If kbar, "kbar".
- For "condition": extract the measurement pressure or temperature mentioned near the value (e.g. "at 7 MPa" → {{"pressure_MPa": 7.0}}, "at 25°C" → {{"temperature_C": 25.0}}). Use null if no condition is stated.
- Return [] if no property values can be found.
- Return ONLY a JSON array (no markdown, no explanation).
"""


def _has_entity_mention(text_lower: str) -> bool:
    return any(name in text_lower for name in _ENTITY_NAMES)


def _has_numeric_data(text: str) -> bool:
    return len(re.findall(r"\d", text)) >= _MIN_DIGITS


def llm_extract_properties(
    chunk_text: str,
    chunk_id: str = "",
) -> list[ExtractedProperty]:
    """Call LLM to extract properties from a single chunk.

    Returns [] if:
    - LLM_NER_ENABLED is False
    - LLM_MODEL or LLM_BASE_URL not configured
    - No entity mention found
    - No numeric data in chunk
    - LLM returns no results
    """
    if not settings.LLM_NER_ENABLED:
        return []
    if not settings.LLM_MODEL or not settings.LLM_BASE_URL:
        return []

    text_lower = chunk_text.lower()
    if not _has_entity_mention(text_lower):
        return []
    if not _has_numeric_data(chunk_text):
        return []

    # Truncate very long chunks to avoid huge prompts
    text = chunk_text[:_MAX_CHUNK_CHARS]

    client = get_llm_client()
    try:
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tool_choice="none",
            timeout=15.0,
        )
        raw = (resp.choices[0].message.content or "[]").strip()
        # Strip markdown fences
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
    except Exception as exc:
        logger.debug(f"LLM NER failed for chunk {chunk_id[:8]}: {exc}")
        return []

    results: list[ExtractedProperty] = []
    for item in items:
        entity = str(item.get("entity", "")).strip()
        prop = str(item.get("property", "")).strip().lower().replace(" ", "_")
        unit = str(item.get("unit", "")).strip()
        quote = str(item.get("quote", "")).strip()[:200]

        try:
            value = float(item.get("value", ""))
        except (TypeError, ValueError):
            continue

        # Validate property name
        if prop not in _PROPERTIES:
            continue

        # Resolve entity to canonical name
        entity_lower = entity.lower()
        canonical = None
        for can, info in KNOWN_ENTITIES.items():
            all_names = [can] + info["aliases"]
            if any(entity_lower == n.lower() for n in all_names):
                canonical = can
                break
        if canonical is None:
            # Accept if it's a close match (starts-with) to a canonical name
            for can in KNOWN_ENTITIES:
                if entity_lower.startswith(can.lower()):
                    canonical = can
                    break
        if canonical is None:
            logger.debug(f"LLM NER: unknown entity '{entity}' — skipped")
            continue

        # Unit normalization
        if unit:
            value, unit = normalize_unit(prop, value, unit)

        # Plausibility gate
        lo, hi = _PLAUSIBILITY.get(prop, (-1e18, 1e18))
        if not (lo <= value <= hi):
            logger.debug(f"LLM NER: implausible {canonical}.{prop}={value} {unit} — skipped")
            continue

        condition = item.get("condition") if isinstance(item.get("condition"), dict) else None

        results.append(ExtractedProperty(
            entity_name=canonical,
            property_name=prop,
            value_text=f"{value:g} {unit}",
            value_numeric=round(value, 6),
            unit=unit,
            condition=condition,
            evidence_quote=quote,
            evidence_offset=0,
        ))

    if results:
        logger.debug(f"LLM NER extracted {len(results)} properties from chunk {chunk_id[:8]}")
    return results
