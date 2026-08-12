"""Experiment Skills — 实验 Agent 工具集，供规划 Agent 和 MCP 调用。

注册的工具（3 个）：
  design_experiment      — 目标→文献检索→配方推荐→属性预测→步骤生成→安全检查
  check_safety           — 对任意配方执行安全审查，返回 approved / needs_review / blocked
  log_experiment_result  — 上传实测结果，生成偏差分析报告，将实验数据写回知识库
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.tools.base import BaseTool, ToolResult
from app.tools.registry import ToolRegistry


# ─────────────────────────────────────────────────────────────────────────────
# 1. design_experiment
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class DesignExperimentTool(BaseTool):
    name = "design_experiment"
    description = (
        "Design a complete experimental protocol for energetic materials research. "
        "Given a natural language research goal (e.g., 'design a propellant with '  "
        "burning rate > 15 mm/s and low sensitivity'), retrieves relevant literature, "
        "recommends formulations, predicts properties, generates step-by-step protocol, "
        "and runs a safety check. Returns 1-3 candidate protocols ranked by suitability."
    )
    parameters = {
        "goal": {
            "type": "string",
            "description": "Research goal in natural language (Chinese or English).",
        },
        "namespace": {
            "type": "string",
            "description": "Document namespace to search for literature.",
            "default": "default",
        },
        "num_candidates": {
            "type": "integer",
            "description": "Number of candidate formulations to generate (1-3).",
            "default": 3,
        },
    }
    required = ["goal"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, goal: str, namespace: str = "default", num_candidates: int = 3, **kwargs) -> ToolResult:
        try:
            from app.services.experiment_design import ExperimentDesignService
            svc = ExperimentDesignService(self.db)
            exp = svc.design(goal, namespace, min(max(num_candidates, 1), 3))

            protocols = []
            for proto in exp.protocols:
                protocols.append({
                    "protocol_id": proto.id,
                    "rank": proto.rank,
                    "formulation": json.loads(proto.formulation_json or "{}"),
                    "predicted_properties": json.loads(proto.predicted_properties_json or "{}"),
                    "steps": json.loads(proto.steps_json or "[]"),
                    "required_instruments": json.loads(proto.required_instruments_json or "[]"),
                    "rationale": proto.rationale,
                    "safety_status": proto.safety_status,
                    "safety_summary": json.loads(proto.safety_report_json or "{}").get("summary", ""),
                })

            return ToolResult(success=True, data={
                "experiment_id": exp.id,
                "status": exp.status,
                "goal": exp.goal,
                "target_properties": json.loads(exp.target_properties_json or "{}"),
                "protocols": protocols,
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 2. check_safety
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class CheckSafetyTool(BaseTool):
    name = "check_safety"
    description = (
        "Run a safety check on a proposed energetic material formulation. "
        "Checks component compatibility, sensitivity, temperature limits, and "
        "overall hazard level. Returns approved / needs_review / blocked status "
        "with specific issues and recommendations. "
        "Always call this before executing any experiment."
    )
    parameters = {
        "formulation": {
            "type": "object",
            "description": (
                'Formulation dict mapping component names to their properties. '
                'Example: {"AP": {"fraction": 0.68, "role": "oxidizer"}, '
                '"HTPB": {"fraction": 0.20, "role": "binder"}}'
            ),
        },
        "steps": {
            "type": "array",
            "description": "Optional list of protocol steps (each with 'step', 'temperature', etc.).",
        },
        "namespace": {
            "type": "string",
            "description": "Namespace for knowledge base sensitivity lookups.",
            "default": "default",
        },
    }
    required = ["formulation"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, formulation: dict, steps: list | None = None, namespace: str = "default", **kwargs) -> ToolResult:
        try:
            from app.services.safety_checker import SafetyCheckerService
            svc = SafetyCheckerService(self.db)
            report = svc.check(formulation, steps or [], namespace)
            return ToolResult(success=True, data=report.to_dict())
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 3. log_experiment_result
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class LogExperimentResultTool(BaseTool):
    name = "log_experiment_result"
    description = (
        "Upload measured experimental results for a planned experiment. "
        "Compares measured values against predicted values, generates an analysis report "
        "highlighting deviations and next-step recommendations, and writes the verified "
        "data back to the knowledge base (tagged as experimental, higher confidence than literature). "
        "Use after physically running an experiment designed by design_experiment."
    )
    parameters = {
        "experiment_id": {
            "type": "string",
            "description": "Experiment ID returned by design_experiment.",
        },
        "measured_properties": {
            "type": "object",
            "description": (
                'Measured property values. Example: '
                '{"burning_rate": {"value": 14.1, "unit": "mm/s"}, '
                '"density": {"value": 1.71, "unit": "g/cm3"}}'
            ),
        },
        "protocol_id": {
            "type": "string",
            "description": "Protocol ID that was followed (optional; defaults to rank-0 protocol).",
        },
        "raw_data_notes": {
            "type": "string",
            "description": "Free-text notes about raw data, instrument settings, or anomalies.",
            "default": "",
        },
        "write_to_kb": {
            "type": "boolean",
            "description": "Whether to write verified results back to the knowledge base.",
            "default": True,
        },
    }
    required = ["experiment_id", "measured_properties"]

    def __init__(self, db: Session):
        self.db = db

    def run(
        self,
        experiment_id: str,
        measured_properties: dict,
        protocol_id: str | None = None,
        raw_data_notes: str = "",
        write_to_kb: bool = True,
        **kwargs,
    ) -> ToolResult:
        try:
            from app.services.experiment_analysis import ExperimentAnalysisService
            svc = ExperimentAnalysisService(self.db)
            result = svc.analyze(
                experiment_id=experiment_id,
                measured_properties=measured_properties,
                protocol_id=protocol_id,
                raw_data_notes=raw_data_notes,
                write_to_kb=write_to_kb,
            )
            return ToolResult(success=True, data={
                "result_id": result.id,
                "analysis_report": result.analysis_report,
                "deviations": json.loads(result.deviation_json or "{}"),
                "written_to_kb": result.written_to_kb,
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))
