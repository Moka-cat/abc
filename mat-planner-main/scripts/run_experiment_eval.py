"""实验设计 Agent 评估脚本。

评估维度：
  design   (D1-D5)  — 实验方案设计质量
  safety   (S1-S3)  — 安全审查准确性
  analysis (A1-A4)  — 结果分析与偏差诊断
  edge_case(E1-E4)  — 边界场景与鲁棒性

评估指标：
  Pass Rate       — grading_criteria 各项通过率
  LLM Score       — LLM 裁判对方案质量的 0-5 分评分（design 题专用）
  Safety Accuracy — 安全状态与预期一致率（safety 题专用）

用法：
    uv run python scripts/run_experiment_eval.py
    uv run python scripts/run_experiment_eval.py --category design
    uv run python scripts/run_experiment_eval.py --ids D1 D3 S1
    uv run python scripts/run_experiment_eval.py --report eval/exp_report_a.json
    uv run python scripts/run_experiment_eval.py --compare eval/exp_report_a.json eval/exp_report_b.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from loguru import logger
from app.core.database import SessionLocal
from app.services.experiment_design import ExperimentDesignService
from app.services.safety_checker import SafetyCheckerService
from app.services.experiment_analysis import ExperimentAnalysisService


EVAL_QUESTIONS_PATH = Path("eval/experiment_eval_questions.json")
DEFAULT_REPORT_PATH = Path(f"eval/exp_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")


# ─────────────────────────────────────────────────────────────────────────────
# Criterion checkers
# ─────────────────────────────────────────────────────────────────────────────

def _check_criterion(criterion_key: str, criterion_desc: str, result: dict) -> tuple[bool, str]:
    """Evaluate a single grading criterion against the result dict.

    Returns (passed: bool, reason: str).
    Uses simple substring / comparison checks — no LLM needed for most.
    """
    desc = criterion_desc.lower()

    # Helper: deep string search in result
    result_str = json.dumps(result, ensure_ascii=False).lower()

    def contains(*words) -> bool:
        return any(w.lower() in result_str for w in words)

    def get_nested(path: str):
        """Dot-separated path in result dict."""
        obj = result
        for k in path.split("."):
            if not isinstance(obj, dict):
                return None
            obj = obj.get(k)
        return obj

    # ── Design criteria ──
    if "配方包含" in criterion_desc and "ap" in desc and "htpb" in desc:
        form = result.get("protocols", [{}])[0].get("formulation", {})
        has_ap = any("ap" in k.lower() for k in form)
        has_htpb = any("htpb" in k.lower() for k in form)
        has_al = any(k.lower() in ("al", "aluminum", "aluminium") for k in form)
        passed = has_ap and has_htpb and has_al
        return passed, f"AP={has_ap}, HTPB={has_htpb}, Al={has_al}"

    if "分数和" in criterion_desc or "fractions" in desc:
        form = result.get("protocols", [{}])[0].get("formulation", {})
        total = sum(v.get("fraction", 0) if isinstance(v, dict) else 0 for v in form.values())
        passed = 0.95 <= total <= 1.05
        return passed, f"fraction sum={total:.3f}"

    if "燃速预测值" in criterion_desc or "prediction_reasonable" in criterion_key:
        props = result.get("protocols", [{}])[0].get("predicted_properties", {})
        br = props.get("burning_rate", {})
        val = br.get("value") if isinstance(br, dict) else None
        if val is None:
            return False, "no burning_rate prediction"
        passed = 8 <= float(val) <= 25
        return passed, f"predicted burning_rate={val}"

    if "safety_status != blocked" in criterion_desc or "safety_not_blocked" in criterion_key:
        proto = result.get("protocols", [{}])[0]
        status = proto.get("safety_status", "")
        passed = status != "blocked"
        return passed, f"safety_status={status}"

    if "steps 数量" in criterion_desc:
        import re
        m = re.search(r"≥\s*(\d+)", criterion_desc)
        min_steps = int(m.group(1)) if m else 3
        steps = result.get("protocols", [{}])[0].get("steps", [])
        passed = len(steps) >= min_steps
        return passed, f"steps count={len(steps)}"

    if "reference_chunk_ids" in criterion_desc:
        refs = result.get("protocols", [{}])[0].get("reference_chunk_ids") or []
        rationale = (result.get("protocols", [{}])[0].get("rationale") or "").lower()
        passed = len(refs) > 0 or len(rationale) > 20
        return passed, f"refs={len(refs)}, rationale_len={len(rationale)}"

    if "cl-20" in desc or "cl20" in desc:
        form_str = json.dumps(result.get("protocols", [{}])[0].get("formulation", {})).lower()
        passed = "cl-20" in form_str or "cl20" in form_str or "cl 20" in form_str
        return passed, f"CL-20 in formulation: {passed}"

    if "tnt" in desc and "present" in desc:
        form_str = json.dumps(result.get("protocols", [{}])[0].get("formulation", {})).lower()
        passed = "tnt" in form_str
        return passed, f"TNT in formulation: {passed}"

    if "melt_step" in criterion_key:
        steps = result.get("protocols", [{}])[0].get("steps", [])
        passed = any(
            isinstance(s.get("temperature"), (int, float)) and 75 <= s["temperature"] <= 110
            for s in steps
        )
        return passed, f"melt step (75-110°C): {passed}"

    if "strontium" in desc:
        form_str = json.dumps(result.get("protocols", [{}])[0].get("formulation", {})).lower()
        passed = any(kw in form_str for kw in ["strontium", "sr(", "sr ", "锶"])
        return passed, f"strontium present: {passed}"

    if "no_heavy_metals" in criterion_key:
        form_str = json.dumps(result.get("protocols", [{}])[0].get("formulation", {})).lower()
        bad = [kw for kw in ["pb", "lead", "ba", "barium", "cr", "chromium"] if kw in form_str]
        passed = len(bad) == 0
        return passed, f"heavy metals found: {bad}"

    if "ap_present" in criterion_key:
        form_str = json.dumps(result.get("protocols", [{}])[0].get("formulation", {})).lower()
        passed = "ap" in form_str or "ammonium perchlorate" in form_str
        return passed, f"AP present: {passed}"

    if "particle_control" in criterion_key:
        full_str = json.dumps(result, ensure_ascii=False).lower()
        passed = any(kw in full_str for kw in ["particle", "粒径", "粒度", "μm", "um", "mesh"])
        return passed, f"particle size mentioned: {passed}"

    # ── Safety criteria ──
    if "status_correct" in criterion_key or "== approved" in criterion_desc:
        status = result.get("status", "")
        passed = status == "approved"
        return passed, f"safety_status={status}"

    if "no_critical" in criterion_key:
        issues = result.get("issues", [])
        has_critical = any(i.get("severity") == "CRITICAL" for i in issues)
        passed = not has_critical
        return passed, f"critical issues: {has_critical}"

    if "not_approved" in criterion_key or "!= approved" in criterion_desc:
        status = result.get("status", "")
        passed = status != "approved"
        return passed, f"safety_status={status}"

    if "temperature_issue_reported" in criterion_key:
        issues = result.get("issues", [])
        summary = result.get("summary", "").lower()
        has_temp = any(i.get("category") == "temperature" for i in issues) or "温度" in summary
        return has_temp, f"temperature issue: {has_temp}"

    if "status_blocked" in criterion_key:
        status = result.get("status", "")
        passed = status in ("blocked", "needs_review")
        return passed, f"safety_status={status} (expected blocked or needs_review)"

    if "critical_issue" in criterion_key:
        issues = result.get("issues", [])
        has_danger = any(i.get("severity") in ("HIGH", "CRITICAL") for i in issues)
        return has_danger, f"high/critical issues: {has_danger}"

    if "compatibility_issue" in criterion_key:
        issues = result.get("issues", [])
        has_compat = any(i.get("category") == "compatibility" for i in issues)
        return has_compat, f"compatibility issues: {has_compat}"

    # ── Analysis criteria ──
    if "deviation_small" in criterion_key:
        devs = result.get("deviations", {})
        if not devs:
            return False, "no deviations computed"
        all_small = all(
            abs(v.get("deviation_pct", 999)) < 5
            for v in devs.values()
            if v.get("deviation_pct") is not None
        )
        pcts = {k: v.get("deviation_pct") for k, v in devs.items()}
        return all_small, f"deviation_pcts={pcts}"

    if "positive_report" in criterion_key:
        report = (result.get("analysis_report") or "").lower()
        positive_words = ["吻合", "一致", "良好", "达到", "符合", "正常", "acceptable", "good"]
        passed = any(w in report for w in positive_words)
        return passed, f"positive words found: {passed}"

    if "burning_rate_large" in criterion_key:
        devs = result.get("deviations", {})
        br_dev = devs.get("burning_rate", {})
        status = br_dev.get("status", "")
        passed = status == "large"
        pct = br_dev.get("deviation_pct")
        return passed, f"burning_rate deviation_status={status}, pct={pct}"

    if "density_good" in criterion_key:
        devs = result.get("deviations", {})
        d_dev = devs.get("density", {})
        status = d_dev.get("status", "")
        passed = status in ("good", "acceptable")
        return passed, f"density deviation_status={status}"

    if "report_actionable" in criterion_key:
        report = (result.get("analysis_report") or "").lower()
        action_words = ["粒径", "ap", "催化剂", "调整", "建议", "增加", "减少", "particle", "adjust", "recommend"]
        found = [w for w in action_words if w in report]
        passed = len(found) >= 2
        return passed, f"action words found: {found[:5]}"

    if "sensitivity_issue_noted" in criterion_key:
        report = (result.get("analysis_report") or "").lower()
        passed = any(w in report for w in ["感度", "sensitivity", "敏感", "sensitive"])
        return passed, f"sensitivity mentioned: {passed}"

    if "kb_written" in criterion_key:
        passed = result.get("written_to_kb", False) is True
        return passed, f"written_to_kb={result.get('written_to_kb')}"

    # ── Edge case criteria ──
    if "returns_protocol" in criterion_key:
        protos = result.get("protocols", [])
        passed = len(protos) > 0
        return passed, f"protocol count={len(protos)}"

    if "low_confidence" in criterion_key:
        full_str = json.dumps(result, ensure_ascii=False).lower()
        low_words = ["low", "矛盾", "trade-off", "conflict", "无法同时", "不可兼得"]
        found = [w for w in low_words if w in full_str]
        passed = len(found) > 0
        return passed, f"uncertainty words: {found}"

    if "not_failed" in criterion_key:
        status = result.get("status", "")
        passed = status != "failed"
        return passed, f"experiment status={status}"

    if "uncertainty_noted" in criterion_key:
        full_str = json.dumps(result, ensure_ascii=False).lower()
        unc_words = ["未找到", "no data", "不确定", "缺乏", "unknown", "not found", "缺少"]
        found = [w for w in unc_words if w in full_str]
        # Also check for low confidence
        low_conf = "\"confidence\": \"low\"" in full_str or "'confidence': 'low'" in full_str
        passed = len(found) > 0 or low_conf
        return passed, f"uncertainty words={found}, low_conf={low_conf}"

    if "vod_prediction" in criterion_key:
        props = result.get("protocols", [{}])[0].get("predicted_properties", {})
        has_vod = any(
            k in props for k in ["detonation_velocity", "VOD", "vod", "detonation velocity"]
        )
        return has_vod, f"VOD/detonation_velocity in predicted_properties: {has_vod}"

    if "safety_reported" in criterion_key:
        full_str = json.dumps(result, ensure_ascii=False).lower()
        passed = "safety" in full_str or "感度" in full_str or "sensitive" in full_str
        return passed, f"safety info present: {passed}"

    # Fallback: text search
    return False, f"[unhandled criterion] {criterion_key}: {criterion_desc}"


# ─────────────────────────────────────────────────────────────────────────────
# LLM judge for design quality
# ─────────────────────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
You are an expert energetic materials scientist evaluating an AI-generated experimental protocol.
Score the protocol 0-5 based on:
  5 — Excellent: formulation well-grounded in literature, steps complete, safety considered, predictions reasonable
  4 — Good: minor gaps but overall solid
  3 — Acceptable: reasonable but missing key details
  2 — Poor: significant gaps or implausible values
  1 — Very poor: mostly wrong or incomplete
  0 — Failed: no usable protocol generated

Return ONLY: {"score": <0-5>, "reason": "<one sentence>"}
"""


