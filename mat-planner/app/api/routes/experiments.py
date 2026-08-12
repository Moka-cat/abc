"""Experiment API routes.

POST /experiments                    提交研究目标，触发实验设计（异步后台）
GET  /experiments                    列出所有实验（支持 namespace 过滤）
GET  /experiments/{id}               获取实验详情（含所有候选方案）
GET  /experiments/{id}/protocols     列出候选方案
GET  /experiments/{id}/protocols/{pid}  获取单个方案（含完整步骤）
POST /experiments/{id}/results       上传实测结果，触发分析
GET  /experiments/{id}/results       查看分析报告
"""
import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.orm.experiment import Experiment, ExperimentProtocol, ExperimentResult

router = APIRouter()


# ── Request / Response schemas ────────────────────────────────────────────────

class DesignRequest(BaseModel):
    goal: str = Field(..., description="研究目标（自然语言）")
    namespace: str = Field("default", description="文献命名空间")
    num_candidates: int = Field(3, ge=1, le=3, description="候选方案数量")


class ResultUploadRequest(BaseModel):
    measured_properties: dict = Field(
        ...,
        description='实测属性，例如 {"burning_rate": {"value": 14.1, "unit": "mm/s"}}',
    )
    protocol_id: Optional[str] = Field(None, description="对应方案 ID（默认取 rank=0 方案）")
    raw_data_notes: str = Field("", description="原始数据备注")
    write_to_kb: bool = Field(True, description="是否写回知识库")


# ── Background workers ────────────────────────────────────────────────────────

def _run_design(experiment_id: str, goal: str, namespace: str, num_candidates: int) -> None:
    """Background: run ExperimentDesignService and update the DB record."""
    db: Session = SessionLocal()
    try:
        from app.services.experiment_design import ExperimentDesignService
        # The service creates its own Experiment record — we need to pass the pre-created id.
        # Simpler: just let the service create it; we return the id from the POST handler.
        # Here we only run design on pre-existing experiment records.
        svc = ExperimentDesignService(db)
        exp = db.query(Experiment).filter_by(id=experiment_id).first()
        if exp:
            svc.design(goal=exp.goal, namespace=exp.namespace, num_candidates=num_candidates)
    except Exception as e:
        db_exp = db.query(Experiment).filter_by(id=experiment_id).first()
        if db_exp:
            db_exp.status = "failed"
            db_exp.error_message = str(e)
            db.commit()
    finally:
        db.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", status_code=202)
async def submit_experiment(req: DesignRequest, background_tasks: BackgroundTasks):
    """提交研究目标，立即返回 experiment_id，后台异步执行实验设计。"""
    db = SessionLocal()
    try:
        exp = Experiment(
            goal=req.goal,
            namespace=req.namespace,
            status="pending",
        )
        db.add(exp)
        db.commit()
        db.refresh(exp)
        exp_id = exp.id
    finally:
        db.close()

    background_tasks.add_task(
        _run_design, exp_id, req.goal, req.namespace, req.num_candidates
    )
    return {
        "experiment_id": exp_id,
        "status": "pending",
        "message": "实验设计任务已提交，正在后台生成方案，请通过 GET /experiments/{id} 查询进度。",
    }


@router.get("")
def list_experiments(namespace: Optional[str] = None, limit: int = 20, offset: int = 0):
    """列出实验记录（支持 namespace 过滤）。"""
    db = SessionLocal()
    try:
        q = db.query(Experiment)
        if namespace:
            q = q.filter_by(namespace=namespace)
        total = q.count()
        exps = q.order_by(Experiment.created_at.desc()).offset(offset).limit(limit).all()
        return {
            "total": total,
            "experiments": [_exp_summary(e) for e in exps],
        }
    finally:
        db.close()


@router.get("/{experiment_id}")
def get_experiment(experiment_id: str):
    """获取实验详情（含所有候选方案摘要）。"""
    db = SessionLocal()
    try:
        exp = db.query(Experiment).filter_by(id=experiment_id).first()
        if not exp:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return {
            **_exp_summary(exp),
            "target_properties": _safe_json(exp.target_properties_json),
            "protocols": [_proto_summary(p) for p in exp.protocols],
            "results": [_result_summary(r) for r in exp.results],
        }
    finally:
        db.close()


@router.get("/{experiment_id}/protocols")
def list_protocols(experiment_id: str):
    """列出候选方案（按 rank 排序）。"""
    db = SessionLocal()
    try:
        exp = db.query(Experiment).filter_by(id=experiment_id).first()
        if not exp:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return {"protocols": [_proto_detail(p) for p in exp.protocols]}
    finally:
        db.close()


