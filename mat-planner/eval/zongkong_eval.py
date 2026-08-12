"""Zongkong Agent Evaluation Framework.

Tests four aspects:
  1. Routing accuracy   — does classify_intent route to the correct agent?
                          Includes boundary cases (R-B*) with per-intent breakdown
  2. DataAgent quality  — does the answer contain expected numeric values?
  3. ExperimentAgent    — does the report contain required fields?
  4. SafetyAgent        — does the report contain required keywords + risk level?

Usage:
  # Routing only (fast, no DB needed):
  uv run python eval/zongkong_eval.py --mode routing

  # Full eval (requires running backend DB):
  uv run python eval/zongkong_eval.py --mode all

  # Single category:
  uv run python eval/zongkong_eval.py --mode data
  uv run python eval/zongkong_eval.py --mode experiment
  uv run python eval/zongkong_eval.py --mode safety
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

QUESTIONS_FILE = Path(__file__).parent / "zongkong_eval_questions.json"


# ─────────────────────────────────────────────────────────────────
# Routing evaluation
# ─────────────────────────────────────────────────────────────────

def eval_routing(questions: list[dict]) -> dict:
    from app.services.zongkong.intent import classify_intent

    rows = [q for q in questions if q["category"] == "routing"]
    # Separate boundary cases
    normal_rows   = [q for q in rows if not q["id"].startswith("R-B")]
    boundary_rows = [q for q in rows if q["id"].startswith("R-B")]

    results = []
    correct = 0

    for q in rows:
        t0 = time.monotonic()
        intent = classify_intent(q["query"])
        elapsed = round(time.monotonic() - t0, 2)

        expected = q["expected_intent"]
        got = intent.type.value
        ok = (got == expected)
        if ok:
            correct += 1

        results.append({
            "id":        q["id"],
            "query":     q["query"],
            "expected":  expected,
            "got":       got,
            "entity":    intent.entity,
            "reason":    intent.reason,
            "ok":        ok,
            "boundary":  q["id"].startswith("R-B"),
            "note":      q.get("note", ""),
            "elapsed_s": elapsed,
        })
        status = "✅" if ok else "❌"
        boundary_tag = " [边界]" if q["id"].startswith("R-B") else ""
        print(f"  {status} [{q['id']}]{boundary_tag} {q['query'][:50]}")
        if not ok:
            print(f"       expected={expected!r}  got={got!r}  reason={intent.reason!r}")

    accuracy = correct / len(rows) if rows else 0
    avg_latency = sum(r["elapsed_s"] for r in results) / len(results) if results else 0

    # Normal vs boundary accuracy
    normal_correct   = sum(1 for r in results if not r["boundary"] and r["ok"])
    boundary_correct = sum(1 for r in results if r["boundary"] and r["ok"])
    print(f"\n  Routing accuracy (all): {correct}/{len(rows)} = {accuracy:.1%}  "
          f"avg latency: {avg_latency:.2f}s")
    print(f"  Normal cases:   {normal_correct}/{len(normal_rows)}")
    print(f"  Boundary cases: {boundary_correct}/{len(boundary_rows)}")

    # Per-intent breakdown
    per_intent: dict[str, dict] = {}
    for r in results:
        k = r["expected"]
        per_intent.setdefault(k, {"total": 0, "correct": 0})
        per_intent[k]["total"] += 1
        if r["ok"]:
            per_intent[k]["correct"] += 1
    for k, v in sorted(per_intent.items()):
        pct = v["correct"] / v["total"]
        bar = "✅" if pct == 1.0 else ("⚠️" if pct >= 0.5 else "❌")
        print(f"    {bar} {k:12s}: {v['correct']}/{v['total']} = {pct:.0%}")

    return {
        "accuracy":         accuracy,
        "correct":          correct,
        "total":            len(rows),
        "normal_correct":   normal_correct,
        "normal_total":     len(normal_rows),
        "boundary_correct": boundary_correct,
        "boundary_total":   len(boundary_rows),
        "avg_latency_s":    round(avg_latency, 3),
        "per_intent":       per_intent,
        "results":          results,
    }


# ─────────────────────────────────────────────────────────────────
# DataAgent evaluation (requires DB)
# ─────────────────────────────────────────────────────────────────

def eval_data_agent(questions: list[dict]) -> dict:
    from app.core.database import get_db
    from app.services.zongkong.intent import classify_intent
    from app.services.zongkong.agents import data as data_agent

    rows = [q for q in questions if q["category"] == "data_agent"]
    results = []
    hits = 0

    db = next(get_db())
    try:
        for q in rows:
            intent = classify_intent(q["query"])
            tokens: list[str] = []
            t0 = time.monotonic()

            for chunk in data_agent.run_stream(
                query=q["query"],
                entity=intent.entity or q.get("expected_entity", ""),
                db=db,
                t_start=t0,
            ):
                raw = chunk.removeprefix("data: ").strip()
                try:
                    evt = json.loads(raw)
                    if evt.get("type") == "token":
                        tokens.append(evt["content"])
                except Exception:
                    pass

            elapsed = round(time.monotonic() - t0, 1)
            answer = "".join(tokens)

            vmin, vmax = q.get("expected_value_range", [None, None])
            value_found = False
            if vmin is not None:
                nums = [float(m) for m in re.findall(r"\d+\.?\d*", answer)]
                value_found = any(vmin * 0.8 <= n <= vmax * 1.2 for n in nums)

            if value_found:
                hits += 1

            results.append({
                "id":          q["id"],
                "query":       q["query"],
                "value_found": value_found,
                "answer_len":  len(answer),
                "elapsed_s":   elapsed,
            })
            status = "✅" if value_found else "⚠️"
            print(f"  {status} [{q['id']}] {q['query'][:50]}  ({elapsed}s)")

    finally:
        db.close()

    accuracy = hits / len(rows) if rows else 0
    print(f"\n  DataAgent value-in-range accuracy: {hits}/{len(rows)} = {accuracy:.1%}")
    return {"accuracy": accuracy, "hits": hits, "total": len(rows), "results": results}


# ─────────────────────────────────────────────────────────────────
# ExperimentAgent evaluation (requires DB, slow)
# ─────────────────────────────────────────────────────────────────

def eval_experiment_agent(questions: list[dict]) -> dict:
    from app.core.database import get_db
    from app.services.zongkong.agents import experiment as experiment_agent

    rows = [q for q in questions if q["category"] == "experiment_agent"]
    results = []
    field_hits = 0
    total_fields = 0

    db = next(get_db())
    try:
        for q in rows:
            tokens: list[str] = []
            t0 = time.monotonic()

            for chunk in experiment_agent.run_stream(query=q["query"], db=db, t_start=t0):
                raw = chunk.removeprefix("data: ").strip()
                try:
                    evt = json.loads(raw)
                    if evt.get("type") == "token":
                        tokens.append(evt["content"])
                except Exception:
                    pass

            elapsed = round(time.monotonic() - t0, 1)
            answer = "".join(tokens)

            found_fields = []
            missing_fields = []
            for field in q.get("expected_fields", []):
                if field in answer:
                    found_fields.append(field)
                    field_hits += 1
                else:
                    missing_fields.append(field)
                total_fields += 1

            ok = len(missing_fields) == 0
            results.append({
                "id":             q["id"],
                "query":          q["query"],
                "found_fields":   found_fields,
                "missing_fields": missing_fields,
                "answer_len":     len(answer),
                "elapsed_s":      elapsed,
            })
            status = "✅" if ok else "⚠️"
            print(f"  {status} [{q['id']}] {q['query'][:50]}  ({elapsed}s)")
            if missing_fields:
                print(f"       missing fields: {missing_fields}")

    finally:
        db.close()

    field_accuracy = field_hits / total_fields if total_fields else 0
    print(f"\n  ExperimentAgent field coverage: {field_hits}/{total_fields} = {field_accuracy:.1%}")
    return {
        "field_accuracy": field_accuracy,
        "field_hits":     field_hits,
        "total_fields":   total_fields,
        "results":        results,
    }


# ─────────────────────────────────────────────────────────────────
# SafetyAgent evaluation (requires DB)
# ─────────────────────────────────────────────────────────────────

def eval_safety_agent(questions: list[dict]) -> dict:
    """Evaluate SafetyAgent on keyword coverage and risk-level presence.

    Scoring per question:
      - 1 pt per expected keyword found in the answer
      - 1 pt if a risk-level indicator is present (expected_risk_level=true)
    """
    from app.core.database import get_db
    from app.services.zongkong.agents import safety as safety_agent

    rows = [q for q in questions if q["category"] == "safety_agent"]
    results = []
    total_pts = 0
    earned_pts = 0

    # Risk-level indicators that should appear in a well-formed safety report
    RISK_INDICATORS = [
        "风险等级", "风险级别", "低风险", "中风险", "高风险", "极高",
        "approved", "needs_review", "blocked",
        "低", "中", "高",   # bare labels are less reliable but counted as fallback
    ]

    db = next(get_db())
    try:
        for q in rows:
            tokens: list[str] = []
            t0 = time.monotonic()

            for chunk in safety_agent.run_stream(query=q["query"], db=db, t_start=t0):
                raw = chunk.removeprefix("data: ").strip()
                try:
                    evt = json.loads(raw)
                    if evt.get("type") == "token":
                        tokens.append(evt["content"])
                except Exception:
                    pass

            elapsed = round(time.monotonic() - t0, 1)
            answer = "".join(tokens)

            # Keyword coverage
            expected_kws = q.get("expected_keywords", [])
            found_kws    = [kw for kw in expected_kws if kw in answer]
            missing_kws  = [kw for kw in expected_kws if kw not in answer]
            kw_pts       = len(found_kws)
            total_pts   += len(expected_kws)
            earned_pts  += kw_pts

            # Risk-level presence
            risk_ok = False
            if q.get("expected_risk_level"):
                risk_ok = any(ind in answer for ind in RISK_INDICATORS)
                total_pts  += 1
                earned_pts += (1 if risk_ok else 0)

            ok = (len(missing_kws) == 0) and (risk_ok or not q.get("expected_risk_level"))
            results.append({
                "id":          q["id"],
                "query":       q["query"],
                "found_kws":   found_kws,
                "missing_kws": missing_kws,
                "risk_ok":     risk_ok,
                "answer_len":  len(answer),
                "elapsed_s":   elapsed,
                "note":        q.get("note", ""),
            })
            status = "✅" if ok else "⚠️"
            print(f"  {status} [{q['id']}] {q['query'][:50]}  ({elapsed}s)")
            if missing_kws:
                print(f"       missing keywords: {missing_kws}")
            if q.get("expected_risk_level") and not risk_ok:
                print(f"       ⚠️  no risk-level indicator found in answer")

    finally:
        db.close()

    accuracy = earned_pts / total_pts if total_pts else 0
    print(f"\n  SafetyAgent score: {earned_pts}/{total_pts} pts = {accuracy:.1%}")
    return {
        "score":      earned_pts,
        "total_pts":  total_pts,
        "accuracy":   accuracy,
        "results":    results,
    }


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Zongkong Agent Eval")
    parser.add_argument(
        "--mode",
        choices=["routing", "data", "experiment", "safety", "all"],
        default="routing",
    )
    parser.add_argument("--output", default="", help="Save JSON report to file")
    args = parser.parse_args()

    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    report: dict = {"timestamp": datetime.now().isoformat(), "mode": args.mode}

    if args.mode in ("routing", "all"):
        print("\n=== Routing Evaluation ===")
        report["routing"] = eval_routing(questions)

    if args.mode in ("data", "all"):
        print("\n=== DataAgent Evaluation ===")
        report["data_agent"] = eval_data_agent(questions)

    if args.mode in ("experiment", "all"):
        print("\n=== ExperimentAgent Evaluation ===")
        report["experiment_agent"] = eval_experiment_agent(questions)

    if args.mode in ("safety", "all"):
        print("\n=== SafetyAgent Evaluation ===")
        report["safety_agent"] = eval_safety_agent(questions)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.output or str(Path(__file__).parent / f"zongkong_eval_{ts}.json")
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved to {out_path}")


if __name__ == "__main__":
    main()