def _llm_judge_design(goal: str, result: dict) -> tuple[float, str]:
    from app.core.config import settings
    if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
        return -1.0, "LLM not configured"
    try:
        from app.services.llm_client import get_llm_client
        proto = (result.get("protocols") or [{}])[0]
        summary = json.dumps({
            "formulation": proto.get("formulation", {}),
            "predicted_properties": proto.get("predicted_properties", {}),
            "step_count": len(proto.get("steps", [])),
            "safety_status": proto.get("safety_status"),
            "rationale": (proto.get("rationale") or "")[:200],
        }, ensure_ascii=False)
        user_msg = f"Research goal: {goal}\n\nGenerated protocol summary:\n{summary}"
        client = get_llm_client()
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=150,
            timeout=15,
        )
        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text)
        return float(data["score"]), data.get("reason", "")
    except Exception as e:
        return -1.0, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Per-question evaluators
# ─────────────────────────────────────────────────────────────────────────────

def _eval_design(q: dict, db) -> dict:
    svc = ExperimentDesignService(db)
    t0 = time.time()
    exp = svc.design(q["goal"], namespace="default", num_candidates=2)
    elapsed = time.time() - t0

    # Build result dict that matches the criterion checker's expected shape
    protos = []
    for p in exp.protocols:
        protos.append({
            "protocol_id": p.id,
            "rank": p.rank,
            "formulation": json.loads(p.formulation_json or "{}"),
            "predicted_properties": json.loads(p.predicted_properties_json or "{}"),
            "steps": json.loads(p.steps_json or "[]"),
            "safety_status": p.safety_status,
            "rationale": p.rationale or "",
            "reference_chunk_ids": json.loads(p.reference_chunk_ids_json or "[]"),
        })

    result = {
        "experiment_id": exp.id,
        "status": exp.status,
        "protocols": protos,
    }

    # Grade criteria
    criteria_results = {}
    for k, v in q.get("grading_criteria", {}).items():
        passed, reason = _check_criterion(k, v, result)
        criteria_results[k] = {"passed": passed, "reason": reason, "criterion": v}

    # LLM judge score
    llm_score, llm_reason = _llm_judge_design(q["goal"], result)

    return {
        "question_id": q["id"],
        "category": q["category"],
        "elapsed_s": round(elapsed, 2),
        "criteria": criteria_results,
        "pass_rate": sum(1 for v in criteria_results.values() if v["passed"]) / max(len(criteria_results), 1),
        "llm_score": llm_score,
        "llm_reason": llm_reason,
        "result_summary": {
            "experiment_id": exp.id,
            "status": exp.status,
            "protocol_count": len(protos),
            "top_formulation": protos[0]["formulation"] if protos else {},
            "top_predicted": protos[0]["predicted_properties"] if protos else {},
            "top_safety": protos[0]["safety_status"] if protos else "",
        },
    }


