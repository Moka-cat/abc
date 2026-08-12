"""Formulation/composition extractor for propellant and energetic material texts.

Detects three patterns commonly found in literature:
  Pattern A: "AP/HTPB/Al = 68/20/12 wt%" (slash notation)
  Pattern B: "78 wt% RDX, 18 wt% HTPB, 4 wt% DOS" (list notation)
  Pattern C: "AP(68%)/HTPB(20%)/Al(12%)" (inline percentage notation)

Returns a list of ExtractedFormulation dataclasses.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from app.pipeline.domain_extractor import KNOWN_ENTITIES


@dataclass
class ExtractedComponent:
    component_name: str  # canonical entity name
    mass_fraction: float | None  # wt%, 0-100
    role: str | None  # oxidizer/binder/fuel/explosive/plasticizer/catalyst


@dataclass
class ExtractedFormulation:
    components: list[ExtractedComponent] = field(default_factory=list)
    formulation_type: str = "general"  # composite_propellant/plastic_explosive/melt_cast/pyrotechnic/general
    description: str = ""  # raw text excerpt


# Build a lookup: lowercase name/alias → canonical name
def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, info in KNOWN_ENTITIES.items():
        lookup[canonical.lower()] = canonical
        for alias in info["aliases"]:
            lookup[alias.lower()] = canonical
    return lookup


_LOOKUP = _build_lookup()

# Role inference from entity type
_TYPE_TO_ROLE: dict[str, str] = {
    "oxidizer": "oxidizer",
    "binder": "binder",
    "fuel": "fuel",
    "plasticizer": "plasticizer",
    "catalyst": "catalyst",
    "compound": "explosive",
    "ingredient": "additive",
}


def _resolve(name: str) -> str | None:
    """Resolve a name/alias to canonical entity name."""
    return _LOOKUP.get(name.strip().lower())


def _infer_role(canonical: str) -> str | None:
    info = KNOWN_ENTITIES.get(canonical, {})
    return _TYPE_TO_ROLE.get(info.get("type", ""), None)


def _classify_formulation(components: list[ExtractedComponent]) -> str:
    """Infer formulation type from component roles."""
    roles = {c.role for c in components if c.role}
    names = {c.component_name for c in components}
    # Composite propellant: has oxidizer (AP/AN/ADN/KP) + binder (HTPB/PBAN etc.)
    oxidizers = {"AP", "AN", "ADN", "KP", "KNO3", "NaNO3", "HNF"}
    binders = {"HTPB", "PBAN", "CTPB", "PBAA", "GAP", "CAB", "PEG", "Estane", "PDMS"}
    explosives = {"RDX", "HMX", "TNT", "PETN", "CL-20", "FOX-7", "TATB", "NTO", "TKX-50",
                  "NG", "NC", "DNTF", "ADN", "LLM-105", "NQ", "DNTF"}
    if names & oxidizers and names & binders:
        return "composite_propellant"
    # Plastic explosive: high explosive content (>60%) + binder
    explosive_fraction = sum(c.mass_fraction or 0 for c in components
                             if c.component_name in explosives)
    if explosive_fraction > 60 and names & binders:
        return "plastic_explosive"
    # Melt-cast: TNT as matrix + solid explosive
    if "TNT" in names and names & (explosives - {"TNT"}):
        return "melt_cast"
    # Pyrotechnic: metal fuel + oxidizer, no binder
    metals = {"Al", "Mg", "B", "Zr", "Ti", "W"}
    if names & metals and names & oxidizers and not names & binders:
        return "pyrotechnic"
    return "general"


# ── Pattern A: "AP/HTPB/Al = 68/20/12" ──────────────────────────────────────
# Also matches "AP/HTPB/Al (68/20/12 wt%)" and "AP/HTPB/Al: 68/20/12"
_SLASH_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9/\-]*(?:/[A-Za-z][A-Za-z0-9\-]*){1,8})"  # names
    r"\s*[=:(]\s*"
    r"([\d.]+(?:/[\d.]+){1,8})"                                      # ratios
    r"(?:\s*(?:wt%?|%|by\s+weight|mass%))?",
    re.IGNORECASE,
)


def _parse_slash(match: re.Match) -> ExtractedFormulation | None:
    names_str, ratios_str = match.group(1), match.group(2)
    names = names_str.split("/")
    ratios = [float(r) for r in ratios_str.split("/") if r]
    if len(names) != len(ratios):
        return None
    total = sum(ratios)
    if total <= 0:
        return None
    components = []
    for name, ratio in zip(names, ratios):
        canonical = _resolve(name)
        if canonical is None:
            return None  # all names must be known entities
        mass_frac = ratio / total * 100 if abs(total - 100) > 1 else ratio
        components.append(ExtractedComponent(
            component_name=canonical,
            mass_fraction=round(mass_frac, 2),
            role=_infer_role(canonical),
        ))
    if not components:
        return None
    ft = _classify_formulation(components)
    return ExtractedFormulation(
        components=components,
        formulation_type=ft,
        description=match.group(0)[:200],
    )


# ── Pattern B: "78 wt% RDX, 18 wt% HTPB" ───────────────────────────────────
_WTP_ITEM = re.compile(
    r"([\d.]+)\s*(?:wt%?|%\s*(?:by\s+weight)?|mass%)\s+([A-Za-z][A-Za-z0-9\-]+)",
    re.IGNORECASE,
)


def _parse_wtp(text: str) -> list[ExtractedFormulation]:
    """Find all groups of consecutive wt% items in text."""
    results = []
    # Find all matches and group consecutive ones (within 80 chars)
    matches = list(_WTP_ITEM.finditer(text))
    if len(matches) < 2:
        return []
    i = 0
    while i < len(matches):
        group = [matches[i]]
        j = i + 1
        while j < len(matches) and matches[j].start() - matches[j-1].end() < 80:
            group.append(matches[j])
            j += 1
        if len(group) >= 2:
            components = []
            for m in group:
                canonical = _resolve(m.group(2))
                if canonical:
                    components.append(ExtractedComponent(
                        component_name=canonical,
                        mass_fraction=float(m.group(1)),
                        role=_infer_role(canonical),
                    ))
            if len(components) >= 2:
                ft = _classify_formulation(components)
                snippet = text[group[0].start():group[-1].end()][:200]
                results.append(ExtractedFormulation(
                    components=components, formulation_type=ft, description=snippet
                ))
        i = j if j > i + 1 else i + 1
    return results


# ── Pattern C: "AP(68%)/HTPB(20%)/Al(12%)" ──────────────────────────────────
_INLINE_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9\-]+)\(([\d.]+)%?\)"
    r"(?:\s*/\s*([A-Za-z][A-Za-z0-9\-]+)\(([\d.]+)%?\)){1,7}",
    re.IGNORECASE,
)


def _parse_inline(match: re.Match) -> ExtractedFormulation | None:
    # Extract all (name, percent) pairs from the match string
    pairs = re.findall(r"([A-Za-z][A-Za-z0-9\-]+)\(([\d.]+)%?\)", match.group(0))
    components = []
    for name, frac_str in pairs:
        canonical = _resolve(name)
        if canonical is None:
            return None
        components.append(ExtractedComponent(
            component_name=canonical,
            mass_fraction=float(frac_str),
            role=_infer_role(canonical),
        ))
    if len(components) < 2:
        return None
    ft = _classify_formulation(components)
    return ExtractedFormulation(
        components=components, formulation_type=ft, description=match.group(0)[:200]
    )


def extract_formulations(text: str) -> list[ExtractedFormulation]:
    """Extract all formulation compositions from a text chunk.

    Returns a deduplicated list of ExtractedFormulation.
    Each formulation must have ≥2 known components.
    """
    results: list[ExtractedFormulation] = []
    seen: set[frozenset] = set()  # dedup by component set

    def _add(f: ExtractedFormulation | None) -> None:
        if f is None or len(f.components) < 2:
            return
        key = frozenset((c.component_name, round(c.mass_fraction or 0)) for c in f.components)
        if key in seen:
            return
        seen.add(key)
        results.append(f)

    # Pattern A
    for m in _SLASH_PATTERN.finditer(text):
        _add(_parse_slash(m))

    # Pattern C (must come before B to avoid partial matches)
    for m in _INLINE_PATTERN.finditer(text):
        _add(_parse_inline(m))

    # Pattern B
    for f in _parse_wtp(text):
        _add(f)

    return results
