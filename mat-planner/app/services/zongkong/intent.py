"""Intent classification — one LLM call routes the query to the right agent."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum

from app.core.config import settings
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    DATA       = "data"        # 快速属性/数值查询
    EXPERIMENT = "experiment"  # 实验设计 / 配方优化
    LITERATURE = "literature"  # 深度文献研究（全流水线）
    HYBRID     = "hybrid"      # 数据查询 + 实验设计（链式执行）
    MODIFY     = "modify"      # 修改上一次实验方案
    SAFETY     = "safety"      # 安全风险评估


# UI 元数据：label / icon / 颜色标识
AGENT_META: dict[IntentType, dict] = {
    IntentType.DATA: {
        "label": "数据查询 Agent",
        "icon": "📊",
        "color": "teal",
    },
    IntentType.EXPERIMENT: {
        "label": "实验设计 Agent",
        "icon": "🔬",
        "color": "violet",
    },
    IntentType.LITERATURE: {
        "label": "文献检索 Agent",
        "icon": "📚",
        "color": "blue",
    },
    IntentType.HYBRID: {
        "label": "数据 + 实验链式 Agent",
        "icon": "⚡",
        "color": "orange",
    },
    IntentType.MODIFY: {
        "label": "方案修改 Agent",
        "icon": "✏️",
        "color": "amber",
    },
    IntentType.SAFETY: {
        "label": "安全评估 Agent",
        "icon": "🛡️",
        "color": "red",
    },
}


@dataclass
class IntentResult:
    type:   IntentType
    entity: str   # 物质名（仅 DATA 类型，多个用 / 分隔）
    reason: str   # 路由原因（一句话）


_SYSTEM = """\
你是含能材料研究助手的任务路由器。分析用户查询，选择最合适的处理路径，返回 JSON。

## 六种路径的判断规则

### "data" — 数值查询（精确、快速）
条件（同时满足）：
  1. 问题涉及 1-3 个**明确命名的物质**（RDX、HMX、AP、HTPB、TATB、奥克托今等）
  2. 问的是**单一具体属性**（密度、爆速、爆压、分子量、熔点、分解温度、比冲、感度、氧平衡等）
  3. 答案是一个或几个数值，不需要机理解释

典型示例：
  ✅ "RDX 的密度是多少" → entity="RDX"
  ✅ "HMX 和 TATB 的爆速对比" → entity="HMX/TATB"
  ✅ "AP 的分解温度" → entity="AP"
  ❌ "AP 粒径对燃速有什么影响" → 问的是机理/规律，不是AP本身的数值，应为 literature

### "experiment" — 实验设计
条件（满足其一）：
  - 请求**设计**配方、方案、实验步骤
  - 请求**优化**现有配方的某项性能
  - 请求给出**组分比例**建议

典型示例：
  ✅ "设计一个燃速 > 15 mm/s 的低感度固体推进剂"
  ✅ "推荐一种 HTPB 基推进剂配方"
  ✅ "如何提高现有配方的比冲"
  ❌ "HTPB 粘合剂的性质" → 知识问答，应为 literature

### "safety" — 安全风险评估（高优先级）
条件（满足其一）：
  - 问的是某物质/配方的安全风险、危险性、爆炸危险、储存要求
  - 问两种组分能否相容/混合
  - 给出了具体配方（含百分比），问该配方是否安全
  - 关键词：安全、危险、风险、感度（定性）、爆炸、储存、运输、相容性、ESD

典型示例：
  ✅ "RDX 的安全风险有哪些？"
  ✅ "AP 和铝粉能混合使用吗？有什么危险？"
  ✅ "AP 68% + HTPB 20% + Al 12% 这个配方安全吗？"
  ✅ "如何安全储存 HMX？"
  ❌ "RDX 的感度值（ESD、撞击）是多少？" → 查具体数值，应为 data
  ❌ "AP 粒径对感度的影响机理" → 机理分析，应为 literature

### "modify" — 修改上一次实验方案（最高优先级）
条件：用户明确指向上一个实验/配方方案，要求调整其中某个参数。
关键词：调整、修改、改成、降低/提高XX到YY、在上个方案基础上、把XX改为YY

典型示例：
  ✅ "调整一下配方，把铝粉比例降到 15%"
  ✅ "在上次方案的基础上，把 AP 粒径改成 200μm"
  ✅ "修改方案，去掉 RDX，换成 HMX"
  ❌ "重新设计一个低感度配方" → 没有指向上一次方案，应为 experiment

### "hybrid" — 数据查询 + 实验设计（链式，优先判断）
条件（同时满足）：
  1. 明确要求先查某物质的某个数值
  2. 然后基于该数值或物质设计配方/实验

典型示例：
  ✅ "帮我找 RDX 的密度，再基于这个设计一个配方"
  ✅ "查一下 HMX 的爆速，然后推荐一个性能相近的替代配方"
  ✅ "先告诉我 AP 的分解温度，再设计一个 AP 基推进剂"
  ❌ "设计一个含 RDX 的配方" → 没有先查数值的要求，应为 experiment

### "literature" — 文献研究 / 知识问答（默认路径）
所有不满足上面三类的查询，包括：
  - 机理、影响因素、规律分析
  - 宽泛综述（"XX 的研究进展"）
  - 涉及多个属性或多个问题混合
  - 任何不确定的情况

典型示例：
  ✅ "AP 粒径对燃速有什么影响"（机理规律，非数值）
  ✅ "铝粉在推进剂中的作用机理"
  ✅ "HTPB 粘合剂体系的研究综述"
  ✅ "含能材料感度研究进展"

## 输出格式
返回 JSON（仅 JSON，不加任何额外文字）：
{
  "type": "data"|"experiment"|"literature"|"hybrid"|"modify"|"safety",
  "entity": "物质名（data/hybrid 类型填写，多个用 / 分隔；其余类型填空字符串）",
  "reason": "路由原因，格式：[关键判据] → [路由结果]。例：'查询 RDX 单一数值属性 → 数据查询路径' 或 '请求调整上次配方比例 → 方案修改路径'"
}
"""


def classify_intent(query: str) -> IntentResult:
    """Single LLM call — classify query intent. Falls back to literature on error."""
    client = get_llm_client()
    try:
        resp = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": query},
            ],
            temperature=0.0,
            max_tokens=120,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Strip ```json ... ``` fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            raw = raw.rsplit("```", 1)[0].strip()
        parsed = json.loads(raw)
        intent_type = IntentType(parsed.get("type", "literature"))
        return IntentResult(
            type=intent_type,
            entity=parsed.get("entity", ""),
            reason=parsed.get("reason", ""),
        )
    except Exception as exc:
        logger.warning(f"[IntentClassifier] failed ({exc!r}), defaulting → literature")
        return IntentResult(
            type=IntentType.LITERATURE,
            entity="",
            reason="分类失败，默认使用文献检索 Agent",
        )