def _eval_safety(q: dict, db) -> dict:
    svc = SafetyCheckerService(db)
    t0 = time.time()
    report = svc.check(q["formulation"], q.get("steps", []))
    elapsed = time.time() - t0

    result = report.to_dict()

    criteria_results = {}
    for k, v in q.get("grading_criteria", {}).items():
        passed, reason = _check_criterion(k, v, result)
        criteria_results[k] = {"passed": passed, "reason": reason, "criterion": v}

    expected_status = q.get("expected_safety_status")
    expected_level = q.get("expected_level")
    status_match = result["status"] == expected_status if expected_status else True
    level_match = result["overall_level"] == expected_level if expected_level else True

    return {
        "question_id": q["id"],
        "category": q["category"],
        "elapsed_s": round(elapsed, 2),
        "criteria": criteria_results,
        "pass_rate": sum(1 for v in criteria_results.values() if v["passed"]) / max(len(criteria_results), 1),
        "llm_score": -1,
        "result_summary": {
            "actual_status": result["status"],
            "expected_status": expected_status,
            "status_match": status_match,
            "actual_level": result["overall_level"],
            "expected_level": expected_level,
            "level_match": level_match,
            "issue_count": len(result.get("issues", [])),
            "summary": result.get("summary", ""),
        },
    }


