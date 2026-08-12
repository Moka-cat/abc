#!/usr/bin/env python
"""Evaluation runner for mat-planner Planning Agent.

Two modes:
  online  (default): sends questions to a running server via POST /chat
  offline (--offline): calls run_agent() directly — no server needed, ideal for CI

Metrics (with --judge):
  LLM-as-Judge (0–5)  — holistic answer quality
  Faithfulness         — are all answer claims supported by evidence? (0–1)
  Entity recall        — fraction of expected_entities mentioned in answer (0–1)

Usage:
  # Online mode (start server first)
  uv run uvicorn app.api.main:app --reload --port 8000
  uv run python scripts/run_eval.py [--base-url http://localhost:8000] [--judge]

  # Offline mode (no server needed, but needs populated DB)
  uv run python scripts/run_eval.py --offline [--judge]

  # Compare two runs
  uv run python scripts/run_eval.py --compare eval/eval_results_v1.json eval/eval_results_v2.json
"""
import argparse
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx


# ── Faithfulness judge ───────────────────────────────────────────────────────

_FAITHFULNESS_SYSTEM = """\
You are a scientific fact-checker. Given a question, an answer, and the evidence
text that was retrieved, assess whether every factual claim in the answer is
supported by the evidence.

Return ONLY JSON:
{
  "faithfulness": <0.0-1.0>,   // fraction of claims that are supported
  "unsupported": ["claim1", ...]  // claims not found in evidence (if any)
}

Rules:
- Ignore greetings, meta-comments ("Based on the evidence…"), and hedges ("may", "might")
- A claim is "supported" if the evidence contains the same fact (allow paraphrasing)
- A claim is "unsupported" if it asserts a specific number, name, or mechanism not in evidence
- If the answer says "not found" / "insufficient data", faithfulness = 1.0 (honest uncertainty)
"""


def judge_faithfulness(question: str, answer: str, evidence: str, client, model: str) -> dict:
    """Return {faithfulness: float, unsupported: list}."""
    if not answer or answer.startswith("Error") or not evidence:
        return {"faithfulness": 0.0, "unsupported": ["no answer or no evidence"]}
    try:
        prompt = (
            f"Question: {question}\n\n"
            f"Evidence:\n{evidence[:3000]}\n\n"
            f"Answer:\n{answer[:1500]}"
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _FAITHFULNESS_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            timeout=20,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return {
            "faithfulness": max(0.0, min(1.0, float(parsed.get("faithfulness", 1.0)))),
            "unsupported": parsed.get("unsupported") or [],
        }
    except Exception as e:
        return {"faithfulness": -1.0, "unsupported": [f"judge error: {e}"]}


def entity_recall(answer: str, expected_entities: list[str]) -> float:
    """Fraction of expected_entities mentioned (case-insensitive) in the answer."""
    if not expected_entities:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for e in expected_entities if e.lower() in answer_lower)
    return round(hits / len(expected_entities), 3)


# ── LLM-as-Judge ────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
You are an expert evaluator for an AI assistant that answers questions about
energetic materials, propellants, and explosives for domain researchers.

Score the assistant's answer on a 0–5 scale:
  5 — Complete and accurate: answers the question fully, correct values with units,
      cites evidence or acknowledges uncertainty appropriately
  4 — Mostly correct: minor omissions, slightly imprecise phrasing, or 1 missing citation
  3 — Partially correct: key information present but some errors, missing units,
      or incomplete when compared to the question scope
  2 — Mostly incorrect: answer is on-topic but has major factual errors or
      important data missing
  1 — Attempted but failed: response is an attempt but essentially empty or wrong
  0 — Failure: no answer, pure error message, or critical hallucination

Deduct 1 point for each:
  - Numeric value without unit (e.g. "density is 1.816" — missing g/cm³)
  - Definitive claim with no evidence citation for quantitative data
  - Conflicting values not acknowledged when question implies comparison