@router.get("/{experiment_id}/protocols/{protocol_id}")
def get_protocol(experiment_id: str, protocol_id: str):
    """获取单个方案详情（含完整步骤 + 安全报告）。"""
    db = SessionLocal()
    try:
        proto = (
            db.query(ExperimentProtocol)
            .filter_by(id=protocol_id, experiment_id=experiment_id)
            .first()
        )
        if not proto:
            raise HTTPException(status_code=404, detail="Protocol not found")
        return _proto_detail(proto)
    finally:
        db.close()


@router.post("/{experiment_id}/results", status_code=201)
def upload_result(experiment_id: str, req: ResultUploadRequest):
    """上传实测结果，同步执行分析（通常 < 5s）。"""
    db = SessionLocal()
    try:
        exp = db.query(Experiment).filter_by(id=experiment_id).first()
        if not exp:
            raise HTTPException(status_code=404, detail="Experiment not found")

        from app.services.experiment_analysis import ExperimentAnalysisService
        svc = ExperimentAnalysisService(db)
        result = svc.analyze(
            experiment_id=experiment_id,
            measured_properties=req.measured_properties,
            protocol_id=req.protocol_id,
            raw_data_notes=req.raw_data_notes,
            write_to_kb=req.write_to_kb,
        )
        return _result_detail(result)
    finally:
        db.close()


@router.get("/{experiment_id}/results")
def list_results(experiment_id: str):
    """查看该实验的所有分析报告。"""
    db = SessionLocal()
    try:
        exp = db.query(Experiment).filter_by(id=experiment_id).first()
        if not exp:
            raise HTTPException(status_code=404, detail="Experiment not found")
        return {"results": [_result_detail(r) for r in exp.results]}
    finally:
        db.close()


@router.post("/{experiment_id}/results/{result_id}/next-goal")
def suggest_next_goal(experiment_id: str, result_id: str):
    """基于本次分析报告，生成下一轮实验的优化目标（迭代闭环）。

    返回 next_goal 字符串，可直接作为新 POST /experiments 的 goal 字段使用。
    """
    db = SessionLocal()
    try:
        exp = db.query(Experiment).filter_by(id=experiment_id).first()
        if not exp:
            raise HTTPException(status_code=404, detail="Experiment not found")
        from app.services.experiment_analysis import ExperimentAnalysisService
        svc = ExperimentAnalysisService(db)
        next_goal = svc.suggest_next_goal(result_id)
        return {
            "experiment_id": experiment_id,
            "result_id": result_id,
            "next_goal": next_goal,
            "tip": "将 next_goal 作为 goal 字段提交到 POST /experiments 即可开始下一轮迭代",
        }
    finally:
        db.close()


# ── Serialization helpers ─────────────────────────────────────────────────────

def _safe_json(text: str | None):
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


def _exp_summary(exp: Experiment) -> dict:
    return {
        "experiment_id": exp.id,
        "goal": exp.goal,
        "namespace": exp.namespace,
        "status": exp.status,
        "error_message": exp.error_message,
        "protocol_count": len(exp.protocols),
        "result_count": len(exp.results),
        "created_at": exp.created_at.isoformat(),
        "updated_at": exp.updated_at.isoformat(),
    }


def _proto_summary(proto: ExperimentProtocol) -> dict:
    return {
        "protocol_id": proto.id,
        "rank": proto.rank,
        "safety_status": proto.safety_status,
        "safety_summary": _safe_json(proto.safety_report_json).get("summary", ""),
        "rationale": proto.rationale,
        "formulation": _safe_json(proto.formulation_json),
        "predicted_properties": _safe_json(proto.predicted_properties_json),
    }


def _proto_detail(proto: ExperimentProtocol) -> dict:
    return {
        **_proto_summary(proto),
        "steps": _safe_json(proto.steps_json) if proto.steps_json else [],
        "required_instruments": _safe_json(proto.required_instruments_json),
        "safety_report": _safe_json(proto.safety_report_json),
        "reference_chunk_ids": _safe_json(proto.reference_chunk_ids_json),
        "created_at": proto.created_at.isoformat(),
    }


def _result_summary(result: ExperimentResult) -> dict:
    return {
        "result_id": result.id,
        "protocol_id": result.protocol_id,
        "written_to_kb": result.written_to_kb,
        "created_at": result.created_at.isoformat(),
    }


def _result_detail(result: ExperimentResult) -> dict:
    return {
        **_result_summary(result),
        "measured_properties": _safe_json(result.measured_properties_json),
        "deviations": _safe_json(result.deviation_json),
        "analysis_report": result.analysis_report,
        "raw_data_notes": result.raw_data_notes,
    }
