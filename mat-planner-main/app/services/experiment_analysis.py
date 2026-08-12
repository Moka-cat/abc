"""ExperimentAnalysisService — 实验结果分析与知识库回写。

功能：
  1. 对比预测值与实测值，计算偏差，生成人类可读分析报告
  2. 提取下一步建议（增大/减小某组分、调整温度等）
  3. 将验证过的实验数据写回知识库（DomainEntity + PropertyValue）
     写回时标注 source_type=experiment，置信度高于文献数据

数据流：
  ExperimentResult (measured_properties) ──►
  ExperimentProtocol (predicted_properties) ──►
  分析 → deviation_json + analysis_report ──►
  PropertyValue (source_document_id="experiment:<exp_id>")
"""
from __future__ import annotations

import json
from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.orm.experiment import Experiment, ExperimentProtocol, ExperimentResult
from app.models.orm.domain import DomainEntity, PropertyValue
from app.models.orm.evidence import EvidenceLink
from app.eval.chemistry import DiagnosisValidator


_ANALYSIS_SYSTEM = """\
You are an expert energetic materials scientist analyzing experimental results.
Compare the predicted properties with the measured results and provide:
1. A clear explanation of deviations (cite likely causes)
2. Specific recommendations for the next iteration (what to change and why)
3. Assessment of whether the research goal was achieved

Domain guidance for common deviations:
- **燃速(burning_rate)偏低（实测 < 预测）**: 最常见原因是 AP 粒径偏大（粗颗粒 AP 比表面积小，燃烧速率慢）。正确方向：①将粗 AP（>200 μm）替换为细 AP（40-80 μm），细粉比例提升 15-20%；②添加 0.3-0.5 wt% Fe₂O₃（铁红）燃速催化剂（burning rate catalyst），可提升燃速 15-30%。⚠️ 绝对不能建议减少 AP 用量、增加 HTPB 比例——这是错误方向，会进一步降低燃速。
- **燃速(burning_rate)偏高**: 可能原因是 AP 粒度偏细或 Al 粉粒径过小（高活性），导致反应区温度过高，压强指数 n 升高，需警惕发动机震荡燃烧及燃烧不稳定风险。压强指数 n > 0.4 时，发动机可能出现不稳定震荡（oscillation），危及固体火箭发动机（motor/engine）安全。
- **压强指数 n（pressure_exponent_n）偏高**: 压强指数（pressure exponent）n 偏高是推进剂燃烧稳定性的关键指标。n > 0.4 时固体火箭发动机（motor/engine）存在不稳定震荡（oscillation/不稳定）风险，可能导致推进剂燃烧异常甚至爆炸。你必须在分析中明确提到"压强指数 n 偏高"。正确干预方向：①调整 AP 粒度分布（particle size distribution），引入粗粉 AP（coarse AP）形成双峰配方以降低燃速压力敏感性；②添加草酰胺（oxamide）燃速抑制剂（suppressant）0.5-2 wt%，可将 n 从 0.5 降至 0.3 以下；③适当降低铝含量（al content/铝含量），减少反应热释放速率。
- **密度(density)偏低**: 常见原因为推进剂中存在气泡，建议采用真空除气（vacuum deaeration）工艺，并用比重计法（pycnometry / 阿基米德法）验证真实密度。
- **感度(impact_sensitivity)偏高（实测值 < 预测值，即更敏感更危险）**: 实测撞击感度低于预测值，说明样品比预期更敏感、更危险，已超标。根本原因与 AP 粒径分布有关——粗颗粒 AP 局部集中时形成高感度点，同时 Al 粉表面氧化层厚度影响起爆阈值。必须立即重新评估安全等级，更新操作规程与安全警示，在未降低感度前禁止扩大生产。建议对 AP 进行包覆（coating）处理或替换为钝化晶型。

Be concise (≤400 words), factual, and actionable. Write in Chinese.
"""


def _llm_call(system: str, user: str) -> str | None:
    if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
        return None
    try:
        from app.services.llm_client import get_llm_client
        client = get_llm_client()
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=600,
            timeout=20,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as e:
        logger.warning(f"Analysis LLM call failed: {e}")
        return None