Return ONLY JSON:
{"score": <0-5>, "reasoning": "<one sentence>", "deductions": ["reason1", ...]}
"""


def judge_answer(
    question: str,
    answer: str,
    category: str,
    llm_client,
    model: str,
) -> dict:
    """Score an answer 0–5. Returns {"score": int, "reasoning": str, "deductions": list}."""
    if not answer or answer.startswith("Error"):
        return {"score": 0, "reasoning": "No answer or error", "deductions": []}

    context = (
        f"Category: {category}\n"
        f"Question: {question}\n\n"
        f"Answer:\n{answer[:2000]}"
    )
    try:
        resp = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": context},
            ],
            response_format={"type": "json_object"},
            timeout=20,
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        score = max(0, min(5, int(parsed.get("score", 0))))
        return {
            "score": score,
            "reasoning": parsed.get("reasoning", ""),
            "deductions": parsed.get("deductions") or [],
        }
    except Exception as e:
        return {"score": -1, "reasoning": f"Judge error: {e}", "deductions": []}


# ── Online mode (HTTP) ───────────────────────────────────────────────────────

def run_online(base_url: str, questions: list[dict], timeout: float) -> list[dict]:
    results = []
    session_ids: dict[str, str] = {}

    for q in questions:
        qid = q["id"]
        question = q["question"]

        session_id = q.get("session_id")
        if isinstance(session_id, str) and session_id.startswith("<<from "):
            ref_id = session_id[7:-2]
            session_id = session_ids.get(ref_id)

        payload: dict = {"question": question}
        if session_id:
            payload["session_id"] = session_id

        print(f"\n[{qid}] {question[:80]}")

        start = time.monotonic()
        try:
            resp = httpx.post(f"{base_url}/chat", json=payload, timeout=timeout)
            elapsed = time.monotonic() - start
            resp.raise_for_status()
            data = resp.json()

            answer = data.get("answer", "")
            ret_session = data.get("session_id", "")
            session_ids[qid] = ret_session

            # Fetch retrieval evidence for faithfulness scoring (best-effort)
            evidence_text = ""
            try:
                ev_resp = httpx.post(
                    f"{base_url}/retrieval/query",
                    json={"query": question, "top_k": 5},
                    timeout=15,
                )
                if ev_resp.status_code == 200:
                    evidence_text = ev_resp.json().get("evidence_text", "")
            except Exception:
                pass

            print(f"  ✓ {elapsed:.1f}s | {answer[:100]}{'…' if len(answer) > 100 else ''}")
            results.append({
                "id": qid, "question": question, "category": q["category"],
                "expected_entities": q.get("expected_entities", []),
                "answer": answer, "session_id": ret_session,
                "evidence_text": evidence_text,
                "iterations": data.get("iterations", 0),
                "tool_names": [t.get("tool") for t in data.get("tool_calls_made", [])],
                "elapsed_sec": round(elapsed, 2), "error": None,
            })
        except httpx.TimeoutException:
            elapsed = time.monotonic() - start
            print(f"  ✗ TIMEOUT {elapsed:.0f}s")
            results.append({"id": qid, "question": question, "category": q["category"],
                            "expected_entities": q.get("expected_entities", []),
                            "error": "timeout", "elapsed_sec": round(elapsed, 2)})
        except Exception as e:
            elapsed = time.monotonic() - start
            print(f"  ✗ ERROR: {e}")
            results.append({"id": qid, "question": question, "category": q["category"],
                            "expected_entities": q.get("expected_entities", []),
                            "error": str(e), "elapsed_sec": round(elapsed, 2)})
    return results


# ── Offline mode (direct agent call) ────────────────────────────────────────

def run_offline(questions: list[dict], timeout: float) -> list[dict]:
    """Call run_agent() directly — no server required."""
    from app.core.database import SessionLocal, create_all_tables
    from app.services.agent import run_agent
    from app.services.session import session_store

    create_all_tables()
    results = []
    session_ids: dict[str, str] = {}

    for q in questions:
        qid = q["id"]
        question = q["question"]

        # Resolve multi-turn session
        session_id = q.get("session_id")
        if isinstance(session_id, str) and session_id.startswith("<<from "):
            ref_id = session_id[7:-2]
            session_id = session_ids.get(ref_id)

        history: list[dict] = []
        if session_id:
            history = session_store.get_history(session_id)

        print(f"\n[{qid}] {question[:80]}")

        start = time.monotonic()
        db = SessionLocal()
        try:
            agent_resp = run_agent(question, db, history=history)
            elapsed = time.monotonic() - start

            answer = agent_resp.answer

            # Store session for follow-up turns
            new_sid = session_id or qid
            session_store.append(new_sid, "user", question)
            session_store.append(new_sid, "assistant", answer)
            session_ids[qid] = new_sid

            print(f"  ✓ {elapsed:.1f}s | {answer[:100]}{'…' if len(answer) > 100 else ''}")
            # Fetch evidence text for faithfulness scoring
            evidence_text = ""
            try:
                from app.services.retrieval import RetrievalService
                ev_db = SessionLocal()
                ev = RetrievalService(ev_db).query(question, top_k=5)
                evidence_text = ev.evidence_text
                ev_db.close()
            except Exception:
                pass
            results.append({
                "id": qid, "question": question, "category": q["category"],
                "expected_entities": q.get("expected_entities", []),
                "answer": answer, "session_id": new_sid,
                "evidence_text": evidence_text,
                "iterations": agent_resp.iterations,
                "tool_names": [s.tool for s in agent_resp.plan],
                "elapsed_sec": round(elapsed, 2), "error": None,
            })
        except Exception as e:
            elapsed = time.monotonic() - start
            print(f"  ✗ ERROR: {e}")
            results.append({"id": qid, "question": question, "category": q["category"],
                            "expected_entities": q.get("expected_entities", []),
                            "error": str(e), "elapsed_sec": round(elapsed, 2)})
        finally:
            db.close()

    return results


# ── Summary + reporting ──────────────────────────────────────────────────────

def print_summary(results: list[dict], use_judge: bool) -> None:
    ok = [r for r in results if not r.get("error")]
    failed = [r for r in results if r.get("error")]

    print("\n" + "=" * 65)
    print(f"Results: {len(ok)}/{len(results)} completed  |  {len(failed)} errors")

    if ok:
        avg_t = sum(r["elapsed_sec"] for r in ok) / len(ok)
        print(f"Avg latency: {avg_t:.1f}s")

    if use_judge:
        scored = [r for r in ok if r.get("judge_score") is not None and r["judge_score"] >= 0]
        if scored:
            avg_score = sum(r["judge_score"] for r in scored) / len(scored)
            dist = {i: sum(1 for r in scored if r["judge_score"] == i) for i in range(6)}
            print(f"\nLLM-as-Judge scores (0–5):  avg = {avg_score:.2f}")
            bar = " | ".join(f"{i}★:{dist[i]}" for i in range(6))
            print(f"  Distribution: {bar}")

        faith_vals = [r["faithfulness"] for r in ok if r.get("faithfulness", -1) >= 0]
        if faith_vals:
            print(f"Faithfulness:               avg = {sum(faith_vals)/len(faith_vals):.3f}  (n={len(faith_vals)})")

        recall_vals = [r["entity_recall"] for r in ok if r.get("entity_recall") is not None]
        if recall_vals:
            print(f"Entity recall:              avg = {sum(recall_vals)/len(recall_vals):.3f}  (n={len(recall_vals)})")

    # Per-category breakdown
    cats: dict[str, list] = {}
    for r in ok:
        cats.setdefault(r.get("category", "?"), []).append(r)
    print("\nBy category:")
    for cat in sorted(cats):
        rows = cats[cat]
        avg_t = sum(r["elapsed_sec"] for r in rows) / len(rows)
        line = f"  {cat:22s}: {len(rows):2d} ok  avg {avg_t:.1f}s"
        if use_judge:
            sc = [r["judge_score"] for r in rows if r.get("judge_score", -1) >= 0]
            if sc:
                line += f"  score {sum(sc)/len(sc):.1f}"
        print(line)

    if failed:
        print(f"\nFailed ({len(failed)}):")
        for r in failed:
            print(f"  [{r['id']}] {r.get('error', '?')}")


def compare_reports(path_a: Path, path_b: Path) -> None:
    """Print a diff table between two eval report files."""
    a = json.loads(path_a.read_text())
    b = json.loads(path_b.read_text())

    label_a = path_a.stem
    label_b = path_b.stem
    metrics = [
        ("succeeded / total", lambda r: f"{r['succeeded']}/{r['total']}"),
        ("avg_latency_sec",   lambda r: f"{r.get('avg_latency_sec', '?')}"),
        ("avg_judge_score",   lambda r: f"{r.get('avg_judge_score', '?')}"),
        ("avg_faithfulness",  lambda r: f"{r.get('avg_faithfulness', '?')}"),
        ("avg_entity_recall", lambda r: f"{r.get('avg_entity_recall', '?')}"),
    ]
    print(f"\n{'Metric':25s}  {label_a:>20s}  {label_b:>20s}")
    print("-" * 70)
    for name, fn in metrics:
        print(f"{name:25s}  {fn(a):>20s}  {fn(b):>20s}")

    # Per-question judge score diff
    scores_a = {r["id"]: r.get("judge_score") for r in a.get("results", [])}
    scores_b = {r["id"]: r.get("judge_score") for r in b.get("results", [])}
    common = set(scores_a) & set(scores_b)
    if common:
        improved = [(qid, scores_a[qid], scores_b[qid])
                    for qid in common
                    if scores_a[qid] is not None and scores_b[qid] is not None
                    and scores_b[qid] > scores_a[qid]]
        regressed = [(qid, scores_a[qid], scores_b[qid])
                     for qid in common
                     if scores_a[qid] is not None and scores_b[qid] is not None
                     and scores_b[qid] < scores_a[qid]]
        if improved:
            print(f"\nImproved in {label_b} (+{len(improved)}):")
            for qid, sa, sb in improved:
                print(f"  [{qid}]  {sa} → {sb}")
        if regressed:
            print(f"\nRegressed in {label_b} (-{len(regressed)}):")
            for qid, sa, sb in regressed:
                print(f"  [{qid}]  {sa} → {sb}")


def main():
    parser = argparse.ArgumentParser(description="mat-planner evaluation runner")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Server base URL (online mode only)")
    parser.add_argument("--questions", default="eval/eval_questions.json", type=Path)
    parser.add_argument("--output", default="eval/eval_results.json", type=Path)
    parser.add_argument("--timeout", type=float, default=200.0,
                        help="Per-question timeout in seconds")
    parser.add_argument("--offline", action="store_true",
                        help="Call run_agent() directly, no server needed")
    parser.add_argument("--judge", action="store_true",
                        help="Score answers with LLM-as-Judge + faithfulness + entity recall")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of questions (0 = all)")
    parser.add_argument("--compare", nargs=2, metavar=("REPORT_A", "REPORT_B"),
                        help="Compare two eval result files and print diff table")
    args = parser.parse_args()

    if args.compare:
        compare_reports(Path(args.compare[0]), Path(args.compare[1]))
        return

    if not args.questions.exists():
        print(f"Questions file not found: {args.questions}")
        raise SystemExit(1)

    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[:args.limit]

    print(f"Running {len(questions)} questions — mode={'offline' if args.offline else 'online'}"
          + (", +judge+faithfulness" if args.judge else ""))

    # Collect answers
    if args.offline:
        results = run_offline(questions, args.timeout)
    else:
        results = run_online(args.base_url, questions, args.timeout)

    # LLM-as-Judge scoring + faithfulness
    if args.judge:
        from app.core.config import settings
        if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
            print("\n[judge] Skipped — LLM_BASE_URL / LLM_MODEL not configured")
        else:
            from app.services.llm_client import get_llm_client
            client = get_llm_client()
            model = settings.LLM_MODEL
            print(f"\nScoring {len(results)} answers with LLM-as-Judge + faithfulness ({model})…")
            for r in results:
                # Entity recall (fast, no LLM)
                r["entity_recall"] = entity_recall(
                    r.get("answer", ""), r.get("expected_entities", [])
                )

                if r.get("error") or not r.get("answer"):
                    r["judge_score"] = 0
                    r["judge_reasoning"] = r.get("error", "no answer")
                    r["judge_deductions"] = []
                    r["faithfulness"] = 0.0
                    r["faithfulness_unsupported"] = []
                    continue

                verdict = judge_answer(r["question"], r["answer"], r.get("category", ""), client, model)
                r["judge_score"] = verdict["score"]
                r["judge_reasoning"] = verdict["reasoning"]
                r["judge_deductions"] = verdict["deductions"]

                faith = judge_faithfulness(
                    r["question"], r["answer"], r.get("evidence_text", ""), client, model
                )
                r["faithfulness"] = faith["faithfulness"]
                r["faithfulness_unsupported"] = faith["unsupported"]

                score_str = f"{verdict['score']}/5  faith={faith['faithfulness']:.2f}  recall={r['entity_recall']:.2f}"
                print(f"  [{r['id']}] {score_str}  {verdict['reasoning'][:60]}")

    print_summary(results, use_judge=args.judge)

    # Write report
    ok = [r for r in results if not r.get("error")]
    faith_vals = [r["faithfulness"] for r in ok if r.get("faithfulness", -1) >= 0]
    recall_vals = [r["entity_recall"] for r in ok if r.get("entity_recall") is not None]
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "offline" if args.offline else args.base_url,
        "judge_enabled": args.judge,
        "total": len(results),
        "succeeded": len(ok),
        "failed": len(results) - len(ok),
        "avg_latency_sec": round(sum(r["elapsed_sec"] for r in ok) / max(len(ok), 1), 2),
        "avg_judge_score": round(
            sum(r.get("judge_score", 0) for r in ok if r.get("judge_score", -1) >= 0)
            / max(sum(1 for r in ok if r.get("judge_score", -1) >= 0), 1), 2
        ) if args.judge else None,
        "avg_faithfulness": round(sum(faith_vals) / len(faith_vals), 3) if faith_vals else None,
        "avg_entity_recall": round(sum(recall_vals) / len(recall_vals), 3) if recall_vals else None,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport → {args.output}")


if __name__ == "__main__":
    main()
