"""Planning Agent using LangGraph.

Architecture:
  ① Query Understanding  — LLM classifies + extracts entities/property
  ② Decomposition        — LLM decides if query needs subproblem split;
                           if yes, executes sub-tools in parallel (ThreadPoolExecutor)
                           and skips planner/executor
  ③ LLM Planner          — LLM generates tool execution plan (falls back to rules)
                           [skipped when decomposition ran]
  ④ Tool Executor        — Direct tool calls (no LLM) [skipped when decomposed]
  ⑤ Multi-hop ReAct      — LLM decides if more tool calls needed (MULTIHOP_ENABLED)
  ⑥ Evidence Judge       — Rules + structured LLM extraction (json_object)
  ⑦ Answer Builder       — LLM synthesizes final answer from validated evidence
  ⑧ Reflection           — LLM self-verifies answer: unit consistency, evidence
                           hallucination, contradictions; corrects if issues found
  [Replanner]            — Only on empty results; reformulates query
"""
import json
import operator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Annotated, Literal, TypedDict
from loguru import logger

from sqlalchemy.orm import Session

from langgraph.errors import GraphRecursionError
from langgraph.graph import StateGraph, END

from app.core.config import settings
from app.services.llm_client import get_llm_client
import app.tools  # noqa: F401 — triggers @ToolRegistry.register for all tools
from app.tools.registry import ToolRegistry

# OpenAI function schemas and tool dispatch — sourced from ToolRegistry.
# To add a new tool: create app/tools/my_tool.py with @ToolRegistry.register.
TOOL_SCHEMAS = ToolRegistry.schemas()

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    step_id: int
    tool: str
    goal: str
    args: dict
    result: Any = None


@dataclass
class AgentResponse:
    answer: str
    plan: list[PlanStep]
    iterations: int
    tool_calls_made: list[dict]
    confidence: str = "medium"
    confidence_reason: str = ""


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    # Input
    input: str
    history: list[dict]
    namespace: str           # document namespace for all retrieval calls

    # Query Understanding output
    query_type: Literal["entity_property", "comparison", "table", "formulation", "general"]
    entities: list[str]          # entity names mentioned (e.g. ["RDX", "HMX"])
    property_name: str           # property mentioned (e.g. "density", "detonation_velocity")

    # Planner output
    tool_sequence: list[dict]    # [{"tool": "get_entity_card", "args": {...}}, ...]

    # Executor output
    tool_results: Annotated[list[dict], operator.add]  # [{tool, args, result}, ...]

    # Multi-hop state
    multihop_count: int          # how many ReAct loops completed

    # Decomposition state
    decomposed: bool             # True when parallel subproblem execution ran

    # Evidence Judge output
    evidence_items: list[dict]   # validated items: [{entity, property, value, unit, evidence_id, source}]
    validation_errors: list[str] # rule violations found
    retry_count: int             # how many times we've retried

    # Reflection state
    reflection_done: bool        # True after reflection node ran (prevent loops)

    # Confidence scoring (set by evidence_judge, used by answer_builder)
    confidence: str              # "high" | "medium" | "low" | "none"
    confidence_reason: str       # human-readable explanation

    # Final
    response: str | None


# ---------------------------------------------------------------------------
# Tool execution helper
# ---------------------------------------------------------------------------

def _execute_tool(tool_name: str, args: dict, db: Session) -> Any:
    """Instantiate the correct tool via ToolRegistry and call .run()."""
    try:
        tool = ToolRegistry.create(tool_name, db)
    except KeyError:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        result = tool.run(**args)
    except TypeError as exc:
        return {"error": f"Tool call error: {exc}"}
    return result.data if result.success else {"error": result.error}


# ---------------------------------------------------------------------------
# Graph builder (per-invocation, closes over db)
# ---------------------------------------------------------------------------

_QUERY_UNDERSTANDING_SYSTEM = (
    "You are a query classifier for energetic materials research.\n"
    "Analyze the user question and return JSON:\n"
    "{\n"
    '  "query_type": "entity_property" | "comparison" | "table" | "formulation" | "general",\n'
    '  "entities": ["RDX", "HMX", ...],   // energetic compounds mentioned\n'
    '  "property_name": "density" | "detonation_velocity" | "burning_rate" | '
    '"detonation_pressure" | "melting_point" | "heat_of_explosion" | '
    '"particle_size" | "oxygen_balance" | ""\n'
    "}\n"
    "- entity_property: asking about one entity's specific property\n"
    "- comparison: comparing properties across multiple entities\n"
    "- table: asking about tabular data\n"
    "- formulation: asking about compositions/formulations/mixtures (e.g. 'what formulations contain AP?', 'show me composite propellant compositions with HTPB')\n"
    "- general: general literature search\n"
    "Return ONLY JSON."
)

_EVIDENCE_EXTRACTION_SYSTEM = """\
You are a data extraction assistant for energetic materials / propellant science.
Extract ALL property values from the tool results provided.

Return a JSON object with an "items" array:
{
  "items": [
    {
      "entity": "entity name (e.g. RDX, HMX, AP)",
      "property": "property name (e.g. density, detonation_velocity)",
      "value": "numeric or text value as string",
      "unit": "unit string or null if none",
      "evidence_id": "UUID from evidence_link if present, else null",
      "source": "section_path or document title"
    }
  ]
}

Rules:
- Extract EVERY distinct (entity, property, value) triple you see
- If value has no unit in the source, set unit to null
- Include text/qualitative values (e.g. sensitivity descriptions) with unit=null
- Do not invent data not present in the tool results
"""

_ANSWER_BUILDER_SYSTEM = """\
You are a senior expert in energetic materials, solid propellants, and explosives — both a researcher and an engineer.
Your role is to synthesize retrieved evidence into a well-reasoned, expert-level response, not merely to summarize it.

## Expert reasoning first
Before stating conclusions, reason like a domain expert:
1. **Flag missing specifications** — If the question is underspecified, state the assumption explicitly.
   Example: "燃速 >15 mm/s 必须对应某个压力点。以下按常见评价点 7 MPa 讨论。"
2. **Bridge evidence to conclusions** — Explain WHY the evidence supports your recommendation,
   not just WHAT the evidence says. Show the engineering logic.
3. **Provide alternatives and risks** — For design questions, give ≥2 routes with trade-offs.
   Label them clearly (推荐方案 / 备选方案 / 不建议).
4. **Acknowledge gaps honestly** — If evidence is insufficient to fully answer, say so and explain
   what additional data or tests would be needed.

## Citation rules
- Evidence items are numbered [1], [2], … in the Evidence section below.
- Cite inline whenever you state a fact: "RDX density is **1.816 g/cm³** [1]"
- If an evidence item has a "citation" blockquote (from trace_evidence), embed it:
    > "…the crystal density of RDX is **1.816 g/cm³**…"
    > — §2.1 Physicochemical Properties, p.3
- If evidence lacks a number: append **(unverified)**
- Do NOT fabricate claims not supported by evidence — state "not found in current sources"

## Output formatting
- Bold all key numeric values: **1.816 g/cm³**, **8750 m/s**
- Include units consistently (g/cm³, km/s, GPa, kJ/kg, °C, s, mm/s, MPa)
- For comparisons: use markdown tables — columns: Entity | Value | Unit | Source
- For design questions: use tables for trade-offs (方案 | 优点 | 风险 | 适用场景)
- Structure multi-part answers with ## headings
- Answer in the same language as the user's question
- If "## Conversation Context" is provided, use it to resolve pronouns and references

## Multiple values for the same property (check "_agg" field)
- "_agg.condition_explained = true": values differ by conditions
  → "X.XX–Y.YY [unit] depending on conditions" + table: Condition | Value | Source [N]
- "_agg.condition_explained = false" (true conflict):
  → "mean ± stdev [unit] across N sources" + table with all values + sources
  → note spread if > 5%: "(X.X% spread across sources)"
- spread_pct < 2%: single value with "(N sources agree)" footnote

Evidence quality: blockquote-cited > [N]-cited > uncited numeric > qualitative text
"""


