"""SafetyCheckerService — 含能材料实验方案安全审查。

设计原则：
  核心判断由**规则引擎**完成，不依赖 LLM——LLM 可被 prompt 影响，规则不会。
  LLM 仅用于生成人类可读的风险说明文字。

审查维度：
  1. 组分相容性   — 已知危险反应对（如 AP + 含 S 化合物）
  2. 操作温度     — 各组分在指定温度下的稳定性
  3. 感度危险等级 — 从知识库查询各组分感度数据
  4. 氧平衡检查   — 高氧平衡配方的额外风险
  5. 总体危险等级 — LOW / MEDIUM / HIGH / EXTREME

输出 safety_status：
  approved      — 无明显风险，可执行
  needs_review  — 存在中等风险，建议人工确认
  blocked       — 存在已知严重风险，必须修改方案
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from loguru import logger
from sqlalchemy.orm import Session

from app.models.orm.domain import DomainEntity, PropertyValue


# ─────────────────────────────────────────────────────────────────────────────
# 已知危险组合规则（可扩展）
# ─────────────────────────────────────────────────────────────────────────────

# (成分A, 成分B) → (风险描述, 建议)
_INCOMPATIBLE_PAIRS: dict[tuple[str, str], tuple[str, str]] = {
    ("AP", "sulfur"): (
        "AP 与硫磺接触可能发生剧烈反应，生成 SO₂ 并引发爆燃。",
        "移除硫磺组分，改用相容替代燃料：金属镁粉（Mg）或铝粉（Al）是 AP 推进剂中常用的安全替代品。",
    ),
    ("AP", "organic_sulfide"): (
        "AP 与有机硫化物相容性差，存在自燃风险。",
        "移除含硫有机物，使用不含硫的燃料替代品（如 HTPB、聚醚等）。",
    ),
    ("RDX", "acid"): (
        "RDX 在强酸环境下会水解降解，产生不稳定中间体。",
        "控制配方 pH 中性，避免酸性添加剂；需要酸处理时先单独进行再混合。",
    ),
    ("NG", "NG"): (
        "纯 NG 对撞击和振动极为敏感，必须用脱敏剂稀释。",
        "使用硅藻土或 NC/NG 混合物（双基推进剂）降低 NG 感度。",
    ),
    ("TATP", "any"): (
        "TATP 不稳定，挥发性高，不推荐任何工艺操作。",
        "禁止使用 TATP；改用 PETN 或 RDX 等经过认证的炸药替代。",
    ),
    ("HMTD", "any"): (
        "HMTD 对热、摩擦、撞击高度敏感，禁止使用。",
        "禁止使用 HMTD；改用经过认证的起爆药替代品。",
    ),
}

# 高感度材料列表（撞击感度 < 4J 或摩擦感度 < 20N）
_HIGH_SENSITIVITY_MATERIALS = {
    "TATP", "HMTD", "ETN", "NG", "EGDN", "NGL",
}

# 高爆速/高能量密度材料（需要额外安全措施）
_HIGH_ENERGY_MATERIALS = {
    "RDX", "HMX", "CL-20", "PETN", "TNT", "TATB", "TKX-50",
    "FOX-7", "NTO", "DNTF", "ADN",
}


@dataclass
class SafetyIssue:
    severity: str          # LOW / MEDIUM / HIGH / CRITICAL
    category: str          # compatibility / temperature / sensitivity / oxygen_balance
    description: str
    affected_components: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class SafetyReport:
    overall_level: str                         # LOW / MEDIUM / HIGH / EXTREME
    status: str                                # approved / needs_review / blocked
    issues: list[SafetyIssue] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "overall_level": self.overall_level,
            "status": self.status,
            "summary": self.summary,
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "description": i.description,
                    "affected_components": i.affected_components,
                    "recommendation": i.recommendation,
                }
                for i in self.issues
            ],
        }


class SafetyCheckerService:
    """对实验方案（配方 + 步骤）执行安全审查，返回 SafetyReport。"""

    def __init__(self, db: Session):
        self.db = db

    def check(
        self,
        formulation: dict[str, dict],   # {"AP": {"fraction": 0.68, "role": "oxidizer"}, ...}
        steps: list[dict] | None = None,
        namespace: str = "default",
    ) -> SafetyReport:
        """主入口：执行全部安全检查，返回 SafetyReport。"""
        issues: list[SafetyIssue] = []
        components = list(formulation.keys())

        issues += self._check_incompatible_pairs(components)
        issues += self._check_high_sensitivity(components)
        issues += self._check_temperature(steps or [], components)
        issues += self._check_sensitivity_from_kb(components, namespace)
        issues += self._check_high_energy_density(components, formulation)

        overall = self._compute_overall(issues)
        status = self._compute_status(overall, issues)
        summary = self._build_summary(components, issues, overall)

        report = SafetyReport(
            overall_level=overall,
            status=status,
            issues=issues,
            summary=summary,
        )
        logger.info(
            f"Safety check: {len(components)} components → "
            f"level={overall}, status={status}, issues={len(issues)}"
        )
        return report

    # ── 规则检查 ──────────────────────────────────────────────────────────────

    def _check_incompatible_pairs(self, components: list[str]) -> list[SafetyIssue]:
        issues = []
        comp_set = {c.upper() for c in components}
        for (a, b), (desc, recommendation) in _INCOMPATIBLE_PAIRS.items():
            a_match = any(c == a.upper() or a.upper() in c for c in comp_set)
            b_match = b == "any" or any(c == b.upper() or b.upper() in c for c in comp_set)
            if a_match and b_match:
                issues.append(SafetyIssue(
                    severity="CRITICAL" if b == "any" else "HIGH",
                    category="compatibility",
                    description=desc,
                    affected_components=[a, b],
                    recommendation=recommendation,
                ))
        return issues

    def _check_high_sensitivity(self, components: list[str]) -> list[SafetyIssue]:
        issues = []
        for comp in components:
            if comp.upper() in {m.upper() for m in _HIGH_SENSITIVITY_MATERIALS}:
                issues.append(SafetyIssue(
                    severity="HIGH",
                    category="sensitivity",
                    description=f"{comp} 对撞击/摩擦/静电高度敏感，操作需在防爆区进行并做好静电防护。",
                    affected_components=[comp],
                    recommendation="确保操作区域接地良好，禁止金属工具直接接触，控制操作量 < 10g。",
                ))
        return issues

    def _check_temperature(self, steps: list[dict], components: list[str]) -> list[SafetyIssue]:
        """检查步骤中温度参数是否超出安全范围。"""
        issues = []
        # 简化规则：含 NG/EGDN 时固化温度不超过 50°C
        has_liquid_explosive = any(
            c.upper() in {"NG", "EGDN", "NGL"} for c in components
        )
        for step in steps:
            temp = step.get("temperature")
            if temp is None:
                continue
            if has_liquid_explosive and temp > 50:
                issues.append(SafetyIssue(
                    severity="HIGH",
                    category="temperature",
                    description=f"步骤 {step.get('step', '?')} 温度 {temp}°C 过高：含液态炸药时建议 ≤ 50°C。",
                    affected_components=[c for c in components if c.upper() in {"NG", "EGDN"}],
                    recommendation="降低操作温度或更换热稳定性更好的增塑剂。",
                ))
            if temp > 150:
                issues.append(SafetyIssue(
                    severity="MEDIUM",
                    category="temperature",
                    description=f"步骤 {step.get('step', '?')} 温度 {temp}°C 较高，建议确认所有组分在此温度下的热稳定性。",
                    affected_components=components,
                    recommendation="查阅各组分 DSC 数据，确认分解温度 > 操作温度 + 50°C。",
                ))
        return issues

    def _check_sensitivity_from_kb(self, components: list[str], namespace: str) -> list[SafetyIssue]:
        """从知识库查询各组分感度数据，对高感度组分发出警告。"""
        issues = []
        for comp in components:
            entity = self.db.query(DomainEntity).filter_by(canonical_name=comp).first()
            if not entity:
                continue
            # 查询撞击感度
            impact_props = (
                self.db.query(PropertyValue)
                .filter_by(entity_id=entity.id, property_name="impact_sensitivity")
                .all()
            )
            for pv in impact_props:
                if pv.value_numeric is not None and pv.value_numeric < 4.0:
                    issues.append(SafetyIssue(
                        severity="MEDIUM",
                        category="sensitivity",
                        description=(
                            f"知识库记录 {comp} 撞击感度 = {pv.value_numeric} J（< 4J 高危阈值），"
                            "操作时注意防撞。"
                        ),
                        affected_components=[comp],
                        recommendation="使用防爆操作规程，限制单次操作量。",
                    ))
                    break
        return issues

    def _check_high_energy_density(
        self, components: list[str], formulation: dict
    ) -> list[SafetyIssue]:
        """高能成分质量分数 > 80% 时发出提醒。"""
        issues = []
        high_energy_fraction = sum(
            formulation[c].get("fraction", 0)
            for c in components
            if c.upper() in {m.upper() for m in _HIGH_ENERGY_MATERIALS}
        )
        if high_energy_fraction > 0.8:
            issues.append(SafetyIssue(
                severity="MEDIUM",
                category="oxygen_balance",
                description=f"高能成分总质量分数 {high_energy_fraction*100:.1f}% > 80%，配方整体感度偏高。",
                affected_components=[
                    c for c in components if c.upper() in {m.upper() for m in _HIGH_ENERGY_MATERIALS}
                ],
                recommendation="考虑增加钝感剂或降低高能成分比例至 ≤ 75%。",
            ))
        return issues

    # ── 汇总 ──────────────────────────────────────────────────────────────────

    def _compute_overall(self, issues: list[SafetyIssue]) -> str:
        if any(i.severity == "CRITICAL" for i in issues):
            return "EXTREME"
        if any(i.severity == "HIGH" for i in issues):
            return "HIGH"
        if any(i.severity == "MEDIUM" for i in issues):
            return "MEDIUM"
        return "LOW"

    def _compute_status(self, overall: str, issues: list[SafetyIssue]) -> str:
        if overall == "EXTREME":
            return "blocked"
        if overall == "HIGH":
            return "needs_review"
        return "approved"

    def _build_summary(
        self, components: list[str], issues: list[SafetyIssue], overall: str
    ) -> str:
        comp_str = "、".join(components)
        issue_count = len(issues)
        if overall == "LOW":
            return f"配方（{comp_str}）安全性良好，未发现明显风险，可进入实验阶段。"
        if overall == "MEDIUM":
            return (
                f"配方（{comp_str}）存在 {issue_count} 个中等风险项，"
                "建议研究员确认后再执行。"
            )
        if overall == "HIGH":
            return (
                f"配方（{comp_str}）存在 {issue_count} 个高风险项，"
                "必须经过人工安全审查后方可执行。"
            )
        return (
            f"配方（{comp_str}）存在已知严重风险（CRITICAL），"
            "当前方案已被阻断，请修改配方后重新提交。"
        )
