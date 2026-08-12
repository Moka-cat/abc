"""Experiment ORM models — 自动化实验设计与结果记录。

三张表：
  Experiment       — 一次研究任务（目标 + 状态）
  ExperimentProtocol — AI 生成的实验方案（配方 + 步骤 + 预测属性 + 安全审查结果）
  ExperimentResult   — 研究员上传的实测结果（测量值 + 原始数据）
"""
import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Experiment(Base):
    """一次实验任务：从研究目标到实测结果的完整生命周期。"""
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    namespace: Mapped[str] = mapped_column(String(128), default="default")

    # 研究目标（自然语言）
    goal: Mapped[str] = mapped_column(Text)
    # 从目标中提取的目标属性（JSON 字符串）
    # e.g. {"burning_rate": {"min": 15, "unit": "mm/s"}, "sensitivity": "low"}
    target_properties_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 状态：pending / designing / safety_review / ready / running / done / failed
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    protocols: Mapped[list["ExperimentProtocol"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan",
        order_by="ExperimentProtocol.rank"
    )
    results: Mapped[list["ExperimentResult"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_experiments_namespace", "namespace"),
        Index("idx_experiments_status", "status"),
    )


class ExperimentProtocol(Base):
    """AI 生成的实验方案。

    一个 Experiment 可以有多个方案（候选配方），rank=0 是推荐方案。
    """
    __tablename__ = "experiment_protocols"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="CASCADE")
    )
    # 方案排序（0 = 最优推荐）
    rank: Mapped[int] = mapped_column(Integer, default=0)

    # 配方（JSON）: {"AP": {"fraction": 0.68, "role": "oxidizer"}, "HTPB": {...}, ...}
    formulation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 实验步骤（JSON list）
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 预测属性（JSON）: {"burning_rate": {"value": 15.3, "unit": "mm/s", "confidence": "medium"}, ...}
    predicted_properties_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 参考文献 chunk_id 列表（JSON list）
    reference_chunk_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 设计依据摘要（自然语言）
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 安全检查结果
    safety_status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending / approved / blocked / needs_review
    safety_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 所需仪器（JSON list of strings）
    required_instruments_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    experiment: Mapped["Experiment"] = relationship(back_populates="protocols")

    __table_args__ = (
        Index("idx_exp_proto_experiment", "experiment_id"),
    )


class ExperimentResult(Base):
    """研究员上传的实测结果，与对应方案关联。"""
    __tablename__ = "experiment_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id", ondelete="CASCADE")
    )
    protocol_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("experiment_protocols.id", ondelete="SET NULL"), nullable=True
    )

    # 实测属性（JSON）: {"burning_rate": {"value": 14.1, "unit": "mm/s"}, ...}
    measured_properties_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 原始数据备注或文件路径
    raw_data_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 分析报告（AI 生成，预测 vs 实测 对比）
    analysis_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 分析偏差摘要（JSON）
    deviation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 是否已写回知识库
    written_to_kb: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    experiment: Mapped["Experiment"] = relationship(back_populates="results")

    __table_args__ = (
        Index("idx_exp_result_experiment", "experiment_id"),
        Index("idx_exp_result_protocol", "protocol_id"),
    )