def _score_confidence(
    evidence_items: list[dict],
    validation_errors: list[str],
) -> tuple[str, str]:
    """Compute (confidence_level, reason) from evidence quality signals.

    Levels:
      "high"   — ≥3 sourced items, no CONFLICT, low spread
      "medium" — 1-2 items OR minor CONFLICT / CONDITION_VARIATION
      "low"    — items exist but unverified OR high spread (>20%)
      "none"   — no items OR EMPTY_RESULTS error
    """
    if not evidence_items or any("EMPTY_RESULTS" in e for e in validation_errors):
        return "none", "No relevant evidence found in the knowledge base"

    n = len(evidence_items)
    has_conflict = any("CONFLICT:" in e for e in validation_errors)
    has_condition_var = any("CONDITION_VARIATION" in e for e in validation_errors)
    all_unverified = all(item.get("_unverified") for item in evidence_items)
    sourced = sum(1 for item in evidence_items if item.get("evidence_id") and not item.get("_unverified"))

    # Check max spread across numeric items with _agg metadata
    max_spread = max(
        (item["_agg"].get("spread_pct", 0) for item in evidence_items if item.get("_agg")),
        default=0,
    )

    if all_unverified or sourced == 0:
        return "low", "Evidence found but none linked to a citable source"

    if has_conflict and max_spread > 20:
        return "low", f"Conflicting values across sources (spread {max_spread:.0f}%) — treat with caution"

    if n >= 3 and sourced >= 2 and not has_conflict and max_spread < 5:
        return "high", f"{sourced} concordant sources; spread {max_spread:.1f}%"

    if n >= 1 and sourced >= 1 and (not has_conflict or has_condition_var):
        reason_parts = [f"{sourced}/{n} items sourced"]
        if has_condition_var:
            reason_parts.append("value variation explained by conditions")
        if max_spread > 5:
            reason_parts.append(f"spread {max_spread:.0f}%")
        return "medium", "; ".join(reason_parts)

    if has_conflict:
        return "medium", f"Conflicting values detected (spread {max_spread:.0f}%)"

    return "medium", f"{sourced} sourced item(s)"


def _build_answer_prompt(state: dict) -> tuple[str, str]:
    """Build the (context, user_content) strings for the answer builder LLM call.

    Evidence items are numbered [1], [2], … so the LLM can use inline citations.
    The last 2 conversation turns are included as "## Conversation Context" for
    coreference resolution (e.g. "that compound", "previous result").

    Returns:
        (context_str, full_user_content) — context_str is also exposed for streaming.
    """
    evidence_items = state.get("evidence_items") or []
    validation_errors = state.get("validation_errors") or []
    history = (state.get("history") or [])[-4:]  # last 2 Q&A pairs = 4 messages
    context_parts: list[str] = []

    # ── Conversation context (helps resolve "that", "previous", etc.) ──
    if history:
        context_parts.append("## Conversation Context (most recent turns)")
        for msg in history:
            role = msg.get("role", "")
            content = (msg.get("content") or "")[:400]  # cap to avoid bloat
            prefix = "User" if role == "user" else "Assistant"
            context_parts.append(f"**{prefix}:** {content}")

    # ── Evidence items (numbered for inline citation) ─────────────────
    if evidence_items:
        context_parts.append("## Evidence Items")
        numbered: list[str] = []
        for i, item in enumerate(evidence_items, 1):
            item_copy = dict(item)
            item_copy["_ref"] = f"[{i}]"
            numbered.append(json.dumps(item_copy, default=str, ensure_ascii=False))
        context_parts.append("\n".join(numbered))

    # ── Validation warnings ───────────────────────────────────────────
    if validation_errors:
        context_parts.append("## Validation Warnings")
        for err in validation_errors:
            if "CONFLICT" in err:
                context_parts.append(f"WARNING: {err}")
            elif "MISSING_UNIT" in err:
                context_parts.append(f"NOTE: {err}")
            else:
                context_parts.append(f"INFO: {err}")

    # ── Fallback: raw tool results ────────────────────────────────────
    if not any("Evidence" in p for p in context_parts):
        tool_results = state.get("tool_results") or []
        if tool_results:
            context_parts.append("## Raw Tool Results (no structured evidence extracted)")
            context_parts.append(json.dumps(tool_results, default=str, ensure_ascii=False)[:2000])

    # ── Confidence level (from evidence_judge) ────────────────────────
    confidence = state.get("confidence", "medium")
    confidence_reason = state.get("confidence_reason", "")
    confidence_block = (
        f"## Evidence Confidence\n"
        f"Level: **{confidence.upper()}**"
        + (f" — {confidence_reason}" if confidence_reason else "")
        + "\n\nLanguage guidelines based on confidence:\n"
        + {
            "high":   "  → State facts directly with citations. Values are well-supported.\n",
            "medium": "  → Use measured phrasing: 'available data indicates', 'reported as'. Note if only 1 source.\n",
            "low":    "  → Explicitly flag uncertainty: 'limited evidence suggests', 'unverified', add ⚠ markers.\n",
            "none":   "  → Clearly state data is NOT available in the knowledge base. Do NOT guess or extrapolate.\n",
        }.get(confidence, "")
    )
    context_parts.insert(0, confidence_block)

    context = "\n\n".join(context_parts)
    user_content = f"Question: {state['input']}\n\n{context}"
    return context, user_content