def _eval_analysis(q: dict, db) -> dict:
    """Run a quick design then immediately analyze with the setup's measured values."""
    setup = q.get("setup", {})

    # Create a minimal experiment record
    from app.models.orm.experiment import Experiment, ExperimentProtocol
    import uuid
    exp = Experiment(
        id=str(uuid.uuid4()),
        goal=q.get("description", "eval test"),
        namespace="default",
        status="ready",
    )
    db.add(exp)
    db.flush()

    proto = ExperimentProtocol(
        id=str(uuid.uuid4()),
        experiment_id=exp.id,
        rank=0,
        formulation_json=json.dumps({"HMX": {"fraction": 1.0, "role": "explosive"}}),
        predicted_properties_json=json.dumps(setup.get("predicted_properties", {})),
        steps_json="[]",
        safety_status="approved",
    )
    db.add(proto)
    db.flush()

    svc = ExperimentAnalysisService(db)
    t0 = time.time()
    result_orm = svc.analyze(
        experiment_id=exp.id,
        measured_properties=setup.get("measured_properties", {}),
        protocol_id=proto.id,
        write_to_kb=True,
    )
    elapsed = time.time() - t0

    result = {
        "result_id": result_orm.id,
        "analysis_report": result_orm.analysis_report or "",
        "deviations": json.loads(result_orm.deviation_json or "{}"),
        "written_to_kb": result_orm.written_to_kb,
    }

    criteria_results = {}
    for k, v in q.get("grading_criteria", {}).items():
        passed, reason = _check_criterion(k, v, result)
        criteria_results[k] = {"passed": passed, "reason": reason, "criterion": v}

    db.rollback()  # Clean up test data

    return {
        "question_id": q["id"],
        "category": q["category"],
        "elapsed_s": round(elapsed, 2),
        "criteria": criteria_results,
        "pass_rate": sum(1 for v in criteria_results.values() if v["passed"]) / max(len(criteria_results), 1),
        "llm_score": -1,
        "result_summary": {
            "deviations": result["deviations"],
            "written_to_kb": result["written_to_kb"],
            "report_snippet": result["analysis_report"][:200],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main eval runner
# ─────────────────────────────────────────────────────────────────────────────

def run_eval(
    questions: list[dict],
    category_filter: str | None = None,
    id_filter: list[str] | None = None,
) -> list[dict]:
    results = []
    for q in questions:
        if category_filter and q["category"] != category_filter:
            continue
        if id_filter and q["id"] not in id_filter:
            continue

        logger.info(f"Evaluating [{q['id']}] {q.get('goal', q.get('description', ''))[:60]}…")
        db = SessionLocal()
        try:
            if q["category"] == "design":
                res = _eval_design(q, db)
            elif q["category"] == "safety":
                res = _eval_safety(q, db)
            elif q["category"] == "analysis":
                res = _eval_analysis(q, db)
            elif q["category"] == "edge_case":
                # Edge cases: use design or safety evaluator based on whether 'goal' or 'formulation' key exists
                if "goal" in q:
                    res = _eval_design(q, db)
                elif "formulation" in q:
                    res = _eval_safety(q, db)
                else:
                    res = {"question_id": q["id"], "category": q["category"],
                           "pass_rate": 0, "llm_score": -1, "criteria": {},
                           "result_summary": {"note": "multi-step edge case, manual evaluation needed"}}
            else:
                continue
        except Exception as e:
            logger.error(f"  ERROR evaluating {q['id']}: {e}")
            res = {
                "question_id": q["id"],
                "category": q["category"],
                "error": str(e),
                "pass_rate": 0,
                "llm_score": -1,
                "criteria": {},
                "result_summary": {},
            }
        finally:
            db.close()

        passed = sum(1 for v in res["criteria"].values() if v.get("passed", False))
        total = len(res["criteria"])
        logger.info(
            f"  [{q['id']}] pass={passed}/{total} ({res['pass_rate']*100:.0f}%) "
            f"llm={res['llm_score']:.1f} t={res.get('elapsed_s', 0):.1f}s"
        )
        results.append(res)

    return results


def print_report(results: list[dict]) -> None:
    print("\n" + "="*70)
    print("实验设计 Agent 评估报告")
    print("="*70)

    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    overall_pass = []
    overall_llm = []

    for cat, cat_results in by_cat.items():
        cat_pass = [r["pass_rate"] for r in cat_results]
        cat_llm = [r["llm_score"] for r in cat_results if r["llm_score"] >= 0]
        print(f"\n── {cat.upper()} ({len(cat_results)} 题) ──")
        print(f"  平均通过率: {sum(cat_pass)/len(cat_pass)*100:.1f}%")
        if cat_llm:
            print(f"  LLM 裁判均分: {sum(cat_llm)/len(cat_llm):.2f}/5")

        for r in cat_results:
            emoji = "✅" if r["pass_rate"] >= 0.8 else "⚠️" if r["pass_rate"] >= 0.5 else "❌"
            llm_str = f" | LLM={r['llm_score']:.1f}" if r["llm_score"] >= 0 else ""
            print(f"  {emoji} [{r['question_id']}] 通过率={r['pass_rate']*100:.0f}%{llm_str}")
            for k, v in r["criteria"].items():
                icon = "✓" if v.get("passed") else "✗"
                print(f"      {icon} {k}: {v.get('reason','')}")

        overall_pass.extend(cat_pass)
        overall_llm.extend(cat_llm)

    print("\n" + "="*70)
    print(f"总体通过率: {sum(overall_pass)/len(overall_pass)*100:.1f}%  ({len(overall_pass)} 题)")
    if overall_llm:
        print(f"LLM 裁判均分: {sum(overall_llm)/len(overall_llm):.2f}/5  ({len(overall_llm)} 题)")
    print("="*70)


def compare_reports(path_a: str, path_b: str) -> None:
    with open(path_a) as f:
        a = json.load(f)
    with open(path_b) as f:
        b = json.load(f)

    a_by_id = {r["question_id"]: r for r in a["results"]}
    b_by_id = {r["question_id"]: r for r in b["results"]}

    print(f"\n对比报告: {path_a} vs {path_b}")
    print(f"{'ID':<6} {'Before':>8} {'After':>8} {'Delta':>8}")
    print("-" * 35)
    for qid in sorted(set(a_by_id) | set(b_by_id)):
        pa = a_by_id.get(qid, {}).get("pass_rate", 0)
        pb = b_by_id.get(qid, {}).get("pass_rate", 0)
        delta = pb - pa
        arrow = "↑" if delta > 0.05 else "↓" if delta < -0.05 else "="
        print(f"{qid:<6} {pa*100:>7.0f}% {pb*100:>7.0f}% {arrow}{abs(delta)*100:>5.0f}%")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="实验设计 Agent 评估")
    parser.add_argument("--category", choices=["design", "safety", "analysis", "edge_case"])
    parser.add_argument("--ids", nargs="+", help="只评估指定题号，如 D1 S2")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="报告输出路径")
    parser.add_argument("--compare", nargs=2, metavar=("REPORT_A", "REPORT_B"),
                        help="对比两份报告")
    args = parser.parse_args()

    if args.compare:
        compare_reports(args.compare[0], args.compare[1])
        return

    questions = json.loads(EVAL_QUESTIONS_PATH.read_text())["questions"]
    results = run_eval(questions, category_filter=args.category, id_filter=args.ids)
    print_report(results)

    report = {
        "timestamp": datetime.now().isoformat(),
        "question_count": len(results),
        "overall_pass_rate": sum(r["pass_rate"] for r in results) / max(len(results), 1),
        "results": results,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    logger.info(f"报告已保存: {args.report}")


if __name__ == "__main__":
    main()
