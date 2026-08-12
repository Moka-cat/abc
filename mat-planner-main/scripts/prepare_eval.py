#!/usr/bin/env python
"""
Evaluation preparation script for mat-planner.

Steps:
  1. Select representative papers from the MinerU JSON dataset.
  2. Ingest selected papers into the database.
  3. Write eval/eval_questions.json — the question set for agent evaluation.

Usage:
  uv run python scripts/prepare_eval.py \
      --data-dir /home/qisirui/Projects/OSS/分类任务/打包文件/去重最终 \
      [--limit 8]   # papers per category
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app.models.orm  # noqa: F401
from app.core.database import SessionLocal, create_all_tables
from app.services.ingestion import IngestionService


# ---------------------------------------------------------------------------
# Paper selection logic
# ---------------------------------------------------------------------------

TARGET_ENTITIES = {"RDX", "HMX", "TNT", "PETN", "CL-20", "FOX-7", "TATB", "NTO", "TKX-50"}
PROP_ENTITIES   = {"RDX", "HMX", "AP", "HTPB", "AN", "Al", "CL-20"}


def _score_energetic(data: dict) -> tuple[float, list[str], str]:
    content = data.get("content_list") or []
    title = ""
    for b in content:
        if b.get("text_level") == 1 and b.get("type") == "text" and len(b.get("text", "")) > 10:
            title = b["text"].strip()
            break
    text = " ".join(b.get("text", "") for b in content)
    entities = [e for e in TARGET_ENTITIES if e in text]
    has_dv = "detonation velocity" in text.lower() or "detonation speed" in text.lower()
    has_density = "density" in text.lower() or " ρ " in text
    num_tables = sum(1 for b in content if b.get("type") == "table")
    score = len(entities) * 2 + (3 if has_dv else 0) + (2 if has_density else 0) + min(num_tables, 4)
    return score, entities, title


def _score_propellant(data: dict) -> tuple[float, list[str], str]:
    content = data.get("content_list") or []
    title = ""
    for b in content:
        if b.get("text_level") == 1 and b.get("type") == "text" and len(b.get("text", "")) > 10:
            title = b["text"].strip()
            break
    text = " ".join(b.get("text", "") for b in content)
    entities = [e for e in PROP_ENTITIES if e in text]
    has_br = "burning rate" in text.lower() or "burn rate" in text.lower()
    num_tables = sum(1 for b in content if b.get("type") == "table")
    score = len(entities) * 2 + (3 if has_br else 0) + min(num_tables, 4)
    return score, entities, title


def select_papers(data_dir: Path, limit: int) -> list[Path]:
    """Select `limit` best papers from each category."""
    selected: list[Path] = []

    # Energetic materials
    em_dir = data_dir / "高能物质" / "有关数据筛选"
    if em_dir.exists():
        candidates = []
        for fp in list(em_dir.iterdir())[:800]:
            if fp.suffix != ".json":
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                score, entities, title = _score_energetic(data)
                if score >= 10 and len(entities) >= 2:
                    candidates.append((score, fp, title))
            except Exception:
                pass
        candidates.sort(reverse=True)
        selected.extend(fp for _, fp, _ in candidates[:limit])
        print(f"[energetic] Selected {min(limit, len(candidates))} / {len(candidates)} scored papers")
        for score, fp, title in candidates[:limit]:
            print(f"  score={score:4.0f}  {title[:70]}")

    # Propellants
    prop_dir = data_dir / "推进剂" / "有关数据筛选"
    if prop_dir.exists():
        candidates = []
        for fp in list(prop_dir.iterdir())[:600]:
            if fp.suffix != ".json":
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                score, entities, title = _score_propellant(data)
                if score >= 8 and entities:
                    candidates.append((score, fp, title))
            except Exception:
                pass
        candidates.sort(reverse=True)
        selected.extend(fp for _, fp, _ in candidates[:limit])
        print(f"\n[propellant] Selected {min(limit, len(candidates))} / {len(candidates)} scored papers")
        for score, fp, title in candidates[:limit]:
            print(f"  score={score:4.0f}  {title[:70]}")

    return selected


# ---------------------------------------------------------------------------
# Ingest selected papers
# ---------------------------------------------------------------------------

def ingest_papers(paths: list[Path]) -> list[str]:
    """Ingest papers into DB; return list of doc IDs."""
    create_all_tables()
    doc_ids = []
    with SessionLocal() as db:
        svc = IngestionService(db)
        for i, fp in enumerate(paths, 1):
            print(f"\nIngesting [{i}/{len(paths)}]: {fp.name[:40]}...")
            try:
                doc = svc.ingest_local_file(str(fp), namespace="eval")
                print(f"  → doc_id={doc.id}  chunks={len(doc.chunks)}  "
                      f"sections={len(doc.sections)}")
                doc_ids.append(doc.id)
            except Exception as e:
                print(f"  ✗ FAILED: {e}")
    return doc_ids


# ---------------------------------------------------------------------------
# Question set
# ---------------------------------------------------------------------------

EVAL_QUESTIONS = [
    # ── entity_property ──────────────────────────────────────────────────
    {
        "id": "EP-01",
        "category": "entity_property",
        "query_type": "entity_property",
        "question": "What is the density of RDX?",
        "expected_entities": ["RDX"],
        "expected_property": "density",
        "rules_exercised": ["Rule 6 (get_entity_card first)", "Rule 1 (evidence_id)", "Rule 2 (unit)"],
        "notes": "Baseline entity-property lookup; multiple sources may conflict (Rule 4).",
    },
    {
        "id": "EP-02",
        "category": "entity_property",
        "query_type": "entity_property",
        "question": "What is the detonation velocity of HMX?",
        "expected_entities": ["HMX"],
        "expected_property": "detonation_velocity",
        "rules_exercised": ["Rule 6", "Rule 2 (unit: m/s)"],
        "notes": "Common property with a clear unit requirement.",
    },
    {
        "id": "EP-03",
        "category": "entity_property",
        "query_type": "entity_property",
        "question": "What is the melting point of TNT?",
        "expected_entities": ["TNT"],
        "expected_property": "melting_point",
        "rules_exercised": ["Rule 6", "Rule 2 (unit: °C)"],
        "notes": "Well-known value; good for ground-truth verification.",
    },
    {
        "id": "EP-04",
        "category": "entity_property",
        "query_type": "entity_property",
        "question": "What is the oxygen balance of CL-20?",
        "expected_entities": ["CL-20"],
        "expected_property": "oxygen_balance",
        "rules_exercised": ["Rule 6", "Rule 1 (may be unverified)"],
        "notes": "Tests less-common property; may trigger Rule 1 if no evidence link.",
    },
    {
        "id": "EP-05",
        "category": "entity_property",
        "query_type": "entity_property",
        "question": "What is the impact sensitivity (h50) of TATB?",
        "expected_entities": ["TATB"],
        "expected_property": "impact_sensitivity",
        "rules_exercised": ["Rule 3 (likely empty — not in domain extractor yet)", "Rule 1"],
        "notes": "Impact sensitivity is NOT in the 8 extracted properties; should trigger Rule 3 → Replanner.",
    },
    {
        "id": "EP-06",
        "category": "entity_property",
        "query_type": "entity_property",
        "question": "TKX-50的密度是多少？",
        "expected_entities": ["TKX-50"],
        "expected_property": "density",
        "rules_exercised": ["Rule 6", "Rule 2"],
        "notes": "Chinese-language query; tests multilingual query understanding.",
    },

    # ── comparison ───────────────────────────────────────────────────────
    {
        "id": "CMP-01",
        "category": "comparison",
        "query_type": "comparison",
        "question": "Compare the detonation velocity of RDX, HMX, and CL-20.",
        "expected_entities": ["RDX", "HMX", "CL-20"],
        "expected_property": "detonation_velocity",
        "rules_exercised": ["Rule 6 (× 3)", "Rule 4 (conflicts if multiple sources)"],
        "notes": "Cross-entity comparison; tests whether compare_values is included in plan.",
    },
    {
        "id": "CMP-02",
        "category": "comparison",
        "query_type": "comparison",
        "question": "Which has higher density: PETN or FOX-7?",
        "expected_entities": ["PETN", "FOX-7"],
        "expected_property": "density",
        "rules_exercised": ["Rule 6 (× 2)", "Rule 2", "Rule 4"],
        "notes": "Two-entity comparison; answer should cite sources.",
    },
    {
        "id": "CMP-03",
        "category": "comparison",
        "query_type": "comparison",
        "question": "对比RDX和HMX的密度和爆速，哪种综合性能更好？",
        "expected_entities": ["RDX", "HMX"],
        "expected_property": "density, detonation_velocity",
        "rules_exercised": ["Rule 6 (× 2)", "Rule 4 (multi-value)"],
        "notes": "Chinese query with two properties; tests Answer Builder synthesis.",
    },

    # ── table ────────────────────────────────────────────────────────────
    {
        "id": "TBL-01",
        "category": "table",
        "query_type": "table",
        "question": "Find tables that list detonation velocity data for energetic materials.",
        "expected_entities": [],
        "expected_property": "detonation_velocity",
        "rules_exercised": ["Rule 5 (find_tables must be in plan)"],
        "notes": "Table-type query; verifies planner forces find_tables.",
    },
    {
        "id": "TBL-02",
        "category": "table",
        "query_type": "table",
        "question": "Show me tables comparing burning rates of AP-based propellants.",
        "expected_entities": ["AP"],
        "expected_property": "burning_rate",
        "rules_exercised": ["Rule 5"],
        "notes": "Propellant table query; tests propellant sub-domain.",
    },
    {
        "id": "TBL-03",
        "category": "table",
        "query_type": "table",
        "question": "列出文献中含有RDX性能数据的表格",
        "expected_entities": ["RDX"],
        "expected_property": "any",
        "rules_exercised": ["Rule 5"],
        "notes": "Chinese table query.",
    },

    # ── general (semantic search) ─────────────────────────────────────────
    {
        "id": "GEN-01",
        "category": "general",
        "query_type": "general",
        "question": "What are the main safety concerns when working with CL-20?",
        "expected_entities": ["CL-20"],
        "expected_property": None,
        "rules_exercised": ["Rule 3 (may be empty)"],
        "notes": "Open-ended query; tests search_memory and Answer Builder.",
    },
    {
        "id": "GEN-02",
        "category": "general",
        "query_type": "general",
        "question": "Summarize recent advances in TKX-50 synthesis.",
        "expected_entities": ["TKX-50"],
        "expected_property": None,
        "rules_exercised": ["Rule 3 (replanner if no results)"],
        "notes": "Synthesis question; tests keyword coverage.",
    },
    {
        "id": "GEN-03",
        "category": "general",
        "query_type": "general",
        "question": "AP基复合固体推进剂的燃速影响因素有哪些？",
        "expected_entities": ["AP"],
        "expected_property": "burning_rate",
        "rules_exercised": ["Rule 3"],
        "notes": "Chinese general query on propellant burning rate factors.",
    },

    # ── conflict detection (Rule 4) ───────────────────────────────────────
    {
        "id": "CONF-01",
        "category": "conflict",
        "query_type": "entity_property",
        "question": "What density values for RDX have been reported across different sources?",
        "expected_entities": ["RDX"],
        "expected_property": "density",
        "rules_exercised": ["Rule 4 — multiple conflicting values expected"],
        "notes": "Deliberately asks for 'across sources'; Rule 4 should list ALL values with citations.",
    },

    # ── empty result / replanner (Rule 3) ────────────────────────────────
    {
        "id": "EMPTY-01",
        "category": "empty_result",
        "query_type": "entity_property",
        "question": "What is the viscosity of HTPB?",
        "expected_entities": ["HTPB"],
        "expected_property": "viscosity",
        "rules_exercised": ["Rule 3 — viscosity not in extracted properties → Replanner"],
        "notes": "viscosity is not in the 8 domain properties; should trigger replanner with reformulated query.",
    },
    {
        "id": "EMPTY-02",
        "category": "empty_result",
        "query_type": "entity_property",
        "question": "What is the crystal polymorph of an unknown compound XYZ-999?",
        "expected_entities": ["XYZ-999"],
        "expected_property": None,
        "rules_exercised": ["Rule 3 — entity not found → EMPTY response"],
        "notes": "Nonexistent entity; system should return graceful EMPTY, not hallucinate.",
    },

    # ── multi-turn (use session_id from previous answer) ──────────────────
    {
        "id": "MT-01a",
        "category": "multi_turn",
        "query_type": "entity_property",
        "question": "What is the density of HMX?",
        "session_id": None,     # first turn; record returned session_id
        "expected_entities": ["HMX"],
        "expected_property": "density",
        "rules_exercised": [],
        "notes": "Turn 1 of multi-turn test. Save session_id for MT-01b.",
    },
    {
        "id": "MT-01b",
        "category": "multi_turn",
        "query_type": "entity_property",
        "question": "那它的爆速呢？",   # "What about its detonation velocity?"
        "session_id": "<<from MT-01a>>",
        "expected_entities": ["HMX"],   # entity resolved from history
        "expected_property": "detonation_velocity",
        "rules_exercised": ["History resolution — 它 (it) refers to HMX"],
        "notes": "Turn 2; tests coreference resolution via conversation history.",
    },
    {
        "id": "MT-02a",
        "category": "multi_turn",
        "query_type": "comparison",
        "question": "Compare the detonation pressures of PETN and RDX.",
        "session_id": None,
        "expected_entities": ["PETN", "RDX"],
        "expected_property": "detonation_pressure",
        "rules_exercised": [],
        "notes": "Turn 1 of a follow-up comparison test.",
    },
    {
        "id": "MT-02b",
        "category": "multi_turn",
        "query_type": "general",
        "question": "Which of the two is more thermally stable?",
        "session_id": "<<from MT-02a>>",
        "expected_entities": ["PETN", "RDX"],
        "expected_property": "melting_point",
        "rules_exercised": ["History: 'the two' = PETN + RDX"],
        "notes": "Turn 2; tests whether session history carries entity context.",
    },
]


def write_questions(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        print(f"\nQuestions file already exists, skipping overwrite → {out_path}")
        print("  (delete eval/eval_questions.json to regenerate defaults)")
        return
    out_path.write_text(json.dumps(EVAL_QUESTIONS, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {len(EVAL_QUESTIONS)} questions → {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Prepare evaluation dataset for mat-planner")
    parser.add_argument(
        "--data-dir",
        default="/home/qisirui/Projects/OSS/分类任务/打包文件/去重最终",
        type=Path,
        help="Root of the MinerU paper dataset",
    )
    parser.add_argument("--limit", type=int, default=8, help="Papers to ingest per category")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingestion, only write questions")
    args = parser.parse_args()

    print("=" * 60)
    print("mat-planner  —  Evaluation Preparation")
    print("=" * 60)

    if not args.skip_ingest:
        print(f"\nSelecting papers from: {args.data_dir}")
        selected = select_papers(args.data_dir, args.limit)
        print(f"\nTotal selected: {len(selected)} papers")

        if selected:
            print("\n" + "=" * 60)
            print("Ingesting papers …")
            print("=" * 60)
            doc_ids = ingest_papers(selected)
            print(f"\nIngested {len(doc_ids)} documents successfully.")
    else:
        print("(skipping ingestion)")

    write_questions(Path("eval/eval_questions.json"))
    print("\nDone. Run the evaluation with:")
    print("  uv run python scripts/run_eval.py")


if __name__ == "__main__":
    main()