def _build_graph(db: Session, custom_answer_builder=None, enable_reflection: bool = True):
    """Build the LangGraph planning agent.

    custom_answer_builder: if provided, replaces the default answer_builder_node.
      Used by run_agent_stream to capture evidence context without calling LLM.
    enable_reflection: if False, the reflection node becomes a pass-through.
      Set to False in streaming mode (answer is streamed, not stored in state).
    """
    client = get_llm_client()
    model = settings.LLM_MODEL

    # ---- Node 1: Query Understanding (LLM) ----
    def query_understanding_node(state: AgentState) -> dict:
        messages = [{"role": "system", "content": _QUERY_UNDERSTANDING_SYSTEM}]
        # Include last 4 history messages
        history = (state.get("history") or [])[-4:]
        messages.extend(history)
        messages.append({"role": "user", "content": state["input"]})

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tool_choice="none",
            )
            raw = (response.choices[0].message.content or "{}").strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
            parsed = json.loads(raw)
            query_type = parsed.get("query_type", "general")
            if query_type not in ("entity_property", "comparison", "table", "formulation", "general"):
                query_type = "general"
            entities = parsed.get("entities", [])
            if not isinstance(entities, list):
                entities = []
            property_name = parsed.get("property_name", "") or ""
        except Exception:
            query_type = "general"
            entities = []
            property_name = ""

        return {
            "query_type": query_type,
            "entities": entities,
            "property_name": property_name,
        }

    # ---- Node 1b: Subproblem Decomposition (parallel execution) ----
    _DECOMPOSITION_SYSTEM = """\
You are a query complexity analyzer for energetic materials research.
Decide if this query should be decomposed into parallel sub-problems.

Decompose when:
  - Comparing 3+ entities on the same property (run entity cards in parallel)
  - Question has 2+ clearly distinct aspects that need separate tool calls
  - Multi-step reasoning: "find X, then compare with Y, then recommend Z"

Do NOT decompose when:
  - Simple single-entity lookup
  - General literature search
  - Already a very focused question

If decomposition is needed, generate sub-problems. Each sub-problem has its own
independent tool steps that can run in parallel without depending on others.

Return ONLY JSON:
{
  "decompose": true | false,
  "reason": "one line explanation",
  "sub_problems": [
    {
      "goal": "what this sub-problem answers",
      "steps": [{"tool": "<name>", "args": {<kwargs>}}]
    }
  ]
}
If decompose=false, sub_problems must be [].
Max 5 sub-problems. Each sub-problem max 3 steps.
"""

    def decomposition_node(state: AgentState) -> dict:
        """LLM decides if parallel subproblem execution is warranted."""
        if not settings.DECOMPOSITION_ENABLED:
            return {"decomposed": False}
        if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            return {"decomposed": False}

        # Fast-path heuristic: skip LLM call for obviously simple queries
        entities = state.get("entities") or []
        qtype = state.get("query_type", "general")
        # Simple single-entity or general queries rarely need decomposition
        if len(entities) <= 1 and qtype in ("entity_property", "general"):
            return {"decomposed": False}

        context = (
            f"Query: {state['input']}\n"
            f"Type: {qtype}\n"
            f"Entities: {entities}\n"
            f"Property: {state.get('property_name') or '(none)'}"
        )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _DECOMPOSITION_SYSTEM},
                    {"role": "user", "content": context},
                ],
                response_format={"type": "json_object"},
                timeout=15,
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
        except Exception as e:
            logger.debug(f"Decomposition LLM call failed: {e}")
            return {"decomposed": False}

        if not parsed.get("decompose", False):
            return {"decomposed": False}

        sub_problems = parsed.get("sub_problems", [])
        if not sub_problems:
            return {"decomposed": False}

        logger.info(
            f"[decomposition] Splitting into {len(sub_problems)} sub-problems: "
            + ", ".join(sp.get("goal", "?") for sp in sub_problems)
        )

        # Execute sub-problems in parallel — each gets its own DB session
        def _run_sub(sub: dict) -> list[dict]:
            from app.core.database import SessionLocal
            sub_db = SessionLocal()
            try:
                results = []
                for step in sub.get("steps", [])[:3]:
                    if step.get("tool") not in TOOL_CLASSES:
                        continue
                    result = _execute_tool(step["tool"], step.get("args", {}), sub_db)
                    results.append({
                        "tool": step["tool"],
                        "args": step.get("args", {}),
                        "result": result,
                        "_sub_goal": sub.get("goal", ""),
                    })
                return results
            except Exception as exc:
                logger.warning(f"Sub-problem execution error: {exc}")
                return []
            finally:
                sub_db.close()

        max_workers = min(len(sub_problems), settings.DECOMPOSITION_MAX_WORKERS)
        all_results: list[dict] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_run_sub, sp): sp for sp in sub_problems[:5]}
            for future in as_completed(futures):
                try:
                    all_results.extend(future.result())
                except Exception as exc:
                    logger.warning(f"Sub-problem future error: {exc}")

        if not all_results:
            return {"decomposed": False}

        logger.info(f"[decomposition] Collected {len(all_results)} tool results from {len(sub_problems)} sub-problems")
        return {"tool_results": all_results, "decomposed": True}

    TOOL_CLASSES = {
        "search_memory", "get_entity_card", "find_tables", "compare_values",
        "trace_evidence", "get_source_snippet", "find_related_entities", "get_formulation",
    }

    # ---- Node 2: LLM Planner (falls back to rules if LLM unavailable) ----
    _PLANNER_SYSTEM = """\
You are a planning agent for energetic materials / propellant research.
Given a classified query, generate a tool execution plan.

Available tools:
  search_memory(query, top_k=8)           — keyword+vector search across all literature
  get_entity_card(name)                   — full property card (density, VOD, etc.) for one entity
  find_tables(query, top_k=5)             — find tables by keyword
  compare_values(property_name, entity_names=[])  — compare property across entities (efficient, 1 DB call)
  trace_evidence(evidence_id)             — fetch original source quote for an evidence UUID
  get_source_snippet(chunk_id)            — fetch raw chunk text
  find_related_entities(entity_name, relation="", limit=10)  — graph-based similarity search
  get_formulation(entity_name, top_k=10)  — find formulations/compositions containing a given entity

Planning rules you MUST follow:
  - entity_property query  → call get_entity_card for EACH mentioned entity (Rule 6)
  - comparison query       → call compare_values FIRST, then get_entity_card only for ≤2 entities
  - table query            → MUST include find_tables (Rule 5)
  - formulation query      → call get_formulation for EACH mentioned entity
  - design/recommendation query (含"设计""方案""推荐""如何提高") →
      MUST use at least 3 steps:
      1. search_memory with the core technical topic
      2. find_tables to look for relevant performance data
      3. get_formulation or compare_values for the key material(s)
      Then the answer builder will synthesize findings into a structured expert recommendation.
  - general query          → use search_memory with specific technical terms
  - Max 5 steps total

Return ONLY JSON:
{"steps": [{"tool": "<name>", "args": {<kwargs>}, "goal": "<one-line purpose>"}]}
"""

    def planner_node(state: AgentState) -> dict:
        qtype = state["query_type"]
        entities = state["entities"]
        prop = state["property_name"]

        def _rule_based_fallback() -> list[dict]:
            """Original rule-based planner as fallback."""
            if qtype == "entity_property" and entities:
                seq = [{"tool": "get_entity_card", "args": {"name": e}, "goal": f"Get {e} properties"} for e in entities[:2]]
                if not prop:
                    seq += [{"tool": "search_memory", "args": {"query": state["input"], "top_k": 5}, "goal": "Broad search"}]
            elif qtype == "comparison" and entities:
                target_prop = prop or "detonation_velocity"
                seq = [{"tool": "compare_values", "args": {"property_name": target_prop, "entity_names": entities[:4]}, "goal": f"Compare {target_prop}"}]
                if len(entities) <= 2:
                    seq += [{"tool": "get_entity_card", "args": {"name": e}, "goal": f"Get {e} card"} for e in entities[:2]]
            elif qtype == "table":
                query = prop or state["input"]
                seq = [
                    {"tool": "find_tables", "args": {"query": query}, "goal": "Find relevant tables"},
                    {"tool": "search_memory", "args": {"query": state["input"], "top_k": 5}, "goal": "Supplementary search"},
                ]
            elif qtype == "formulation" and entities:
                seq = [{"tool": "get_formulation", "args": {"entity_name": e}, "goal": f"Find formulations containing {e}"} for e in entities[:3]]
                seq += [{"tool": "search_memory", "args": {"query": state["input"], "top_k": 5}, "goal": "Supplementary search"}]
            else:
                seq = [{"tool": "search_memory", "args": {"query": state["input"], "top_k": 8}, "goal": "General search"}]
            return seq

        # Try LLM planner first
        if settings.LLM_BASE_URL and settings.LLM_MODEL:
            context = (
                f"Query: {state['input']}\n"
                f"Type: {qtype}\n"
                f"Entities: {entities}\n"
                f"Property: {prop or '(none)'}"
            )
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": _PLANNER_SYSTEM},
                        {"role": "user", "content": context},
                    ],
                    response_format={"type": "json_object"},
                    timeout=15,
                )
                raw = response.choices[0].message.content or "{}"
                parsed = json.loads(raw)
                steps = parsed.get("steps", [])
                if steps and isinstance(steps, list):
                    # Validate: each step must have tool and args
                    valid_steps = [
                        s for s in steps
                        if isinstance(s, dict) and s.get("tool") in TOOL_CLASSES and isinstance(s.get("args"), dict)
                    ]
                    if valid_steps:
                        return {"tool_sequence": valid_steps[:5]}
            except Exception as e:
                logger.debug(f"LLM planner failed, using rules: {e}")

        return {"tool_sequence": _rule_based_fallback()}

    # ---- Node 3: Tool Executor (parallel, no LLM) ----
    def tool_executor_node(state: AgentState) -> dict:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ns = state.get("namespace", "default")

        def _run_step(step: dict) -> dict:
            args = dict(step["args"])
            if step["tool"] in ("search_memory", "find_tables") and "namespace" not in args:
                args["namespace"] = ns
            result = _execute_tool(step["tool"], args, db)
            return {"tool": step["tool"], "args": args, "result": result, "_order": step.get("_order", 0)}

        steps = [dict(s, _order=i) for i, s in enumerate(state["tool_sequence"])]
        if len(steps) <= 1:
            # No parallelism needed for single step
            return {"tool_results": [_run_step(s) for s in steps]}

        results_map: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=min(len(steps), 4), thread_name_prefix="tool") as pool:
            futures = {pool.submit(_run_step, s): s["_order"] for s in steps}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    results_map[idx] = fut.result()
                except Exception as e:
                    results_map[idx] = {"tool": steps[idx]["tool"], "args": steps[idx]["args"],
                                        "result": {"error": str(e)}, "_order": idx}

        results = [results_map[i] for i in sorted(results_map)]
        return {"tool_results": results}

    # ---- Node 3b: Multi-hop ReAct (LLM decides if more tool calls needed) ----
    _MULTIHOP_SYSTEM = """\
You are a ReAct reasoning agent for energetic materials research.
Examine the tool results so far and decide if additional tool calls would substantially improve the answer.

Rules:
  - If results already contain the requested data with evidence → return {"continue": false}
  - If results are sparse/missing and a specific follow-up search would help → return {"continue": true, "steps": [...]}
  - Max 3 additional steps per round
  - Prefer targeted searches (specific entity names, property names) over broad ones
  - If the last tool returned an evidence_id, consider calling trace_evidence to get the source quote

Return ONLY JSON: {"continue": false} OR {"continue": true, "steps": [{"tool": "...", "args": {...}, "goal": "..."}]}
"""

    def multihop_node(state: AgentState) -> dict:
        """LLM examines current results and optionally triggers additional tool calls."""
        if not settings.MULTIHOP_ENABLED:
            return {"multihop_count": state.get("multihop_count", 0)}
        if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            return {"multihop_count": state.get("multihop_count", 0)}

        hop_count = state.get("multihop_count", 0)
        if hop_count >= settings.MULTIHOP_MAX_ROUNDS:
            return {"multihop_count": hop_count}

        tool_results = state.get("tool_results") or []
        if not tool_results:
            return {"multihop_count": hop_count}

        context = (
            f"Original question: {state['input']}\n\n"
            f"Tool results so far:\n{json.dumps(tool_results, default=str, ensure_ascii=False)[:3000]}"
        )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _MULTIHOP_SYSTEM},
                    {"role": "user", "content": context},
                ],
                response_format={"type": "json_object"},
                timeout=15,
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)

            if not parsed.get("continue", False):
                return {"multihop_count": hop_count + 1}

            additional_steps = parsed.get("steps", [])
            valid_steps = [
                s for s in additional_steps
                if isinstance(s, dict) and s.get("tool") in TOOL_CLASSES and isinstance(s.get("args"), dict)
            ][:3]

            if not valid_steps:
                return {"multihop_count": hop_count + 1}

            # Execute additional tool calls and append results
            new_results = []
            for step in valid_steps:
                result = _execute_tool(step["tool"], step["args"], db)
                new_results.append({"tool": step["tool"], "args": step["args"], "result": result})
                logger.info(f"[multihop round {hop_count+1}] {step['tool']}({step['args']}) → {str(result)[:80]}")

            return {"tool_results": new_results, "multihop_count": hop_count + 1}

        except Exception as e:
            logger.debug(f"Multihop node error (non-fatal): {e}")
            return {"multihop_count": hop_count + 1}

    # ---- Node 4: Evidence Judge (rule-based + structured LLM extraction) ----
    def evidence_judge_node(state: AgentState) -> dict:
        tool_results = state.get("tool_results") or []
        qtype = state.get("query_type", "general")

        # For general / table queries: search_memory returns text chunks.
        # Pass them directly as evidence_items (no LLM extraction needed).
        if qtype in ("general", "table"):
            text_items = []
            raw_chunks_found = False
            for tr in tool_results:
                if tr.get("tool") in ("search_memory", "find_tables") and tr.get("result"):
                    chunks = tr["result"].get("results") or []
                    if chunks:
                        raw_chunks_found = True
                    for chunk in chunks:
                        text_items.append({
                            "entity": "",
                            "property": "text",
                            "value": chunk.get("text", ""),
                            "unit": None,
                            "evidence_id": chunk.get("chunk_id"),
                            "source": chunk.get("section_path", ""),
                        })
            if text_items:
                conf, conf_r = _score_confidence(text_items, [])
                return {"evidence_items": text_items, "validation_errors": [],
                        "confidence": conf, "confidence_reason": conf_r}
            if not raw_chunks_found:
                return {
                    "evidence_items": [],
                    "validation_errors": ["EMPTY_RESULTS: no chunks found, must retry with different query"],
                    "confidence": "none", "confidence_reason": "No chunks retrieved",
                }
            # chunks were returned but all empty text
            return {"evidence_items": [], "validation_errors": ["EMPTY_RESULTS: no usable text found"],
                    "confidence": "none", "confidence_reason": "All retrieved chunks were empty"}

        # For entity_property / comparison: extract structured values via LLM
        # Step 1: Extract evidence items using structured JSON output (no parsing failures)
        results_text = json.dumps(tool_results, default=str, ensure_ascii=False)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _EVIDENCE_EXTRACTION_SYSTEM},
                    {"role": "user", "content": results_text},
                ],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            # Model may return {"items": [...]} or just [...] wrapped in an object
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict):
                # Find the first list value
                items = next((v for v in parsed.values() if isinstance(v, list)), [])
            else:
                items = []
        except Exception:
            items = []

        # Step 2: Apply hard validation rules
        errors = []
        valid = []

        # Rule 3: empty results → flag for retry
        if not items:
            errors.append("EMPTY_RESULTS: no data found, must retry with different query")
            return {"evidence_items": valid, "validation_errors": errors}

        property_groups: dict[tuple, list] = {}

        for item in items:
            # Rule 2: no unit → reject property value
            if item.get("property") and not item.get("unit"):
                errors.append(
                    f"MISSING_UNIT: {item.get('entity', '?')}.{item.get('property', '?')} "
                    f"= {item.get('value', '?')} has no unit"
                )
                continue

            # Rule 1: no evidence_id → cannot output definitive number
            if not item.get("evidence_id"):
                item["_unverified"] = True  # mark but don't reject

            key = (item.get("entity", ""), item.get("property", ""))
            property_groups.setdefault(key, []).append(item)
            valid.append(item)

        # Rule 4: conflicting values → smart aggregation analysis
        for (entity, prop), group in property_groups.items():
            if len(group) < 2:
                continue
            numeric_vals = [
                g.get("value_numeric") or (float(g["value"]) if isinstance(g.get("value"), (int, float)) else None)
                for g in group
            ]
            numeric_vals = [v for v in numeric_vals if v is not None]
            str_values = set(str(g.get("value", "")) for g in group)

            if len(str_values) <= 1:
                continue  # all same, no conflict

            # Check if conditions differ (condition-explained variation)
            conditions = [g.get("condition") for g in group]
            has_condition_variation = (
                any(c for c in conditions) and
                len({str(c) for c in conditions}) > 1
            )

            if numeric_vals and len(numeric_vals) >= 2:
                import statistics as _stats
                mean_val = _stats.mean(numeric_vals)
                stdev_val = _stats.stdev(numeric_vals) if len(numeric_vals) >= 2 else 0.0
                spread_pct = (stdev_val / mean_val * 100) if mean_val != 0 else 0
                sources = [g.get("source", "unknown") for g in group]

                if has_condition_variation:
                    # Different measurement conditions — not a true conflict
                    errors.append(
                        f"CONDITION_VARIATION: {entity}.{prop} values differ by conditions "
                        f"(mean={mean_val:.3g}, stdev={stdev_val:.3g}, spread={spread_pct:.1f}%) "
                        f"— likely different pressures/temperatures, not measurement error"
                    )
                    # Tag each item with aggregation stats
                    for item in group:
                        item["_agg"] = {
                            "n": len(numeric_vals),
                            "mean": round(mean_val, 4),
                            "stdev": round(stdev_val, 4),
                            "spread_pct": round(spread_pct, 2),
                            "condition_explained": True,
                        }
                else:
                    # True measurement conflict
                    errors.append(
                        f"CONFLICT: {entity}.{prop} has {len(group)} conflicting values "
                        f"mean={mean_val:.3g} ± {stdev_val:.3g} ({spread_pct:.1f}% spread) "
                        f"from {len(set(sources))} source(s) — list all with citations"
                    )
                    for item in group:
                        item["_agg"] = {
                            "n": len(numeric_vals),
                            "mean": round(mean_val, 4),
                            "stdev": round(stdev_val, 4),
                            "spread_pct": round(spread_pct, 2),
                            "condition_explained": False,
                        }
            else:
                # Non-numeric conflict (e.g. text descriptions differ)
                sources = [g.get("source", "unknown") for g in group]
                errors.append(
                    f"CONFLICT: {entity}.{prop} has conflicting non-numeric values "
                    f"{list(str_values)[:3]} from {sources}"
                )

        # ── Confidence scoring ────────────────────────────────────────
        confidence, confidence_reason = _score_confidence(valid, errors)
        return {
            "evidence_items": valid,
            "validation_errors": errors,
            "confidence": confidence,
            "confidence_reason": confidence_reason,
        }

    # ---- Node 5a: Replanner (rule-based + 1 LLM call for query reformulation) ----
    def replanner_node(state: AgentState) -> dict:
        original_query = state["input"]
        retry_count = state.get("retry_count") or 0

        _REPLAN_SYSTEM = (
            f'The query "{original_query}" returned no results.\n'
            "Suggest ONE alternative search query for the same topic in a different phrasing.\n"
            "Return ONLY the query string, nothing else."
        )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": _REPLAN_SYSTEM},
                ],
                tool_choice="none",
            )
            new_query = (response.choices[0].message.content or original_query).strip()
        except Exception:
            new_query = original_query

        # Build a new tool_sequence with the alternative query
        new_tool_sequence = [
            {"tool": "search_memory", "args": {"query": new_query, "top_k": 8}}
        ]

        return {
            "tool_sequence": new_tool_sequence,
            "retry_count": retry_count + 1,
        }

    # ---- Node 5b: Answer Builder (LLM) ----
    if custom_answer_builder is not None:
        answer_builder_node = custom_answer_builder
    else:
        def answer_builder_node(state: AgentState) -> dict:
            context, user_content = _build_answer_prompt(state)
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": _ANSWER_BUILDER_SYSTEM},
                        {"role": "user", "content": user_content},
                    ],
                    tool_choice="none",
                )
                answer = response.choices[0].message.content or "No answer generated."
            except Exception as exc:
                answer = f"Error generating answer: {exc}"

            # Retrieval-hit boosting: increment hit counter for cited chunks
            # Parse [N] inline citations from answer to identify which items were used
            try:
                from app.services.retrieval import RetrievalService
                import re as _re
                evidence_items = state.get("evidence_items") or []
                # Parse [N] markers from the answer text
                cited_nums = {int(m) for m in _re.findall(r'\[(\d+)\]', answer)}
                cited_ids: list[str] = []
                for num in cited_nums:
                    idx = num - 1  # [1] → index 0
                    if 0 <= idx < len(evidence_items):
                        ev = evidence_items[idx]
                        eid = ev.get("evidence_id", "")
                        if eid and not eid.startswith("ev-"):
                            cited_ids.append(eid)
                # Fallback: if no [N] citations found, use all evidence ids (old behavior)
                if not cited_ids:
                    cited_ids = [
                        e["evidence_id"] for e in evidence_items
                        if e.get("evidence_id") and not e["evidence_id"].startswith("ev-")
                    ]
                if cited_ids:
                    RetrievalService(db).record_chunk_hits(cited_ids)
            except Exception:
                pass  # non-fatal

            return {"response": answer}

    # ---- Node 6: Reflection (self-verify the generated answer) ----
    _REFLECTION_SYSTEM = """\
You are a scientific fact-checker for energetic materials research.
Review the generated answer against the evidence provided.

Check for these issues:
  1. HALLUCINATED_EVIDENCE: Answer cites an evidence_id not present in the evidence list
  2. UNIT_INCONSISTENCY: Same property compared with different units without conversion
     (e.g. comparing g/cm³ with kg/m³ without noting the conversion)
  3. UNSUPPORTED_CLAIM: Answer states a fact not found in any evidence item
  4. MISSING_CAVEAT: Answer gives a definitive value for data marked as unverified
  5. CONTRADICTION: Answer contradicts itself (states two incompatible values)

If issues found: return the corrected answer in "corrected".
If answer is accurate: set "corrected" to null.

Return ONLY JSON:
{
  "issues": ["ISSUE_TYPE: description", ...],
  "corrected": "full corrected answer text, or null if no changes needed"
}
"""

    def reflection_node(state: AgentState) -> dict:
        """Self-verify the generated answer. Correct issues if found."""
        # Only run once, only in non-streaming mode, only if we have a real answer
        if not enable_reflection:
            return {"reflection_done": True}
        if state.get("reflection_done"):
            return {}
        if not settings.REFLECTION_ENABLED:
            return {"reflection_done": True}
        if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            return {"reflection_done": True}

        answer = state.get("response") or ""
        evidence_items = state.get("evidence_items") or []

        # Nothing to verify for trivial cases
        if not answer or answer.startswith("Error") or not evidence_items:
            return {"reflection_done": True}

        # For text/general evidence, skip reflection (no numeric claims to verify)
        non_text = [e for e in evidence_items if e.get("property") != "text"]
        if not non_text:
            return {"reflection_done": True}

        context = (
            f"Evidence Items (ground truth):\n"
            f"{json.dumps(non_text[:20], default=str, ensure_ascii=False)}\n\n"
            f"Generated Answer:\n{answer[:3000]}"
        )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _REFLECTION_SYSTEM},
                    {"role": "user", "content": context},
                ],
                response_format={"type": "json_object"},
                timeout=20,
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
        except Exception as e:
            logger.debug(f"Reflection node error (non-fatal): {e}")
            return {"reflection_done": True}

        issues = parsed.get("issues") or []
        corrected = parsed.get("corrected")

        if issues:
            logger.info(f"[reflection] Found {len(issues)} issue(s): {issues}")

        if corrected and corrected.strip() and corrected != answer:
            logger.info("[reflection] Answer corrected")
            return {"response": corrected, "reflection_done": True}

        return {"reflection_done": True}

    # ---- Node 5c: Conflict Repair (trace_evidence for conflicting items) ----
    def conflict_repair_node(state: AgentState) -> dict:
        """When CONFLICT detected, automatically call trace_evidence for conflicting items.

        This provides measurement conditions (pressure, temperature, sample source)
        that explain WHY values differ, enabling the Answer Builder to give a
        condition-qualified answer instead of just listing conflicting numbers.
        """
        evidence_items = state.get("evidence_items") or []
        validation_errors = state.get("validation_errors") or []

        # Find evidence_ids from conflicting items (those with _agg metadata)
        conflict_ids: list[str] = []
        for item in evidence_items:
            if item.get("_agg") and item.get("evidence_id"):
                eid = item["evidence_id"]
                if eid and not eid.startswith("ev-") and eid not in conflict_ids:
                    conflict_ids.append(eid)
                if len(conflict_ids) >= 4:  # cap at 4 trace calls
                    break

        if not conflict_ids:
            return {}  # nothing to repair, proceed to answer_builder

        # Call trace_evidence for each conflicting item to get source context + conditions
        repair_results = []
        for eid in conflict_ids:
            result = _execute_tool("trace_evidence", {"evidence_id": eid}, db)
            repair_results.append({
                "tool": "trace_evidence",
                "args": {"evidence_id": eid},
                "result": result,
            })
            logger.info(f"[conflict_repair] trace_evidence({eid[:8]}...) → {str(result)[:80]}")

        # Append repair results to tool_results so evidence_judge / answer_builder can see them
        return {"tool_results": repair_results}

    # ---- Conditional edges ----
    def route_after_judge(state: AgentState) -> str:
        errors = state.get("validation_errors") or []
        retry = state.get("retry_count") or 0
        has_empty = any("EMPTY_RESULTS" in e for e in errors)
        has_conflict = any("CONFLICT:" in e for e in errors)

        if has_empty and retry < 2:
            return "replanner"
        # On first conflict, try repair; skip on retry to avoid loop
        if has_conflict and retry == 0:
            return "conflict_repair"
        return "answer_builder"

    def route_after_replanner(state: AgentState) -> str:
        return "tool_executor"  # always go back to executor after replanning

    # ---- Routing: after decomposition, skip planner+executor if already ran ----
    def route_after_decomposition(state: AgentState) -> str:
        if state.get("decomposed"):
            return "multihop"   # tools already executed in parallel
        return "planner"        # normal sequential path

    # ---- Build the graph ----
    graph = StateGraph(AgentState)
    graph.add_node("query_understanding", query_understanding_node)
    graph.add_node("decomposition", decomposition_node)
    graph.add_node("planner", planner_node)
    graph.add_node("tool_executor", tool_executor_node)
    graph.add_node("multihop", multihop_node)
    graph.add_node("evidence_judge", evidence_judge_node)
    graph.add_node("replanner", replanner_node)
    graph.add_node("conflict_repair", conflict_repair_node)
    graph.add_node("answer_builder", answer_builder_node)
    graph.add_node("reflection", reflection_node)

    graph.set_entry_point("query_understanding")
    graph.add_edge("query_understanding", "decomposition")
    graph.add_conditional_edges("decomposition", route_after_decomposition, {
        "planner": "planner",
        "multihop": "multihop",
    })
    graph.add_edge("planner", "tool_executor")
    graph.add_edge("tool_executor", "multihop")
    graph.add_edge("multihop", "evidence_judge")
    graph.add_conditional_edges("evidence_judge", route_after_judge, {
        "replanner": "replanner",
        "conflict_repair": "conflict_repair",
        "answer_builder": "answer_builder",
    })
    graph.add_edge("replanner", "tool_executor")
    graph.add_edge("conflict_repair", "answer_builder")
    graph.add_edge("answer_builder", "reflection")
    graph.add_edge("reflection", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Proactive knowledge suggestion helper
# ---------------------------------------------------------------------------

def _build_proactive_suggestions(answer: str, entities: list[str], db: Session) -> str:
    """Find KG neighbors of entities mentioned in the answer and append suggestions.

    Returns a markdown string like:
        ---
        **Related compounds in knowledge base:** HMX (co-occurs 23×), TATB, ...

    Returns empty string if nothing found or on any error.
    """
    try:
        from app.pipeline.graph_builder import _entities_in_text
        from app.models.orm.graph import MemoryGraphNode, MemoryGraphEdge

        # Collect entities mentioned in the answer + declared entities
        all_entities = set(entities) | _entities_in_text(answer)
        if not all_entities:
            return ""

        # Get graph nodes for those entities
        nodes = db.query(MemoryGraphNode).filter(MemoryGraphNode.label.in_(all_entities)).all()
        if not nodes:
            return ""

        source_ids = [n.id for n in nodes]
        label_by_id = {n.id: n.label for n in nodes}

        # Find strong CO_OCCURS_WITH neighbors (ignore COMPARED_BY edges for suggestions)
        edges = (
            db.query(MemoryGraphEdge)
            .filter(
                MemoryGraphEdge.source_id.in_(source_ids),
                MemoryGraphEdge.edge_type == "CO_OCCURS_WITH",
                MemoryGraphEdge.weight >= 0.2,
            )
            .order_by(MemoryGraphEdge.weight.desc())
            .limit(20)
            .all()
        )

        # Collect unique neighbor labels not already in the answer entities
        seen = set(all_entities)
        suggestions: list[tuple[str, float]] = []  # (label, weight)
        for edge in edges:
            label = label_by_id.get(edge.target_id)
            if label is None:
                node = db.get(MemoryGraphNode, edge.target_id)
                if node:
                    label = node.label
                    label_by_id[edge.target_id] = label
            if label and label not in seen:
                suggestions.append((label, edge.weight))
                seen.add(label)
            if len(suggestions) >= 5:
                break

        if not suggestions:
            return ""

        parts = []
        for label, weight in suggestions:
            co_count = int(round(weight * 50))  # reverse-normalize (weight = count/50)
            if co_count > 0:
                parts.append(f"**{label}** (co-occurs {co_count}×)")
            else:
                parts.append(f"**{label}**")

        return "\n\n---\n**相关化合物（知识库关联）：** " + "、".join(parts)

    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def run_agent(question: str, db: Session, history: list[dict] | None = None,
              namespace: str = "default") -> AgentResponse:
    """Run the structured rule-based planning agent and return a structured AgentResponse."""
    from app.services.retrieval import prefetch_for_query
    prefetch_for_query(question)
    compiled = _build_graph(db)

    try:
        result = compiled.invoke(
            {
                "input": question,
                "history": history or [],
                "namespace": namespace,
                "query_type": "general",
                "entities": [],
                "property_name": "",
                "tool_sequence": [],
                "tool_results": [],
                "multihop_count": 0,
                "decomposed": False,
                "evidence_items": [],
                "validation_errors": [],
                "retry_count": 0,
                "reflection_done": False,
                "confidence": "medium",
                "confidence_reason": "",
                "response": None,
            },
            {"recursion_limit": 20},
        )
    except GraphRecursionError:
        result = {"tool_results": [], "response": None, "evidence_items": [], "validation_errors": []}

    # Build plan steps from tool_results
    plan_steps = [
        PlanStep(step_id=i + 1, tool=r["tool"], goal=r["tool"], args=r["args"], result=r["result"])
        for i, r in enumerate(result.get("tool_results") or [])
    ]

    answer = result.get("response") or "No answer generated."

    # Fallback synthesis if no response
    if not result.get("response"):
        items = result.get("evidence_items") or []
        if items:
            answer = "**Evidence found (unvalidated answer):**\n" + "\n".join(
                f"- {it.get('entity', '?')}.{it.get('property', '?')} "
                f"= {it.get('value', '?')} {it.get('unit', '')}"
                for it in items
            )

    # Proactive knowledge suggestions: append KG-related compounds
    entities_found = result.get("entities") or []
    suggestion_suffix = _build_proactive_suggestions(answer, entities_found, db)
    if suggestion_suffix:
        answer = answer + suggestion_suffix

    return AgentResponse(
        answer=answer,
        plan=plan_steps,
        iterations=result.get("retry_count", 0) + 1,
        tool_calls_made=[{"tool": s.tool, "args": s.args, "result": s.result} for s in plan_steps],
        confidence=result.get("confidence", "medium"),
        confidence_reason=result.get("confidence_reason", ""),
    )


def run_agent_stream(question: str, db: Session, history: list[dict] | None = None,
                     namespace: str = "default"):
    """Three-phase streaming agent.

    Phase 1a (sync): query_understanding + decomposition + planner.
                     Emits plan SSE immediately so the user sees tool steps early.
    Phase 1b (sync): tool_executor + multihop + evidence_judge.
                     Runs remaining graph nodes to completion.
    Phase 2 (generator): stream the final answer token-by-token via SSE.

    Yields SSE-formatted strings:
      data: {"type": "plan", "plan": [...], "tool_names": [...]}   ← after ~6-9s (planning only)
      data: {"type": "token", "content": "..."}
      data: {"type": "done"}
    """
    import json as _json
    from app.services.retrieval import prefetch_for_query
    prefetch_for_query(question)  # kick off expansion + HyDE in background immediately

    client = get_llm_client()
    model = settings.LLM_MODEL
    collected: dict = {}

    # ── Build graph with capturing answer_builder ──────────
    def _capture_node(state: AgentState) -> dict:
        _, user_content = _build_answer_prompt(state)
        collected["user_content"] = user_content
        return {"response": "__streaming__"}

    compiled = _build_graph(db, custom_answer_builder=_capture_node, enable_reflection=False)
    initial = {
        "input": question,
        "history": history or [],
        "namespace": namespace,
        "query_type": "general",
        "entities": [],
        "property_name": "",
        "tool_sequence": [],
        "tool_results": [],
        "multihop_count": 0,
        "decomposed": False,
        "evidence_items": [],
        "validation_errors": [],
        "retry_count": 0,
        "reflection_done": False,
        "confidence": "medium",
        "confidence_reason": "",
        "response": None,
    }

    import time as _time

    # ── Immediately signal activity so the frontend updates right away ────────
    _t_start = _time.monotonic()
    yield f"data: {_json.dumps({'type': 'start'}, ensure_ascii=False)}\n\n"

    # ── Phase 1: run full graph (invoke releases all connections before Phase 2) ─
    # Use stream() to accumulate partial state so we can recover on recursion errors
    _partial: dict = dict(initial)
    try:
        for _chunk in compiled.stream(initial, {"recursion_limit": 20}, stream_mode="updates"):
            for _node, _out in _chunk.items():
                if isinstance(_out, dict):
                    _partial.update(_out)
                # Emit per-node progress events so the frontend stays responsive
                if _node == "tool_executor":
                    yield f"data: {_json.dumps({'type': 'progress', 'phase': 'retrieving', 'message': '正在检索文献数据…'}, ensure_ascii=False)}\n\n"
                elif _node == "decomposition" and isinstance(_out, dict) and _out.get("decomposed"):
                    yield f"data: {_json.dumps({'type': 'progress', 'phase': 'retrieving', 'message': '正在并行检索文献…'}, ensure_ascii=False)}\n\n"
                elif _node == "multihop":
                    yield f"data: {_json.dumps({'type': 'progress', 'phase': 'reasoning', 'message': '正在深度推理…'}, ensure_ascii=False)}\n\n"
                elif _node == "evidence_judge":
                    yield f"data: {_json.dumps({'type': 'progress', 'phase': 'judging', 'message': '正在整理证据…'}, ensure_ascii=False)}\n\n"
        result = _partial
    except GraphRecursionError:
        logger.warning("[run_agent_stream] GraphRecursionError — using partial state")
        result = _partial
        result.setdefault("confidence", "low")
        result.setdefault("confidence_reason", "检索循环次数超限，以下为部分结果")
        # If answer_builder never ran, build fallback from accumulated tool results
        if not collected.get("user_content") and _partial.get("tool_results"):
            try:
                _, _uc = _build_answer_prompt(_partial)
                if _uc:
                    collected["user_content"] = _uc
            except Exception:
                pass

    _t_phase1 = _time.monotonic()
    _phase1_secs = int(_t_phase1 - _t_start)
    logger.info(f"[Phase1] completed in {_phase1_secs}s")

    tool_results = result.get("tool_results") or []
    plan_steps = [{"step_id": i+1, "tool": r["tool"], "args": r["args"]}
                  for i, r in enumerate(tool_results)]
    tool_summary = []
    for i, r in enumerate(tool_results):
        td = r.get("result") or {}
        cnt = (len(td.get("results") or []) or len(td.get("comparisons") or [])
               or len(td.get("formulations") or []) or (1 if td else 0))
        tool_summary.append({"step": i+1, "tool": r["tool"], "result_count": cnt})
    confidence = result.get("confidence", "medium")
    confidence_reason = result.get("confidence_reason", "")
    yield f"data: {_json.dumps({'type': 'plan', 'plan': plan_steps, 'tool_names': [s['tool'] for s in plan_steps], 'tool_summary': tool_summary, 'confidence': confidence, 'confidence_reason': confidence_reason, 'phase1_secs': _phase1_secs}, ensure_ascii=False)}\n\n"

    # ── Phase 2: stream the answer ────────────────────────────────
    user_content = collected.get("user_content")
    if not user_content:
        yield f"data: {_json.dumps({'type': 'token', 'content': '抱歉，未能检索到相关信息。'}, ensure_ascii=False)}\n\n"
        yield f"data: {_json.dumps({'type': 'done', 'confidence': confidence, 'confidence_reason': confidence_reason, 'elapsed_secs': int(_time.monotonic() - _t_start)}, ensure_ascii=False)}\n\n"
        return

    # Notify frontend that answer generation is starting
    yield f"data: {_json.dumps({'type': 'progress', 'phase': 'generating', 'message': '正在生成回答…'}, ensure_ascii=False)}\n\n"

    # Phase 2 uses a dedicated client with no timeout — long answers can take minutes
    from openai import OpenAI as _OpenAI
    stream_client = _OpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL.rstrip("/") + "/v1",
        max_retries=2,    # retry on transient connection errors
        timeout=None,
    )
    logger.info(f"[Phase2] user_content={len(user_content)} chars, phase1={_phase1_secs}s, starting stream")
    _messages_payload = [
        {"role": "system", "content": _ANSWER_BUILDER_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    full_tokens: list[str] = []

    # ── Attempt 1: streaming ──────────────────────────────────────────────────
    _stream_ok = False
    try:
        stream = stream_client.chat.completions.create(
            model=model,
            messages=_messages_payload,
            stream=True,
        )
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if delta:
                full_tokens.append(delta)
                yield f"data: {_json.dumps({'type': 'token', 'content': delta}, ensure_ascii=False)}\n\n"
        _stream_ok = True
        logger.info(f"[Phase2] stream complete, {len(full_tokens)} chunks")
    except Exception as exc:
        logger.warning(f"[Phase2] stream error (tokens so far: {len(full_tokens)}): {exc!r}")

    # ── Attempt 2: retry streaming once after short delay ────────────────────
    if not _stream_ok and not full_tokens:
        logger.info("[Phase2] retrying stream after 3s delay")
        _time.sleep(3)
        try:
            stream2 = stream_client.chat.completions.create(
                model=model,
                messages=_messages_payload,
                stream=True,
            )
            for chunk in stream2:
                delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if delta:
                    full_tokens.append(delta)
                    yield f"data: {_json.dumps({'type': 'token', 'content': delta}, ensure_ascii=False)}\n\n"
            _stream_ok = True
            logger.info(f"[Phase2] stream retry complete, {len(full_tokens)} chunks")
        except Exception as exc2:
            logger.warning(f"[Phase2] stream retry error: {exc2!r}")

    # ── Attempt 3: non-streaming fallback (only if streaming got 0 tokens) ───
    if not _stream_ok and not full_tokens:
        logger.info("[Phase2] falling back to non-streaming request")
        _time.sleep(2)  # brief pause before re-using the API
        try:
            fallback_client = _OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL.rstrip("/") + "/v1",
                max_retries=3,
                timeout=360.0,   # 6 min hard limit
            )
            resp = fallback_client.chat.completions.create(
                model=model,
                messages=_messages_payload,
                stream=False,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content:
                logger.info(f"[Phase2] fallback got {len(content)} chars, simulating stream")
                chunk_size = 80
                for i in range(0, len(content), chunk_size):
                    piece = content[i:i + chunk_size]
                    full_tokens.append(piece)
                    yield f"data: {_json.dumps({'type': 'token', 'content': piece}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {_json.dumps({'type': 'token', 'content': '抱歉，生成回答失败，请重试。'}, ensure_ascii=False)}\n\n"
        except Exception as exc3:
            logger.error(f"[Phase2] all attempts failed: {exc3!r}")
            yield f"data: {_json.dumps({'type': 'token', 'content': '抱歉，答案生成失败，请稍后重试。'}, ensure_ascii=False)}\n\n"

    _t_end = _time.monotonic()
    _elapsed = int(_t_end - _t_start)
    logger.info(f"[run_agent_stream] total={_elapsed}s (phase1={_phase1_secs}s, phase2={_elapsed - _phase1_secs}s)")

    # Record chunk hits for cited evidence (non-fatal)
    try:
        import re as _re2
        full_answer = "".join(full_tokens)
        evidence_items = result.get("evidence_items") or []
        cited_nums = {int(m) for m in _re2.findall(r'\[(\d+)\]', full_answer)}
        cited_ids: list[str] = []
        for num in cited_nums:
            idx = num - 1
            if 0 <= idx < len(evidence_items):
                eid = evidence_items[idx].get("evidence_id", "")
                if eid and not eid.startswith("ev-"):
                    cited_ids.append(eid)
        if not cited_ids:
            cited_ids = [
                e["evidence_id"] for e in evidence_items
                if e.get("evidence_id") and not e["evidence_id"].startswith("ev-")
            ]
        if cited_ids:
            from app.services.retrieval import RetrievalService
            RetrievalService(db).record_chunk_hits(cited_ids)
    except Exception:
        pass

    yield f"data: {_json.dumps({'type': 'done', 'confidence': confidence, 'confidence_reason': confidence_reason, 'elapsed_secs': _elapsed, 'phase1_secs': _phase1_secs}, ensure_ascii=False)}\n\n"