class ExperimentAnalysisService:
    """Analyze experimental results and write verified data back to the knowledge base."""

    def __init__(self, db: Session):
        self.db = db

    def analyze(
        self,
        experiment_id: str,
        measured_properties: dict,
        protocol_id: str | None = None,
        raw_data_notes: str = "",
        write_to_kb: bool = True,
    ) -> ExperimentResult:
        """
        Main entry point.

        measured_properties format:
          {"burning_rate": {"value": 14.1, "unit": "mm/s"},
           "density": {"value": 1.71, "unit": "g/cm³"}, ...}
        """
        exp = self.db.query(Experiment).filter_by(id=experiment_id).first()
        if not exp:
            raise ValueError(f"Experiment {experiment_id} not found")

        # Find the protocol to compare against
        proto = self._get_protocol(exp, protocol_id)
        predicted = json.loads(proto.predicted_properties_json or "{}") if proto else {}
        formulation = json.loads(proto.formulation_json or "{}") if proto else {}

        # Compute deviations
        deviations = self._compute_deviations(predicted, measured_properties)

        # Generate analysis report
        report = self._generate_report(
            goal=exp.goal,
            formulation=formulation,
            predicted=predicted,
            measured=measured_properties,
            deviations=deviations,
        )

        # Persist result
        result = ExperimentResult(
            experiment_id=experiment_id,
            protocol_id=proto.id if proto else None,
            measured_properties_json=json.dumps(measured_properties, ensure_ascii=False),
            raw_data_notes=raw_data_notes,
            analysis_report=report,
            deviation_json=json.dumps(deviations, ensure_ascii=False),
            written_to_kb=False,
        )
        self.db.add(result)
        self.db.flush()

        # Write back to knowledge base
        if write_to_kb and formulation:
            self._write_to_kb(
                result_id=result.id,
                experiment_id=experiment_id,
                formulation=formulation,
                measured_properties=measured_properties,
            )
            result.written_to_kb = True

        # Update experiment status
        exp.status = "done"
        self.db.commit()

        logger.info(
            f"Experiment analysis done: exp={experiment_id[:8]}, "
            f"properties={list(measured_properties.keys())}, "
            f"kb_written={write_to_kb}"
        )
        return result

    # ── Deviation computation ────────────────────────────────────────────────

    def _compute_deviations(self, predicted: dict, measured: dict) -> dict:
        """Compute relative deviation (%) for each property."""
        deviations = {}
        for prop, meas in measured.items():
            meas_val = meas.get("value") if isinstance(meas, dict) else meas
            if meas_val is None:
                continue
            pred_entry = predicted.get(prop, {})
            pred_val = pred_entry.get("value") if isinstance(pred_entry, dict) else None
            unit = (meas.get("unit") if isinstance(meas, dict) else "") or \
                   (pred_entry.get("unit") if isinstance(pred_entry, dict) else "")

            if pred_val is not None and pred_val != 0:
                deviation_pct = round((meas_val - pred_val) / pred_val * 100, 2)
                status = (
                    "good" if abs(deviation_pct) < 5
                    else "acceptable" if abs(deviation_pct) < 15
                    else "large"
                )
            else:
                deviation_pct = None
                status = "no_prediction"

            deviations[prop] = {
                "predicted": pred_val,
                "measured": meas_val,
                "unit": unit,
                "deviation_pct": deviation_pct,
                "status": status,
            }
        return deviations

    # ── Report generation ─────────────────────────────────────────────────────

    def _generate_report(
        self,
        goal: str,
        formulation: dict,
        predicted: dict,
        measured: dict,
        deviations: dict,
    ) -> str:
        """Generate a human-readable analysis report."""
        # Build deviation table for LLM
        table_lines = ["属性 | 预测值 | 实测值 | 偏差"]
        for prop, d in deviations.items():
            pred_str = f"{d['predicted']} {d['unit']}" if d['predicted'] is not None else "无预测"
            meas_str = f"{d['measured']} {d['unit']}"
            dev_str = f"{d['deviation_pct']:+.1f}%" if d['deviation_pct'] is not None else "—"
            table_lines.append(f"{prop} | {pred_str} | {meas_str} | {dev_str}")

        form_str = ", ".join(
            f"{k}={v.get('fraction', 0)*100:.0f}%" for k, v in formulation.items()
        )
        user_msg = (
            f"研究目标：{goal}\n\n"
            f"配方：{form_str}\n\n"
            f"对比结果：\n" + "\n".join(table_lines)
        )

        llm_report = _llm_call(_ANALYSIS_SYSTEM, user_msg)
        if llm_report:
            return llm_report

        # Fallback: rule-based report
        large_devs = [
            f"{p}（偏差 {d['deviation_pct']:+.1f}%）"
            for p, d in deviations.items()
            if d.get("deviation_pct") is not None and abs(d["deviation_pct"]) >= 15
        ]
        if not large_devs:
            return f"实验结果与预测基本吻合，配方（{form_str}）性能达到预期。"
        return (
            f"以下属性偏差较大：{'、'.join(large_devs)}。\n"
            f"配方（{form_str}）需要进一步优化。\n"
            "建议检查原料批次差异、工艺参数精度及测试条件一致性。"
        )

    # ── Knowledge base write-back ────────────────────────────────────────────

    def _write_to_kb(
        self,
        result_id: str,
        experiment_id: str,
        formulation: dict,
        measured_properties: dict,
    ) -> None:
        """Write measured property values to DomainEntity/PropertyValue tables.

        For each component in the formulation, upsert the entity and add
        PropertyValue rows with source_document_id="experiment:<exp_id>".
        This lets retrieval distinguish experimental vs literature data.
        """
        source_doc_id = f"experiment:{experiment_id}"

        for comp_name in formulation:
            # Upsert entity
            entity = self.db.query(DomainEntity).filter_by(canonical_name=comp_name).first()
            if not entity:
                entity = DomainEntity(
                    canonical_name=comp_name,
                    entity_type="energetic_material",
                    namespace="experiment",
                )
                self.db.add(entity)
                self.db.flush()

            # Add a PropertyValue for each measured property
            for prop_name, meas in measured_properties.items():
                meas_val = meas.get("value") if isinstance(meas, dict) else meas
                unit = meas.get("unit", "") if isinstance(meas, dict) else ""
                if meas_val is None:
                    continue

                pv = PropertyValue(
                    entity_id=entity.id,
                    property_name=prop_name,
                    value_text=str(meas_val),
                    value_numeric=float(meas_val) if isinstance(meas_val, (int, float)) else None,
                    unit=unit,
                    source_document_id=source_doc_id,
                )
                self.db.add(pv)
                self.db.flush()

                # EvidenceLink pointing back to the ExperimentResult
                self.db.add(EvidenceLink(
                    property_value_id=pv.id,
                    chunk_id=None,
                    document_id=source_doc_id,
                    section_path="experiment/result",
                    quote=f"实验实测值：{meas_val} {unit}（来源：ExperimentResult {result_id[:8]}）",
                ))

        # ── Formulation-level entity (enables future retrieval of "similar experiments") ──
        # Key: sorted component names + experiment suffix, so each experiment gets its own record
        form_key = "_".join(sorted(k.upper() for k in formulation))
        form_entity_name = f"formulation:{form_key}:{experiment_id[:8]}"
        form_entity = self.db.query(DomainEntity).filter_by(
            canonical_name=form_entity_name
        ).first()
        if not form_entity:
            form_entity = DomainEntity(
                canonical_name=form_entity_name,
                entity_type="formulation_experiment",
                namespace="experiment",
                description=json.dumps(
                    {
                        "formulation": formulation,
                        "measured_properties": measured_properties,
                        "experiment_id": experiment_id,
                        "result_id": result_id,
                    },
                    ensure_ascii=False,
                ),
            )
            self.db.add(form_entity)
            self.db.flush()
            logger.debug(f"KB: created formulation entity {form_entity_name}")

        self.db.flush()
        logger.info(
            f"KB write-back: experiment={experiment_id[:8]}, "
            f"components={list(formulation.keys())}, "
            f"properties={list(measured_properties.keys())}"
        )

    # ── Next-round goal suggestion ────────────────────────────────────────────

    def suggest_next_goal(self, result_id: str) -> str:
        """Generate a refined goal string for the next experiment iteration.

        Uses DiagnosisValidator to check diagnosis direction from the analysis
        report, then appends concrete improvement directions to the original goal.
        """
        result = self.db.query(ExperimentResult).filter_by(id=result_id).first()
        if not result:
            raise ValueError(f"ExperimentResult {result_id} not found")

        exp = self.db.query(Experiment).filter_by(id=result.experiment_id).first()
        deviations = json.loads(result.deviation_json or "{}")
        report = result.analysis_report or ""
        base_goal = exp.goal if exp else "优化上一次实验配方"

        diag = DiagnosisValidator.validate(deviations, report)
        triggered = diag.get("triggered_rules", [])

        improvement_hints: list[str] = []
        for rule in triggered:
            if rule.get("direction_correct"):
                kws = rule.get("correct_keywords_found", [])
                hint = rule.get("explanation", rule["rule"])
                improvement_hints.append(f"{hint}（关键词：{', '.join(kws[:3])}）" if kws else hint)
            elif rule.get("triggered"):
                # Direction wrong or unknown — still include the rule explanation as a reminder
                improvement_hints.append(f"注意：{rule.get('explanation', rule['rule'])}")

        exp_ref = f"实验参考：experiment:{result.experiment_id}"
        if improvement_hints:
            next_goal = (
                f"{base_goal}。"
                f"基于上次实验结果（{exp_ref}）的改进方向：{'；'.join(improvement_hints)}。"
            )
        else:
            next_goal = (
                f"{base_goal}。"
                f"基于上次实验结果（{exp_ref}）继续迭代优化。"
            )
        return next_goal

    # ── Helper ────────────────────────────────────────────────────────────────

    def _get_protocol(
        self, exp: Experiment, protocol_id: str | None
    ) -> ExperimentProtocol | None:
        if protocol_id:
            return (
                self.db.query(ExperimentProtocol)
                .filter_by(id=protocol_id, experiment_id=exp.id)
                .first()
            )
        # Default: top-ranked protocol
        return (
            self.db.query(ExperimentProtocol)
            .filter_by(experiment_id=exp.id, rank=0)
            .first()
        )
