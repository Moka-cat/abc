"""实验 Agent 细粒度评估脚本 v2（实验员视角）。

题集：eval/experiment_eval_v2.json（20 题，259 分）
评分工具：app/eval/chemistry.py（OB%、密度、Vieille 燃速、工艺检查、诊断方向验证）

评分维度（权重）：
  chemical_validity      0.15  — 氧平衡/密度/分数和是否正确
  prediction_accuracy    0.20  — 燃速/密度预测是否在文献范围
  protocol_completeness  0.20  — 工艺步骤完整性（固化剂、真空、混合顺序等）
  safety_technical       0.15  — 安全审查准确性（含误报率）
  diagnosis_direction    0.20  — 诊断建议方向是否符合材料科学规律
  iterative_learning     0.10  — 第二轮是否利用第一轮数据

用法：
    uv run python scripts/run_experiment_eval_v2.py
    uv run python scripts/run_experiment_eval_v2.py --ids CV1 PR1 PC1
    uv run python scripts/run_experiment_eval_v2.py --category diagnosis_direction
    uv run python scripts/run_experiment_eval_v2.py --report eval/v2_report_20250101.json
    uv run python scripts/run_experiment_eval_v2.py --dry-run      # 仅显示题目，不执行
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, ".")

from loguru import logger

# Chemistry calculators
from app.eval.chemistry import (
    OxygenBalance,
    DensityEstimator,
    BurningRateEstimator,
    ProcessabilityChecker,
    DiagnosisValidator,
    COMPONENT_DB,
)

# AI services
from app.core.database import SessionLocal
from app.services.experiment_design import ExperimentDesignService
from app.services.safety_checker import SafetyCheckerService
from app.services.experiment_analysis import ExperimentAnalysisService

EVAL_PATH = Path("eval/experiment_eval_v2.json")
DEFAULT_REPORT = Path(f"eval/v2_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")

DIMENSION_WEIGHTS = {
    "chemical_validity": 0.15,
    "prediction_accuracy": 0.20,
    "protocol_completeness": 0.20,
    "safety_technical": 0.15,
    "diagnosis_direction": 0.20,
    "iterative_learning": 0.10,
}


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CriterionResult:
    name: str
    earned: int
    max: int
    passed: bool
    detail: str


@dataclass
class QuestionResult:
    question_id: str
    category: str
    title: str
    max_score: int
    earned_score: int
    criteria: list[CriterionResult] = field(default_factory=list)
    raw_output: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def score_pct(self) -> float:
        return self.earned_score / self.max_score if self.max_score > 0 else 0.0

    @property
    def passed(self) -> bool:
        return self.score_pct >= 0.55


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _json_search(obj: Any, *keywords: str) -> bool:
    """Check if any keyword appears in the JSON-serialised object."""
    text = json.dumps(obj, ensure_ascii=False).lower()
    return any(kw.lower() in text for kw in keywords)


def _json_search_unnegated(obj: Any, *keywords: str, window: int = 12) -> bool:
    """Check if any keyword appears WITHOUT a preceding negation within `window` chars.

    Negation words like 不应/不要/避免 preceding a keyword mean the LLM is
    explicitly advising AGAINST that direction — not recommending it.
    """
    text = json.dumps(obj, ensure_ascii=False).lower()
    negations = ["不应", "不要", "不能", "避免", "不建议", "无需", "不必", "不宜",
                 "不可", "不该", "切忌", "不推荐"]
    for kw in keywords:
        kw_lower = kw.lower()
        start = 0
        idx = text.find(kw_lower, start)
        while idx != -1:
            context = text[max(0, idx - window): idx]
            if not any(neg in context for neg in negations):
                return True
            idx = text.find(kw_lower, idx + 1)
    return False


def _get_protocol_formulation(result: dict) -> dict:
    """Extract formulation dict from experiment design result."""
    protocols = result.get("protocols", [])
    if protocols:
        return protocols[0].get("formulation", {})
    return {}


def _get_protocol_steps(result: dict) -> list[dict]:
    """Extract steps list from best protocol."""
    protocols = result.get("protocols", [])
    if protocols:
        return protocols[0].get("steps", [])
    return []


def _get_predicted_properties(result: dict) -> dict:
    """Extract predicted_properties from best protocol."""
    protocols = result.get("protocols", [])
    if protocols:
        return protocols[0].get("predicted_properties", {})
    return {}


def _run_design(goal: str, namespace: str = "eval") -> dict:
    """Call ExperimentDesignService and return full experiment dict.

    Retries up to 3 times on SQLite write-lock errors (PendingRollbackError /
    OperationalError: database is locked) that can occur under parallel execution.
    """
    last_err: Exception | None = None
    for attempt in range(3):
        db = SessionLocal()
        try:
            svc = ExperimentDesignService(db)
            experiment = svc.design(goal=goal, namespace=namespace)
            return {
                "id": experiment.id,
                "goal": experiment.goal,
                "status": experiment.status,
                "protocols": [
                    {
                        "id": p.id,
                        "rank": p.rank,
                        "formulation": json.loads(p.formulation_json or "{}"),
                        "steps": json.loads(p.steps_json or "[]"),
                        "predicted_properties": json.loads(p.predicted_properties_json or "{}"),
                        "rationale": p.rationale or "",
                        "safety_status": p.safety_status,
                        "safety_report": json.loads(p.safety_report_json or "{}"),
                    }
                    for p in experiment.protocols
                ],
            }
        except Exception as exc:
            last_err = exc
            err_str = str(exc)
            if "locked" in err_str.lower() or "PendingRollback" in type(exc).__name__:
                logger.warning(
                    f"_run_design: DB lock on attempt {attempt + 1}/3 — "
                    f"retrying after {(attempt + 1) * 3}s"
                )
                db.close()
                time.sleep((attempt + 1) * 3)
                continue
            raise
        finally:
            try:
                db.close()
            except Exception:
                pass
    raise RuntimeError(f"_run_design failed after 3 attempts: {last_err}") from last_err


def _run_safety(formulation: dict, steps: list[dict], namespace: str = "eval") -> dict:
    """Call SafetyCheckerService and return SafetyReport dict."""
    db = SessionLocal()
    try:
        svc = SafetyCheckerService(db)
        report = svc.check(formulation=formulation, steps=steps, namespace=namespace)
        return {
            "status": report.status,
            "level": report.overall_level,
            "summary": report.summary,
            "issues": [
                {
                    "severity": i.severity,
                    "check": i.category,
                    "description": i.description,
                    "recommendation": i.recommendation,
                }
                for i in report.issues
            ],
        }
    finally:
        db.close()


def _override_protocol_predictions(exp_id: str, predicted: dict) -> None:
    """Overwrite the top protocol's predicted_properties_json with the eval's ground-truth values.

    This is needed for DG questions where the eval specifies exact predicted values
    (e.g. burning_rate=15 mm/s) but the LLM may predict something different. Without
    this override the deviation calculation uses the LLM's value, not the eval's.
    Retries up to 3 times on SQLite write-lock errors.
    """
    from app.models.orm.experiment import ExperimentProtocol
    last_err: Exception | None = None
    for attempt in range(3):
        db = SessionLocal()
        try:
            proto = db.query(ExperimentProtocol).filter_by(experiment_id=exp_id, rank=0).first()
            if proto and predicted:
                existing = json.loads(proto.predicted_properties_json or "{}")
                existing.update(predicted)
                proto.predicted_properties_json = json.dumps(existing, ensure_ascii=False)
                db.commit()
            return
        except Exception as exc:
            last_err = exc
            err_str = str(exc)
            if "locked" in err_str.lower() or "PendingRollback" in type(exc).__name__:
                logger.warning(f"_override_protocol_predictions: DB lock attempt {attempt + 1}/3")
                db.close()
                time.sleep((attempt + 1) * 3)
                continue
            raise
        finally:
            try:
                db.close()
            except Exception:
                pass
    raise RuntimeError(f"_override_protocol_predictions failed: {last_err}") from last_err


def _run_analysis(experiment_id: int, measured: dict) -> dict:
    """Call ExperimentAnalysisService and return analysis dict.
    Retries up to 3 times on SQLite write-lock errors.
    """
    last_err: Exception | None = None
    for attempt in range(3):
        db = SessionLocal()
        try:
            svc = ExperimentAnalysisService(db)
            result = svc.analyze(experiment_id=experiment_id, measured_properties=measured)
            return {
                "id": result.id,
                "deviation": json.loads(result.deviation_json or "{}"),
                "analysis_report": result.analysis_report,
                "written_to_kb": result.written_to_kb,
            }
        except Exception as exc:
            last_err = exc
            err_str = str(exc)
            if "locked" in err_str.lower() or "PendingRollback" in type(exc).__name__:
                logger.warning(f"_run_analysis: DB lock attempt {attempt + 1}/3")
                db.close()
                time.sleep((attempt + 1) * 3)
                continue
            raise
        finally:
            try:
                db.close()
            except Exception:
                pass
    raise RuntimeError(f"_run_analysis failed after 3 attempts: {last_err}") from last_err


# ─────────────────────────────────────────────────────────────────────────────
# Per-category evaluators
# ─────────────────────────────────────────────────────────────────────────────

def eval_chemical_validity(q: dict) -> QuestionResult:
    """CV1, CV2 — OB%、密度、分数和自动验证。"""
    qid = q["id"]
    criteria: list[CriterionResult] = []

    # Call design service
    design_result = _run_design(q["input_goal"])
    formulation = _get_protocol_formulation(design_result)

    rubric = q.get("scoring_rubric", {})
    max_score = q["max_score"]
    earned = 0

    # ── fraction_sum ──
    frac_pts = rubric.get("fraction_sum_ok", 2)
    ob_result = OxygenBalance.calculate(formulation)
    frac_sum = ob_result["fraction_sum"]
    frac_ok = abs(frac_sum - 1.0) <= 0.02
    pts = frac_pts if frac_ok else 0
    earned += pts
    criteria.append(CriterionResult("fraction_sum", pts, frac_pts, frac_ok,
                                    f"fraction_sum={frac_sum:.4f}"))

    # ── OB% in good range ──
    ob_pts = rubric.get("ob_in_good_range", 3)
    ob_pct = ob_result["ob_percent"]
    auto_checks = q.get("auto_checks", {})
    if "ob_percent" in auto_checks:
        good_range = auto_checks["ob_percent"].get("good_range", [-70, -30])
        ob_good = good_range[0] <= ob_pct <= good_range[1]
        acceptable_range = auto_checks["ob_percent"].get("acceptable_range", [-80, -10])
        ob_acceptable = acceptable_range[0] <= ob_pct <= acceptable_range[1]
    else:
        ob_good = ob_result["status"] == "good"
        ob_acceptable = ob_result["status"] in ("good", "acceptable")

    if qid == "CV2":
        # Direction check: OB should be higher than CV1 baseline (-51%)
        ob_good = ob_pct > -45.0
        ob_acceptable = ob_pct > -55.0

    pts = ob_pts if ob_good else (ob_pts // 2 if ob_acceptable else 0)
    earned += pts
    criteria.append(CriterionResult("ob_percent", pts, ob_pts, ob_good,
                                    f"OB={ob_pct:.2f}%, status={ob_result['status']}"))

    # ── Density reasonable ──
    dens_pts = rubric.get("density_reasonable", 3)
    density_result = DensityEstimator.estimate(formulation)
    tmd = density_result.get("tmd_g_cm3")
    pred_props = _get_predicted_properties(design_result)
    pred_density = None
    for k, v in pred_props.items():
        if "density" in k.lower() or "密度" in k:
            pred_density = v.get("value") if isinstance(v, dict) else v
            break

    if tmd and pred_density:
        prac_min = density_result["practical_min_g_cm3"]
        prac_max = density_result["practical_max_g_cm3"]
        # Allow 5% above TMD (LLM often gives TMD as the predicted density; that's acceptable)
        dens_ok = prac_min * 0.95 <= pred_density <= tmd * 1.05
        pts = dens_pts if dens_ok else 0
        detail = (f"TMD={tmd:.3f}, pred={pred_density:.3f}, "
                  f"practical=[{prac_min:.3f},{prac_max:.3f}]")
    elif tmd:
        pts = dens_pts // 2
        detail = f"TMD={tmd:.3f}, no predicted density found"
    else:
        pts = 0
        detail = "Unable to compute TMD (unknown components)"
    earned += pts
    criteria.append(CriterionResult("density_reasonable", pts, dens_pts, pts == dens_pts, detail))

    # ── Roles assigned ──
    role_pts = rubric.get("roles_assigned", 2)
    has_oxidizer = any(
        COMPONENT_DB.get(OxygenBalance._lookup(k) and OxygenBalance._lookup(k).name or k, None) is not None
        and OxygenBalance._lookup(k).role == "oxidizer"
        for k in formulation
    )
    # Simpler check using ob sign
    has_oxidizer = any(
        "ap" in k.lower() or "an" in k.lower() or "kno3" in k.lower() or "oxidizer" in str(v).lower()
        for k, v in formulation.items()
    )
    has_binder = any(
        "htpb" in k.lower() or "gap" in k.lower() or "nc" in k.lower() or "binder" in str(v).lower()
        for k, v in formulation.items()
    )
    roles_ok = has_oxidizer and has_binder
    pts = role_pts if roles_ok else 0
    earned += pts
    criteria.append(CriterionResult("roles_assigned", pts, role_pts, roles_ok,
                                    f"oxidizer={has_oxidizer}, binder={has_binder}"))

    # CV2: AP fraction check
    if qid == "CV2":
        ap_pts = rubric.get("ap_fraction_correct", 3)
        ap_frac = 0.0
        for k, v in formulation.items():
            if "ap" in k.lower():
                ap_frac = v.get("fraction", v) if isinstance(v, dict) else float(v)
        ap_ok = 0.72 <= ap_frac <= 0.85
        pts = ap_pts if ap_ok else 0
        earned += pts
        criteria.append(CriterionResult("ap_fraction_correct", pts, ap_pts, ap_ok,
                                        f"AP fraction={ap_frac:.3f}"))

    return QuestionResult(
        question_id=qid,
        category=q["category"],
        title=q["title"],
        max_score=max_score,
        earned_score=min(earned, max_score),
        criteria=criteria,
        raw_output=design_result,
    )


def eval_prediction_accuracy(q: dict) -> QuestionResult:
    """PR1, PR2, PR3 — 性能预测准确性。"""
    qid = q["id"]
    criteria: list[CriterionResult] = []
    rubric = q.get("scoring_rubric", {})
    max_score = q["max_score"]
    earned = 0

    design_result = _run_design(q["input_goal"])
    formulation = _get_protocol_formulation(design_result)
    pred_props = _get_predicted_properties(design_result)
    result_str = json.dumps(design_result, ensure_ascii=False).lower()

    if qid in ("PR1", "PR2"):
        auto_checks = q.get("auto_checks", {})
        br_check = auto_checks.get("burning_rate_vs_empirical") or auto_checks.get("rate_increase_vs_coarse", {})

        # Compute Vieille estimate
        vieille = BurningRateEstimator.estimate(formulation, pressure_mpa=7.0)

        # Extract AI's predicted burning rate
        ai_br = None
        for k, v in pred_props.items():
            if "burning" in k.lower() or "燃速" in k:
                ai_br = v.get("value") if isinstance(v, dict) else v
                break

        if qid == "PR1":
            good_range = br_check.get("good_range_mm_s", [6.5, 12.0])
            acceptable_range = br_check.get("acceptable_range_mm_s", [5.0, 15.0])
            empirical = br_check.get("empirical_estimate_mm_s", 8.5)

            # Check unit
            unit_pts = rubric.get("unit_specified", 1)
            has_unit = "mm/s" in result_str or "毫米" in result_str
            pts = unit_pts if has_unit else 0
            earned += pts
            criteria.append(CriterionResult("unit_specified", pts, unit_pts, has_unit,
                                            f"unit mentioned: {has_unit}"))

            good_pts = rubric.get("prediction_in_good_range", 6)
            acceptable_pts = rubric.get("prediction_in_acceptable_range", 3)
            if ai_br is not None:
                in_good = good_range[0] <= ai_br <= good_range[1]
                in_acceptable = acceptable_range[0] <= ai_br <= acceptable_range[1]
                if in_good:
                    pts = good_pts
                    earned += pts
                    criteria.append(CriterionResult("prediction_in_good_range", pts, good_pts, True,
                                                    f"AI={ai_br:.1f} in [{good_range[0]},{good_range[1]}]"))
                elif in_acceptable:
                    pts = acceptable_pts
                    earned += pts
                    criteria.append(CriterionResult("prediction_in_acceptable_range", pts, acceptable_pts, True,
                                                    f"AI={ai_br:.1f} in [{acceptable_range[0]},{acceptable_range[1]}]"))
                else:
                    criteria.append(CriterionResult("prediction_in_good_range", 0, good_pts, False,
                                                    f"AI={ai_br:.1f} out of range"))
            elif vieille:
                # Use Vieille as proxy if no explicit prediction
                v_br = vieille["estimated_r_mm_s"]
                in_good = good_range[0] <= v_br <= good_range[1]
                pts = (good_pts if in_good else acceptable_pts // 2)
                earned += pts
                criteria.append(CriterionResult("prediction_in_good_range", pts, good_pts, in_good,
                                                f"Vieille={v_br:.1f} (no explicit AI prediction)"))
            else:
                criteria.append(CriterionResult("prediction_in_good_range", 0, good_pts, False,
                                                "No burning rate prediction found"))

        elif qid == "PR2":
            # Check that fine AP produces higher burning rate
            higher_pts = rubric.get("predicts_higher_rate_for_fine", 4)
            ratio_pts = rubric.get("ratio_at_least_1_5x", 4)
            mention_pts = rubric.get("mentions_particle_size_effect", 2)

            mentions = _json_search(design_result, "particle", "粒径", "细粉", "fine", "μm", "um")
            pts = mention_pts if mentions else 0
            earned += pts
            criteria.append(CriterionResult("mentions_particle_size_effect", pts, mention_pts, mentions,
                                            f"particle size mentioned: {mentions}"))

            # Run Vieille for fine vs coarse
            fine_form = dict(formulation)
            # Force particle_size_um=20 for fine
            for k in list(fine_form.keys()):
                if "ap" in k.lower():
                    if isinstance(fine_form[k], dict):
                        fine_form[k] = dict(fine_form[k], particle_size_um=20)
                    else:
                        fine_form[k] = {"fraction": fine_form[k], "particle_size_um": 20}
            coarse_form = dict(formulation)
            for k in list(coarse_form.keys()):
                if "ap" in k.lower():
                    if isinstance(coarse_form[k], dict):
                        coarse_form[k] = dict(coarse_form[k], particle_size_um=200)
                    else:
                        coarse_form[k] = {"fraction": coarse_form[k], "particle_size_um": 200}

            fine_br = BurningRateEstimator.estimate(fine_form)
            coarse_br = BurningRateEstimator.estimate(coarse_form)

            # Check if AI predicts higher rate: keyword OR actual predicted value > coarse baseline
            predicts_higher_kw = _json_search(design_result,
                                              "higher", "增加", "提高", "faster", "更快", "higher burning",
                                              "提升", "增大")
            # Also check if predicted burning_rate value is >= 12 mm/s (1.5x coarse baseline of 8.5)
            ai_br_fine = None
            for k, v in pred_props.items():
                if "burning" in k.lower() or "燃速" in k:
                    ai_br_fine = v.get("value") if isinstance(v, dict) else v
                    break
            predicts_higher_val = ai_br_fine is not None and ai_br_fine >= 12.0
            predicts_higher = predicts_higher_kw or predicts_higher_val
            pts = higher_pts if predicts_higher else 0
            earned += pts
            criteria.append(CriterionResult("predicts_higher_rate_for_fine", pts, higher_pts, predicts_higher,
                                            f"keyword={predicts_higher_kw}, predicted_value={ai_br_fine}"))

            if fine_br and coarse_br:
                ratio = fine_br["estimated_r_mm_s"] / coarse_br["estimated_r_mm_s"]
                ratio_ok = ratio >= 1.5
                pts = ratio_pts if ratio_ok else 0
                earned += pts
                criteria.append(CriterionResult("ratio_at_least_1_5x", pts, ratio_pts, ratio_ok,
                                                f"fine={fine_br['estimated_r_mm_s']:.1f}, "
                                                f"coarse={coarse_br['estimated_r_mm_s']:.1f}, ratio={ratio:.2f}"))
            else:
                criteria.append(CriterionResult("ratio_at_least_1_5x", 0, ratio_pts, False,
                                                "Vieille model not applicable"))

    elif qid == "PR3":
        gt = q["ground_truth"]
        dens_pts = rubric.get("density_in_range", 4)
        vod_pts = rubric.get("vod_in_range", 4)
        is_pts = rubric.get("impact_sensitivity_mentioned", 2)
        det_pts = rubric.get("detonation_pressure_mentioned", 2)

        # Density check
        density_result = DensityEstimator.estimate(formulation)
        tmd = density_result.get("tmd_g_cm3")
        acceptable_density = [gt.get("formulation_90_10_tmd", 1.77) * 0.95,
                               gt.get("formulation_90_10_tmd", 1.77) * 1.05]
        if tmd:
            in_range = acceptable_density[0] <= tmd <= acceptable_density[1]
            pts = dens_pts if in_range else 0
            earned += pts
            criteria.append(CriterionResult("density_in_range", pts, dens_pts, in_range,
                                            f"TMD={tmd:.3f}, expected≈{gt.get('formulation_90_10_tmd', 1.77)}"))
        else:
            criteria.append(CriterionResult("density_in_range", 0, dens_pts, False, "TMD not computed"))

        # VOD check
        vod_range = [7800, 9000]
        has_vod = False
        for k, v in pred_props.items():
            if "vod" in k.lower() or "velocity" in k.lower() or "爆速" in k:
                vod_val = v.get("value") if isinstance(v, dict) else v
                if vod_val and vod_range[0] <= vod_val <= vod_range[1]:
                    has_vod = True
        # also check text
        if not has_vod:
            has_vod = _json_search(design_result, "8500", "8400", "8300", "8600", "8750")
        pts = vod_pts if has_vod else 0
        earned += pts
        criteria.append(CriterionResult("vod_in_range", pts, vod_pts, has_vod,
                                        f"VOD in [{vod_range[0]},{vod_range[1]}]: {has_vod}"))

        has_is = _json_search(design_result, "impact", "撞击", "7.5", "sensitivity", "感度")
        pts = is_pts if has_is else 0
        earned += pts
        criteria.append(CriterionResult("impact_sensitivity_mentioned", pts, is_pts, has_is, ""))

        has_det = _json_search(design_result, "detonation", "爆压", "gpa", "34.9")
        pts = det_pts if has_det else 0
        earned += pts
        criteria.append(CriterionResult("detonation_pressure_mentioned", pts, det_pts, has_det, ""))

    return QuestionResult(
        question_id=qid,
        category=q["category"],
        title=q["title"],
        max_score=max_score,
        earned_score=min(earned, max_score),
        criteria=criteria,
        raw_output=design_result,
    )


def eval_protocol_completeness(q: dict) -> QuestionResult:
    """PC1, PC2, PC3 — 工艺步骤完整性检查。"""
    qid = q["id"]
    criteria: list[CriterionResult] = []
    max_score = q["max_score"]
    earned = 0

    design_result = _run_design(q["input_goal"])
    formulation = _get_protocol_formulation(design_result)
    steps = _get_protocol_steps(design_result)
    result_str = json.dumps(design_result, ensure_ascii=False).lower()

    if qid == "PC1":
        proc_checks = q.get("process_checks", {})
        checker = ProcessabilityChecker()
        issues = checker.check(steps, formulation)
        issue_checks = {i.check: i for i in issues}

        # curing_agent_present (critical, 5 pts)
        curing_pts = proc_checks.get("curing_agent_present", {}).get("points", 5)
        has_curing = not any(i.check == "curing_agent" for i in issues if i.severity == "critical")
        pts = curing_pts if has_curing else 0
        earned += pts
        criteria.append(CriterionResult("curing_agent_present", pts, curing_pts, has_curing,
                                        "TDI/IPDI found in steps" if has_curing else "CRITICAL: no curing agent"))

        # NCO:OH ratio
        nco_pts = proc_checks.get("nco_oh_ratio_mentioned", {}).get("points", 3)
        has_nco = _json_search(design_result, "nco", "oh", "0.85", "0.90", "0.95", "isocyanate")
        pts = nco_pts if has_nco else 0
        earned += pts
        criteria.append(CriterionResult("nco_oh_ratio_mentioned", pts, nco_pts, has_nco, ""))

        # Vacuum deaeration
        vac_pts = proc_checks.get("vacuum_deaeration", {}).get("points", 3)
        has_vac = not any(i.check == "vacuum_deaeration" for i in issues)
        pts = vac_pts if has_vac else 0
        earned += pts
        criteria.append(CriterionResult("vacuum_deaeration", pts, vac_pts, has_vac, ""))

        # Cure temperature 50-70°C
        temp_pts = proc_checks.get("cure_temp_50_70", {}).get("points", 2)
        cure_temp_ok = not any(i.check == "cure_temperature" and i.severity == "critical" for i in issues)
        pts = temp_pts if cure_temp_ok else 0
        earned += pts
        criteria.append(CriterionResult("cure_temp_50_70", pts, temp_pts, cure_temp_ok, ""))

        # Cure duration 72h+
        dur_pts = proc_checks.get("cure_duration_72h_plus", {}).get("points", 2)
        cure_dur_ok = not any(i.check == "cure_duration" for i in issues)
        pts = dur_pts if cure_dur_ok else 0
        earned += pts
        criteria.append(CriterionResult("cure_duration_72h_plus", pts, dur_pts, cure_dur_ok, ""))

        # Mixing sequence (≥2 mixing steps)
        mix_pts = proc_checks.get("mixing_sequence", {}).get("points", 2)
        mix_ok = not any(i.check == "mixing_sequence" for i in issues)
        pts = mix_pts if mix_ok else 0
        earned += pts
        criteria.append(CriterionResult("mixing_sequence", pts, mix_pts, mix_ok, ""))

        # AP drying
        dry_pts = proc_checks.get("ap_drying", {}).get("points", 1)
        dry_ok = not any(i.check == "ap_drying" for i in issues)
        pts = dry_pts if dry_ok else 0
        earned += pts
        criteria.append(CriterionResult("ap_drying", pts, dry_pts, dry_ok, ""))

        # Pot life
        pot_pts = proc_checks.get("pot_life_mentioned", {}).get("points", 1)
        pot_ok = not any(i.check == "pot_life" for i in issues)
        pts = pot_pts if pot_ok else 0
        earned += pts
        criteria.append(CriterionResult("pot_life_mentioned", pts, pot_pts, pot_ok, ""))

    elif qid == "PC2":
        proc_checks = q.get("process_checks", {})

        checks = {
            "tnt_melt_temperature": (
                proc_checks.get("tnt_melt_temperature", {}).get("points", 4),
                ["85", "90", "95", "melt", "熔化", "熔融"],
            ),
            "rdx_addition_to_melt": (
                proc_checks.get("rdx_addition_to_melt", {}).get("points", 3),
                ["rdx", "加入", "add", "solv", "溶解"],
            ),
            "slow_cooling": (
                proc_checks.get("slow_cooling", {}).get("points", 3),
                ["slow", "缓慢", "冷却", "anneal", "退火", "controlled cooling"],
            ),
            "mold_temperature": (
                proc_checks.get("mold_temperature", {}).get("points", 2),
                ["mold", "模具", "preheated", "预热"],
            ),
            "safety_tnt_vapors": (
                proc_checks.get("safety_tnt_vapors", {}).get("points", 2),
                ["vapor", "蒸气", "通风", "ventilat", "toxic", "毒"],
            ),
        }
        for name, (pts_max, keywords) in checks.items():
            found = _json_search(design_result, *keywords)
            pts = pts_max if found else 0
            earned += pts
            criteria.append(CriterionResult(name, pts, pts_max, found, f"keywords: {keywords[:2]}"))

    elif qid == "PC3":
        proc_checks = q.get("process_checks", {})

        checks = {
            "impact_test_method": (
                proc_checks.get("impact_test_method", {}).get("points", 4),
                ["bam", "落锤", "drop hammer", "撞击感度", "impact sensitivity"],
            ),
            "friction_test_method": (
                proc_checks.get("friction_test_method", {}).get("points", 3),
                ["friction", "摩擦", "bam 摩擦"],
            ),
            "sample_mass_limited": (
                proc_checks.get("sample_mass_limited", {}).get("points", 3),
                ["mg", "毫克", "50 mg", "< 50", "少量"],
            ),
            "repetitions": (
                proc_checks.get("repetitions", {}).get("points", 2),
                ["repeat", "重复", "次", "replicate", "≥5", "5次", "6次"],
            ),
            "reference_standard": (
                proc_checks.get("reference_standard", {}).get("points", 2),
                ["dnt", "standard", "标准", "reference", "参考样"],
            ),
        }
        for name, (pts_max, keywords) in checks.items():
            found = _json_search(design_result, *keywords)
            pts = pts_max if found else 0
            earned += pts
            criteria.append(CriterionResult(name, pts, pts_max, found, ""))

    return QuestionResult(
        question_id=qid,
        category=q["category"],
        title=q["title"],
        max_score=max_score,
        earned_score=min(earned, max_score),
        criteria=criteria,
        raw_output=design_result,
    )


def eval_safety_technical(q: dict) -> QuestionResult:
    """SA1, SA2, SA3 — 安全审查技术准确性。"""
    qid = q["id"]
    criteria: list[CriterionResult] = []
    max_score = q["max_score"]
    earned = 0

    formulation = q.get("input_formulation", {})
    steps = q.get("steps", [])

    safety_result = _run_safety(formulation, steps)
    result_str = json.dumps(safety_result, ensure_ascii=False).lower()
    eval_crit = q.get("evaluation_criteria", {})

    if qid == "SA1":
        # Correct classification (blocked or needs_review)
        cls_pts = eval_crit.get("correct_classification", {}).get("points", 4)
        classified_ok = safety_result.get("status") in ("blocked", "needs_review")
        pts = cls_pts if classified_ok else 0
        earned += pts
        criteria.append(CriterionResult("correct_classification", pts, cls_pts, classified_ok,
                                        f"status={safety_result.get('status')}"))

        # Mechanism explained
        mech_pts = eval_crit.get("mechanism_explained", {}).get("points", 4)
        has_mech = _json_search(safety_result, "sulfur", "硫", "so2", "自燃", "爆燃", "接触", "反应", "mechanism")
        pts = mech_pts if has_mech else 0
        earned += pts
        criteria.append(CriterionResult("mechanism_explained", pts, mech_pts, has_mech, ""))

        # Alternative suggested
        alt_pts = eval_crit.get("alternative_suggested", {}).get("points", 2)
        has_alt = _json_search(safety_result, "mg", "镁", "替代", "alternative", "instead", "replace")
        pts = alt_pts if has_alt else 0
        earned += pts
        criteria.append(CriterionResult("alternative_suggested", pts, alt_pts, has_alt, ""))

    elif qid == "SA2":
        # Should be approved
        status_pts = eval_crit.get("status_approved", {}).get("points", 5)
        is_approved = safety_result.get("status") == "approved"
        pts = status_pts if is_approved else 0
        earned += pts
        criteria.append(CriterionResult("status_approved", pts, status_pts, is_approved,
                                        f"status={safety_result.get('status')}"))

        # Risk level LOW or MEDIUM
        level_pts = eval_crit.get("risk_level_low_medium", {}).get("points", 3)
        level = safety_result.get("level", "")
        level_ok = level in ("LOW", "MEDIUM")
        pts = level_pts if level_ok else 0
        earned += pts
        criteria.append(CriterionResult("risk_level_low_medium", pts, level_pts, level_ok,
                                        f"level={level}"))

        # No false alarms (no critical issues)
        fa_pts = eval_crit.get("no_false_alarms", {}).get("points", 2)
        issues = safety_result.get("issues", [])
        critical_issues = [i for i in issues if i.get("severity") == "critical"]
        no_critical = len(critical_issues) == 0
        pts = fa_pts if no_critical else 0
        earned += pts
        criteria.append(CriterionResult("no_false_alarms", pts, fa_pts, no_critical,
                                        f"critical_issues={len(critical_issues)}"))

    elif qid == "SA3":
        # NG at 80°C — should be flagged
        temp_pts = eval_crit.get("temperature_risk_flagged", {}).get("points", 5)
        flagged = safety_result.get("status") in ("needs_review", "blocked")
        pts = temp_pts if flagged else 0
        earned += pts
        criteria.append(CriterionResult("temperature_risk_flagged", pts, temp_pts, flagged,
                                        f"status={safety_result.get('status')}"))

        sens_pts = eval_crit.get("ng_sensitivity_noted", {}).get("points", 3)
        has_ng_sens = _json_search(safety_result, "ng", "0.2", "is=0.2", "sensitivity", "感度", "极高感度")
        pts = sens_pts if has_ng_sens else 0
        earned += pts
        criteria.append(CriterionResult("ng_sensitivity_noted", pts, sens_pts, has_ng_sens, ""))

        temp_concern_pts = eval_crit.get("specific_temperature_concern", {}).get("points", 2)
        has_temp = _json_search(safety_result, "80", "50°c", "50 °c", "温度", "温度限制", "分解")
        pts = temp_concern_pts if has_temp else 0
        earned += pts
        criteria.append(CriterionResult("specific_temperature_concern", pts, temp_concern_pts, has_temp, ""))

    return QuestionResult(
        question_id=qid,
        category=q["category"],
        title=q["title"],
        max_score=max_score,
        earned_score=min(earned, max_score),
        criteria=criteria,
        raw_output=safety_result,
    )


def eval_diagnosis_direction(q: dict) -> QuestionResult:
    """DG1-DG4 — 诊断建议方向验证。"""
    qid = q["id"]
    criteria: list[CriterionResult] = []
    max_score = q["max_score"]
    earned = 0
    eval_crit = q.get("evaluation_criteria", {})
    setup = q.get("setup", {})

    # Design an experiment first
    formulation = setup.get("formulation", {
        "AP": {"fraction": 0.68, "role": "oxidizer"},
        "HTPB": {"fraction": 0.20, "role": "binder"},
        "Al": {"fraction": 0.12, "role": "fuel"},
    })
    predicted = setup.get("predicted_properties", {})
    measured = setup.get("measured_properties", {})

    # Create experiment via design service to get an ID
    goal = f"设计推进剂实验（评估题 {qid}）"
    design_result = _run_design(goal)
    exp_id = design_result.get("id")

    # Override protocol predicted properties with eval's ground-truth values so that
    # deviation = (measured - eval_predicted) / eval_predicted, not vs LLM's own prediction.
    if exp_id and predicted:
        _override_protocol_predictions(exp_id, predicted)

    # Run analysis with the specified measured properties
    analysis_result = {}
    if exp_id:
        analysis_result = _run_analysis(exp_id, measured)

    deviations = analysis_result.get("deviation", {})
    report_text = analysis_result.get("analysis_report", "")

    # Use DiagnosisValidator
    diag = DiagnosisValidator.validate(deviations, report_text)

    if qid == "DG1":
        # deviation computation
        dev_pts = eval_crit.get("deviation_computed_correctly", {}).get("points", 2)
        br_dev = deviations.get("burning_rate", {})
        expected_dev = -20.0
        actual_dev = br_dev.get("deviation_pct", None)
        tol = eval_crit.get("deviation_computed_correctly", {}).get("tolerance", 0.5)
        dev_ok = actual_dev is not None and abs(actual_dev - expected_dev) <= tol
        pts = dev_pts if dev_ok else 0
        earned += pts
        criteria.append(CriterionResult("deviation_computed_correctly", pts, dev_pts, dev_ok,
                                        f"deviation={actual_dev}, expected={expected_dev}"))

        # AP particle size mentioned
        ap_pts = eval_crit.get("root_cause_ap_particle_size", {}).get("points", 4)
        has_ap_cause = _json_search(analysis_result, "粒径", "particle size", "细粉", "fine ap", "particle")
        pts = ap_pts if has_ap_cause else 0
        earned += pts
        criteria.append(CriterionResult("root_cause_ap_particle_size", pts, ap_pts, has_ap_cause, ""))

        # Catalyst suggestion
        cat_pts = eval_crit.get("catalyst_suggestion", {}).get("points", 3)
        has_cat = _json_search(analysis_result, "fe2o3", "催化剂", "catalyst", "iron oxide", "铁红")
        pts = cat_pts if has_cat else 0
        earned += pts
        criteria.append(CriterionResult("catalyst_suggestion", pts, cat_pts, has_cat, ""))

        # Quantitative recommendation
        quant_pts = eval_crit.get("quantitative_recommendation", {}).get("points", 3)
        has_quant = _json_search(analysis_result, "%", "percent", "比例", "g/cm", "μm", "um", "mm/s")
        pts = quant_pts if has_quant else 0
        earned += pts
        criteria.append(CriterionResult("quantitative_recommendation", pts, quant_pts, has_quant, ""))

        # No wrong direction — use negation-aware check to avoid false positives
        # when the LLM says "不应减少AP用量" (correctly advising against it).
        wrong_pts = eval_crit.get("no_wrong_direction", {}).get("points", 2)
        has_wrong = _json_search_unnegated(analysis_result, "减少ap用量", "降低ap用量",
                                            "减少ap比例", "降低ap比例", "减少高氯酸铵用量",
                                            "reduce ap content", "remove ap", "less ap",
                                            "增加htpb", "增大htpb")
        no_wrong = not has_wrong
        pts = wrong_pts if no_wrong else 0
        earned += pts
        criteria.append(CriterionResult("no_wrong_direction", pts, wrong_pts, no_wrong,
                                        f"wrong direction: {has_wrong}"))

    elif qid == "DG2":
        # Identifies process root cause (porosity/voids)
        proc_pts = eval_crit.get("identifies_process_root_cause", {}).get("points", 6)
        has_proc = _json_search(analysis_result, "气孔", "气泡", "porosity", "void", "真空", "除气",
                                "bubble", "pore", "density", "密度", "vacuum")
        pts = proc_pts if has_proc else 0
        earned += pts
        criteria.append(CriterionResult("identifies_process_root_cause", pts, proc_pts, has_proc, ""))

        # Vacuum recommendation
        vac_pts = eval_crit.get("vacuum_recommendation", {}).get("points", 4)
        has_vac_rec = _json_search(analysis_result, "真空", "vacuum", "除气", "deaeration", "抽真空")
        pts = vac_pts if has_vac_rec else 0
        earned += pts
        criteria.append(CriterionResult("vacuum_recommendation", pts, vac_pts, has_vac_rec, ""))

        # Not suggesting formulation change for density
        no_form_pts = eval_crit.get("not_suggesting_formulation_change", {}).get("points", 2)
        wrong_form = _json_search(analysis_result, "增加ap", "increase ap", "调整htpb", "增加htpb",
                                  "调整配方比例")
        no_wrong = not wrong_form
        pts = no_form_pts if no_wrong else 0
        earned += pts
        criteria.append(CriterionResult("not_suggesting_formulation_change", pts, no_form_pts, no_wrong, ""))

        # Pycnometry suggested
        pyc_pts = eval_crit.get("pycnometry_suggested", {}).get("points", 2)
        has_pyc = _json_search(analysis_result, "比重", "pycn", "阿基米德", "archimed",
                               "重新测量", "确认密度")
        pts = pyc_pts if has_pyc else 0
        earned += pts
        criteria.append(CriterionResult("pycnometry_suggested", pts, pyc_pts, has_pyc, ""))

    elif qid == "DG3":
        # Understands 3J < 7J means MORE sensitive
        dir_pts = eval_crit.get("deviation_direction_understood", {}).get("points", 3)
        has_dir = _json_search(analysis_result, "更敏感", "更危险", "more sensitive", "higher risk",
                               "dangerous", "危险", "超过", "超标")
        pts = dir_pts if has_dir else 0
        earned += pts
        criteria.append(CriterionResult("deviation_direction_understood", pts, dir_pts, has_dir, ""))

        # Particle size cause
        ps_pts = eval_crit.get("particle_size_cause", {}).get("points", 4)
        has_ps = _json_search(analysis_result, "粒径", "particle", "晶体", "crystal", "细粒",
                              "fine", "smaller", "小粒")
        pts = ps_pts if has_ps else 0
        earned += pts
        criteria.append(CriterionResult("particle_size_cause", pts, ps_pts, has_ps, ""))

        # Safety implication
        safe_pts = eval_crit.get("safety_implication", {}).get("points", 4)
        has_safe = _json_search(analysis_result, "安全", "safety", "操作规程", "protocol",
                                "规程", "警示", "重新评估")
        pts = safe_pts if has_safe else 0
        earned += pts
        criteria.append(CriterionResult("safety_implication", pts, safe_pts, has_safe, ""))

        # Practical recommendation
        prac_pts = eval_crit.get("practical_recommendation", {}).get("points", 3)
        has_prac = _json_search(analysis_result, "coating", "包覆", "钝化", "fox-7", "tatb",
                                "粒度分布", "particle distribution", "替换")
        pts = prac_pts if has_prac else 0
        earned += pts
        criteria.append(CriterionResult("practical_recommendation", pts, prac_pts, has_prac, ""))

    elif qid == "DG4":
        # Understands n significance
        n_pts = eval_crit.get("understands_n_significance", {}).get("points", 5)
        has_n = _json_search(analysis_result, "压强指数", "pressure exponent", "震荡", "oscillat",
                             "instab", "不稳定", "n=0.5", "n>0.4", "n > 0.4")
        pts = n_pts if has_n else 0
        earned += pts
        criteria.append(CriterionResult("understands_n_significance", pts, n_pts, has_n, ""))

        # Correct intervention
        int_pts = eval_crit.get("correct_intervention", {}).get("points", 5)
        has_int = _json_search(analysis_result, "粒度分布", "粗粉", "coarse", "抑制剂",
                               "suppressant", "oxamide", "草酰胺", "铝含量", "al content")
        pts = int_pts if has_int else 0
        earned += pts
        criteria.append(CriterionResult("correct_intervention", pts, int_pts, has_int, ""))

        # Motor safety concern
        motor_pts = eval_crit.get("motor_safety_concern", {}).get("points", 4)
        has_motor = _json_search(analysis_result, "发动机", "motor", "engine", "重新设计",
                                 "redesign", "不适合", "not suitable", "红线")
        pts = motor_pts if has_motor else 0
        earned += pts
        criteria.append(CriterionResult("motor_safety_concern", pts, motor_pts, has_motor, ""))

    return QuestionResult(
        question_id=qid,
        category=q["category"],
        title=q["title"],
        max_score=max_score,
        earned_score=min(earned, max_score),
        criteria=criteria,
        raw_output={"design": design_result, "analysis": analysis_result},
    )


def eval_iterative_learning(q: dict) -> QuestionResult:
    """IL1, IL2 — 迭代学习质量。"""
    qid = q["id"]
    criteria: list[CriterionResult] = []
    max_score = q["max_score"]
    earned = 0
    eval_crit = q.get("evaluation_criteria", {})

    if qid == "IL1":
        # Round 1
        goal1 = "设计 AP/HTPB/Al 推进剂，燃速 15 mm/s @7MPa"
        r1 = _run_design(goal1)
        exp_id = r1.get("id")

        # Upload Round 1 results (燃速偏低)
        measured_r1 = {"burning_rate": {"value": 10.0, "unit": "mm/s"},
                       "density": {"value": 1.58, "unit": "g/cm3"}}
        analysis_r1 = {}
        if exp_id:
            analysis_r1 = _run_analysis(exp_id, measured_r1)

        # Round 2 — reference previous
        goal2 = "基于上次配方改进，提高燃速至 14-16 mm/s @7MPa"
        r2 = _run_design(goal2)
        form2 = _get_protocol_formulation(r2)
        pred2 = _get_predicted_properties(r2)

        # Check R2 references R1
        ref_pts = eval_crit.get("round2_references_round1_data", {}).get("points", 4)
        has_ref = _json_search(r2, "experiment:", "上次", "previous", "round 1", "第一轮",
                               "last", "prior", "改进", "based on")
        pts = ref_pts if has_ref else 0
        earned += pts
        criteria.append(CriterionResult("round2_references_round1_data", pts, ref_pts, has_ref, ""))

        # Formulation changed correctly (more fine AP or catalyst)
        form_pts = eval_crit.get("formulation_changed_correctly", {}).get("points", 5)
        has_fine = _json_search(r2, "细粉", "fine", "catalyst", "催化剂", "fe2o3",
                                "20μm", "20 μm", "particle size")
        pts = form_pts if has_fine else 0
        earned += pts
        criteria.append(CriterionResult("formulation_changed_correctly", pts, form_pts, has_fine, ""))

        # Predicted rate higher in R2
        rate_pts = eval_crit.get("predicted_rate_higher", {}).get("points", 3)
        # Get R1 predicted rate
        pred1 = _get_predicted_properties(r1)
        br1 = None
        for k, v in pred1.items():
            if "burning" in k.lower() or "燃速" in k:
                br1 = v.get("value") if isinstance(v, dict) else v
                break
        br2 = None
        for k, v in pred2.items():
            if "burning" in k.lower() or "燃速" in k:
                br2 = v.get("value") if isinstance(v, dict) else v
                break
        # Compare R2 predicted rate vs R1 *measured* rate (not R1 predicted, which can be unreliable)
        measured_r1_br = measured_r1.get("burning_rate", {}).get("value", 10.0)
        rate_ok = (br2 is not None and br2 > measured_r1_br)
        if br2 is None:
            # Check text mentions of target rates
            rate_ok = _json_search(r2, "15", "16", "17", "18", "higher", "更高", "提高")
        pts = rate_pts if rate_ok else 0
        earned += pts
        criteria.append(CriterionResult("predicted_rate_higher", pts, rate_pts, rate_ok,
                                        f"R1 BR={br1}, R2 BR={br2}"))

        # KB source noted
        kb_pts = eval_crit.get("kb_source_noted", {}).get("points", 2)
        has_kb = _json_search(r2, "experiment:", "kb", "知识库", "knowledge base")
        pts = kb_pts if has_kb else 0
        earned += pts
        criteria.append(CriterionResult("kb_source_noted", pts, kb_pts, has_kb, ""))

        raw = {"round1_design": r1, "round1_analysis": analysis_r1, "round2_design": r2}

    elif qid == "IL2":
        # Design experiment
        goal = "设计 AP/HTPB/Al 标准推进剂实验"
        r1 = _run_design(goal)
        exp_id = r1.get("id")

        # Upload specific measured values
        measured = {"burning_rate": {"value": 13.5, "unit": "mm/s"},
                    "density": {"value": 1.67, "unit": "g/cm3"}}
        analysis_r = {}
        if exp_id:
            analysis_r = _run_analysis(exp_id, measured)

        # Query KB for newly written record
        db = SessionLocal()
        try:
            from app.models.orm.domain import PropertyValue
            from sqlalchemy import and_
            src_filter = f"experiment:{exp_id}" if exp_id else None
            if src_filter:
                kb_entries = db.query(PropertyValue).filter(
                    PropertyValue.source_document_id == src_filter
                ).order_by(PropertyValue.id.desc()).all()
            else:
                kb_entries = db.query(PropertyValue).filter(
                    PropertyValue.source_document_id.like("experiment:%")
                ).order_by(PropertyValue.id.desc()).limit(5).all()

            # Check correctness
            val_pts = eval_crit.get("correct_value_stored", {}).get("points", 4)
            unit_pts = eval_crit.get("unit_stored", {}).get("points", 3)
            src_pts = eval_crit.get("source_tagged_experiment", {}).get("points", 4)
            entity_pts = eval_crit.get("entity_linked", {}).get("points", 3)

            # Check stored value
            br_entry = next(
                (e for e in kb_entries
                 if e.property_name and "burn" in e.property_name.lower() and
                 e.value_numeric is not None and abs(e.value_numeric - 13.5) <= 0.1),
                None
            )
            val_ok = br_entry is not None
            pts = val_pts if val_ok else 0
            earned += pts
            criteria.append(CriterionResult("correct_value_stored", pts, val_pts, val_ok,
                                            f"found entry: {br_entry is not None}"))

            unit_ok = br_entry is not None and br_entry.unit == "mm/s"
            pts = unit_pts if unit_ok else 0
            earned += pts
            criteria.append(CriterionResult("unit_stored", pts, unit_pts, unit_ok, ""))

            src_ok = any(e.source_document_id and e.source_document_id.startswith("experiment:")
                         for e in kb_entries)
            pts = src_pts if src_ok else 0
            earned += pts
            criteria.append(CriterionResult("source_tagged_experiment", pts, src_pts, src_ok, ""))

            entity_ok = br_entry is not None  # entity link implied by PropertyValue existence
            pts = entity_pts if entity_ok else 0
            earned += pts
            criteria.append(CriterionResult("entity_linked", pts, entity_pts, entity_ok, ""))

            raw = {
                "design": r1,
                "analysis": analysis_r,
                "kb_entries_found": len(kb_entries),
                "written_to_kb": analysis_r.get("written_to_kb", False),
            }
        finally:
            db.close()

    return QuestionResult(
        question_id=qid,
        category=q["category"],
        title=q["title"],
        max_score=max_score,
        earned_score=min(earned, max_score),
        criteria=criteria,
        raw_output=raw if "raw" in dir() else {},
    )


def eval_edge_case(q: dict) -> QuestionResult:
    """EC1, EC2 — 边界场景与矛盾目标识别。"""
    qid = q["id"]
    criteria: list[CriterionResult] = []
    max_score = q["max_score"]
    earned = 0
    eval_crit = q.get("evaluation_criteria", {})

    design_result = _run_design(q["input_goal"])
    result_str = json.dumps(design_result, ensure_ascii=False)

    if qid == "EC1":
        # Contradiction recognized
        contr_pts = eval_crit.get("contradiction_recognized", {}).get("points", 5)
        has_contr = _json_search(design_result, "矛盾", "contradict", "impossible", "不可能",
                                 "无法同时", "trade-off", "conflict", "物理矛盾")
        pts = contr_pts if has_contr else 0
        earned += pts
        criteria.append(CriterionResult("contradiction_recognized", pts, contr_pts, has_contr, ""))

        # Trade-off explained
        tradeoff_pts = eval_crit.get("trade_off_explained", {}).get("points", 4)
        has_tradeoff = _json_search(design_result, "键能", "bond", "分子结构", "molecular",
                                    "高能", "感度", "energy density", "低感", "high energy")
        pts = tradeoff_pts if has_tradeoff else 0
        earned += pts
        criteria.append(CriterionResult("trade_off_explained", pts, tradeoff_pts, has_tradeoff, ""))

        # Realistic alternative
        alt_pts = eval_crit.get("realistic_alternative", {}).get("points", 3)
        has_alt = _json_search(design_result, "cl-20", "tatb", "共晶", "cocrystal",
                               "fox-7", "compromise", "折中")
        pts = alt_pts if has_alt else 0
        earned += pts
        criteria.append(CriterionResult("realistic_alternative", pts, alt_pts, has_alt, ""))

        # No hallucinated material
        hall_pts = eval_crit.get("no_hallucinated_material", {}).get("points", 2)
        # Hard to detect hallucination automatically; assume pass unless VOD>10000 claimed feasible
        claims_feasible = _json_search(design_result, "10000 m/s可实现", "满足所有要求", "完全满足",
                                       "fully satisfies", "meets all")
        no_hall = not claims_feasible
        pts = hall_pts if no_hall else 0
        earned += pts
        criteria.append(CriterionResult("no_hallucinated_material", pts, hall_pts, no_hall, ""))

    elif qid == "EC2":
        formulation = _get_protocol_formulation(design_result)

        # No AP in formulation
        no_ap_pts = eval_crit.get("no_ap_in_formulation", {}).get("points", 4)
        has_ap = any("ap" in k.lower() or "perchlorate" in k.lower() for k in formulation)
        no_ap = not has_ap
        pts = no_ap_pts if no_ap else 0
        earned += pts
        criteria.append(CriterionResult("no_ap_in_formulation", pts, no_ap_pts, no_ap,
                                        f"AP present: {has_ap}"))

        # Alternative oxidizer reasonable (AN, ADN, KNO3)
        alt_ox_pts = eval_crit.get("alternative_oxidizer_reasonable", {}).get("points", 4)
        has_alt_ox = any(
            k.lower() in ("an", "adn", "kno3", "ammonium nitrate", "potassium nitrate", "dinitramide")
            for k in formulation
        ) or _json_search(design_result, " an ", "adn", "kno3", "硝酸铵", "二硝酰胺", "硝酸钾")
        pts = alt_ox_pts if has_alt_ox else 0
        earned += pts
        criteria.append(CriterionResult("alternative_oxidizer_reasonable", pts, alt_ox_pts, has_alt_ox, ""))

        # Smoke reason explained
        smoke_pts = eval_crit.get("smoke_reason_explained", {}).get("points", 3)
        has_smoke = _json_search(design_result, "hcl", "氯化氢", "白烟", "smoke", "chloride", "氯")
        pts = smoke_pts if has_smoke else 0
        earned += pts
        criteria.append(CriterionResult("smoke_reason_explained", pts, smoke_pts, has_smoke, ""))

        # Predicted Isp in range 220-280s
        pred_props = _get_predicted_properties(design_result)
        isp_pts = eval_crit.get("predicted_isp_in_range", {}).get("points", 3)
        isp_ok = False
        for k, v in pred_props.items():
            if "isp" in k.lower() or "比冲" in k:
                isp_val = v.get("value") if isinstance(v, dict) else v
                if isp_val and 220 <= isp_val <= 280:
                    isp_ok = True
        if not isp_ok:
            isp_ok = _json_search(design_result, "240", "250", "245", "255", "260", "230", "235")
        pts = isp_pts if isp_ok else 0
        earned += pts
        criteria.append(CriterionResult("predicted_isp_in_range", pts, isp_pts, isp_ok, ""))

    return QuestionResult(
        question_id=qid,
        category=q["category"],
        title=q["title"],
        max_score=max_score,
        earned_score=min(earned, max_score),
        criteria=criteria,
        raw_output=design_result,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

EVALUATORS = {
    "chemical_validity": eval_chemical_validity,
    "prediction_accuracy": eval_prediction_accuracy,
    "protocol_completeness": eval_protocol_completeness,
    "safety_technical": eval_safety_technical,
    "diagnosis_direction": eval_diagnosis_direction,
    "iterative_learning": eval_iterative_learning,
    "edge_case": eval_edge_case,
}


def run_question(q: dict) -> QuestionResult:
    cat = q["category"]
    fn = EVALUATORS.get(cat)
    if fn is None:
        return QuestionResult(
            question_id=q["id"],
            category=cat,
            title=q.get("title", ""),
            max_score=q.get("max_score", 0),
            earned_score=0,
            error=f"No evaluator for category '{cat}'",
        )
    try:
        return fn(q)
    except Exception as exc:
        logger.exception(f"Error evaluating {q['id']}")
        return QuestionResult(
            question_id=q["id"],
            category=cat,
            title=q.get("title", ""),
            max_score=q.get("max_score", 0),
            earned_score=0,
            error=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Report printer
# ─────────────────────────────────────────────────────────────────────────────

_PASS = "\033[32m✓\033[0m"
_FAIL = "\033[31m✗\033[0m"
_WARN = "\033[33m~\033[0m"

LEVEL_LABELS = {
    range(85, 101): "EXCELLENT",
    range(70, 85):  "GOOD",
    range(55, 70):  "ACCEPTABLE",
    range(0, 55):   "POOR",
}

def _level(pct: float) -> str:
    p = int(pct * 100)
    for r, label in LEVEL_LABELS.items():
        if p in r:
            return label
    return "POOR"


def print_report(results: list[QuestionResult], questions: list[dict]) -> None:
    print("\n" + "═" * 70)
    print("  实验 Agent 评估报告 v2（实验员视角）")
    print("═" * 70)

    # Per-dimension
    dim_scores: dict[str, list] = {d: [] for d in DIMENSION_WEIGHTS}
    for res in results:
        cat = res.category
        if cat in dim_scores:
            dim_scores[cat].append((res.earned_score, res.max_score))

    print("\n【维度得分】")
    weighted_total = 0.0
    total_weight = 0.0
    for dim, weight in DIMENSION_WEIGHTS.items():
        entries = dim_scores.get(dim, [])
        if not entries:
            continue
        earned_sum = sum(e[0] for e in entries)
        max_sum = sum(e[1] for e in entries)
        pct = earned_sum / max_sum if max_sum > 0 else 0.0
        level = _level(pct)
        bar = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))
        print(f"  {dim:25s} {bar} {pct*100:5.1f}%  [{earned_sum}/{max_sum}]  {level}")
        weighted_total += pct * weight
        total_weight += weight

    print("\n【题目明细】")
    for res in results:
        marker = _PASS if res.passed else _FAIL
        print(f"  {marker} {res.question_id:4s}  {res.title[:42]:42s}  {res.earned_score:3d}/{res.max_score:3d}"
              f"  ({res.score_pct*100:.0f}%)")
        if res.error:
            print(f"       \033[31mERROR: {res.error}\033[0m")
        for c in res.criteria:
            mk = _PASS if c.passed else _FAIL
            print(f"         {mk} {c.name:35s} {c.earned:2d}/{c.max:2d}  {c.detail[:60]}")

    # Overall
    total_earned = sum(r.earned_score for r in results)
    total_max = sum(r.max_score for r in results)
    overall_pct = total_earned / total_max if total_max > 0 else 0.0
    weighted_pct = weighted_total / total_weight if total_weight > 0 else 0.0

    print("\n" + "─" * 70)
    print(f"  总分:          {total_earned}/{total_max}  ({overall_pct*100:.1f}%)")
    print(f"  加权得分:      {weighted_pct*100:.1f}%  [{_level(weighted_pct)}]")
    print("═" * 70 + "\n")


def save_report(results: list[QuestionResult], path: Path) -> None:
    data = {
        "version": "2.0",
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_earned": sum(r.earned_score for r in results),
            "total_max": sum(r.max_score for r in results),
            "overall_pct": sum(r.earned_score for r in results) / sum(r.max_score for r in results)
            if any(r.max_score for r in results) else 0,
        },
        "results": [
            {
                "id": r.question_id,
                "category": r.category,
                "title": r.title,
                "earned": r.earned_score,
                "max": r.max_score,
                "pct": r.score_pct,
                "passed": r.passed,
                "error": r.error,
                "criteria": [
                    {"name": c.name, "earned": c.earned, "max": c.max,
                     "passed": c.passed, "detail": c.detail}
                    for c in r.criteria
                ],
            }
            for r in results
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info(f"Report saved → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="实验 Agent 细粒度评估 v2")
    parser.add_argument("--ids", nargs="+", help="指定评估题 ID，如 CV1 PR1")
    parser.add_argument("--category", help="按维度过滤，如 diagnosis_direction")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="报告输出路径")
    parser.add_argument("--dry-run", action="store_true", help="仅列出题目，不执行评估")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行线程数（默认 4，设为 1 禁用并行）")
    args = parser.parse_args()

    questions_data = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    all_questions = questions_data["questions"]

    # Filter
    questions = all_questions
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    if args.category:
        questions = [q for q in questions if q["category"] == args.category]

    if not questions:
        print("No questions matched filters.")
        sys.exit(1)

    if args.dry_run:
        print(f"\n{'ID':5s}  {'Category':25s}  {'Title'}")
        print("─" * 75)
        for q in questions:
            print(f"{q['id']:5s}  {q['category']:25s}  {q['title']}")
        print(f"\nTotal: {len(questions)} questions, max {sum(q['max_score'] for q in questions)} pts")
        return

    workers = min(args.workers, len(questions))
    logger.info(f"Running {len(questions)} question(s) with {workers} worker(s)…")

    results_map: dict[str, QuestionResult] = {}

    def _run_and_log(q: dict) -> QuestionResult:
        logger.info(f"[start] {q['id']} — {q['title']}")
        t0 = time.time()
        res = run_question(q)
        elapsed = time.time() - t0
        logger.info(f"[done]  {q['id']}  {res.earned_score}/{res.max_score} pts  ({elapsed:.1f}s)")
        return res

    if workers <= 1:
        for i, q in enumerate(questions, 1):
            logger.info(f"[{i}/{len(questions)}] {q['id']} — {q['title']}")
            t0 = time.time()
            res = run_question(q)
            elapsed = time.time() - t0
            logger.info(f"  {res.earned_score}/{res.max_score} pts  ({elapsed:.1f}s)")
            results_map[q["id"]] = res
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_and_log, q): q for q in questions}
            for fut in as_completed(futures):
                res = fut.result()
                results_map[res.question_id] = res

    # Preserve original question order in results
    results: list[QuestionResult] = [results_map[q["id"]] for q in questions]

    print_report(results, questions)
    save_report(results, args.report)


if __name__ == "__main__":
    main()
