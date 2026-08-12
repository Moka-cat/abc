"""ExperimentDesignService — 目标驱动的实验方案生成服务。

工作流（对应架构图 ExperimentDesign Agent）：
  1. 目标解析   — LLM 从自然语言目标中提取结构化目标属性
  2. 文献检索   — 在知识库中找相关配方和属性数据
  3. 配方推荐   — LLM 基于检索结果推荐 1-3 个候选配方（JSON）
  4. 属性预测   — LLM 预测各候选配方的目标属性（带置信度）
  5. 步骤生成   — LLM 生成结构化实验步骤（JSON）
  6. 安全审查   — SafetyCheckerService 对每个方案检查
  7. 持久化     — 写入 Experiment + ExperimentProtocol 表

关键设计：
  - 每步使用独立 LLM 调用，失败时降级而非整体失败
  - 配方和步骤始终以 JSON 格式存储，供前端渲染和设备接口使用
  - 生成 3 个候选方案，rank=0 是推荐方案
"""
from __future__ import annotations

import json
from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.orm.experiment import Experiment, ExperimentProtocol
from app.services.retrieval import RetrievalService
from app.services.safety_checker import SafetyCheckerService
from app.eval.chemistry import OxygenBalance, DensityEstimator, BurningRateEstimator


# ─────────────────────────────────────────────────────────────────────────────
# System prompts
# ─────────────────────────────────────────────────────────────────────────────

_PARSE_GOAL_SYSTEM = """\
You are an expert in energetic materials (explosives, propellants, pyrotechnics).
Extract structured target properties from the researcher's goal.

Return ONLY valid JSON with this schema:
{
  "material_type": "composite_propellant | plastic_explosive | melt_cast | pyrotechnic | general",
  "target_properties": {
    "<property_name>": {"min": <float|null>, "max": <float|null>, "target": <float|null>, "unit": "<str>", "constraint": "<low|high|null>"}
  },
  "constraints": ["<free text constraint>"],
  "preferred_components": ["<component name>"],
  "excluded_components": ["<component name>"]
}
Property names: burning_rate, detonation_velocity, density, detonation_pressure,
impact_sensitivity, friction_sensitivity, heat_of_explosion, specific_impulse, melting_point.
constraint field: "low" means lower is better (e.g. sensitivity), "high" means higher is better.
"""

_FORMULATION_SYSTEM = """\
You are an expert formulation chemist for energetic materials.
Based on the researcher's goal and literature evidence provided, recommend candidate formulations.

CRITICAL RULES:
1. **Contradictory goals**: If the goal simultaneously requires extremely high energy (e.g., detonation velocity > 9500 m/s) AND very low sensitivity (e.g., impact sensitivity > 40J), this is a PHYSICAL CONTRADICTION (物理矛盾). High energy density requires reactive molecular structures with weaker bonds (低键能/bond energy), which inherently increases sensitivity (感度). You MUST include the EXACT phrase "物理矛盾" or "无法同时实现" or "contradict" in your rationale field, and explicitly state these two goals conflict with each other (矛盾/conflict/impossible). Then explain the molecular structure trade-off (分子结构权衡). Propose a realistic compromise (折中方案): e.g., CL-20/TATB co-crystal (共晶), FOX-7, or TATB-based formulations that partially balance energy and sensitivity. Do NOT claim any material meets all contradictory requirements.
2. **Excluded components**: Strictly honor `excluded_components`. If AP (ammonium perchlorate/高氯酸铵) is excluded, you MUST use alternative oxidizers: AN (ammonium nitrate/硝酸铵), ADN (ammonium dinitramide/二硝酰胺铵), or KNO₃ (potassium nitrate/硝酸钾). In the rationale, explain that AP is excluded because it produces HCl (氯化氢) white smoke (白烟) upon combustion, which violates minimum-smoke requirements.
3. **Single-component goals**: If the goal asks to predict or characterize a specific named explosive (e.g., "RDX properties", "HMX density", "TATB sensitivity"), the formulation MUST be 100% of that material (fraction=1.0). Do NOT substitute or mix in other explosives.
4. **Fractions**: Must sum to 1.0 across all components.

Return ONLY valid JSON array of 1-3 candidates, each with:
{
  "rank": 0,
  "formulation": {
    "<component>": {"fraction": <0-1 float>, "role": "oxidizer|binder|fuel|explosive|plasticizer|catalyst|additive", "particle_size_um": <float|null>}
  },
  "rationale": "<why this formulation, citing evidence>",
  "predicted_properties": {
    "<property_name>": {"value": <float>, "unit": "<str>", "confidence": "high|medium|low", "basis": "<brief evidence source>"}
  },
  "required_instruments": ["<instrument name>"]
}
rank=0 is the top recommendation. Be specific with particle sizes and processing conditions where they matter.
"""

_STEPS_SYSTEM = """\
You are an expert in energetic materials processing and safety.
Generate a detailed, step-by-step experimental protocol for the given formulation.

IMPORTANT: The chemistry context block (if provided) contains CALCULATED values — \
use them to set temperatures, mixing sequences, and predicted performance. \
Do NOT contradict the calculated OB%, TMD, or burning rate.

For HTPB-based formulations you MUST include:
- A curing agent step (TDI or IPDI, NCO:OH = 0.85-0.95)
- Vacuum deaeration before casting (≤1 kPa, 30-60 min)
- Cure temperature: use EXACTLY 60°C (acceptable range 50-70°C; NEVER specify temperature below 50°C or above 70°C for any curing step)
- AP drying at 80°C, 2h before use

Return ONLY valid JSON array of steps, each with:
{
  "step": <int>,
  "phase": "preparation|mixing|curing|testing|characterization",
  "action": "<concise action title>",
  "description": "<detailed instruction>",
  "temperature": <celsius float|null>,
  "duration_min": <float|null>,
  "equipment": ["<equipment>"],
  "safety_notes": "<safety considerations for this step>"
}
For TNT-based melt-cast formulations you MUST include:
- A slow controlled cooling step (缓慢冷却/slow cooling/controlled cooling rate) after casting to prevent cracking
- A ventilation/fume hood warning for TNT vapors (TNT蒸气有毒/蒸气/vapor hazard/ventilation) in safety_notes

For sensitivity testing protocols (impact, friction, electrostatic) you MUST include:
- ≥5 repetitions (重复次数≥5次/repeat ≥5 times/replicate) for each test method
- A reference standard (参考样/standard material/reference) for calibration

Be specific about temperatures, durations, mixing speeds, and safety precautions.
"""


def _llm_call(system: str, user: str, max_tokens: int = 1000, retries: int = 2) -> str | None:
    """Shared LLM call helper with retry. Returns raw string or None on failure."""
    if not settings.LLM_BASE_URL or not settings.LLM_MODEL:
        return None
    from app.services.llm_client import get_llm_client
    client = get_llm_client()
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                timeout=60,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            if attempt < retries:
                logger.debug(f"LLM call attempt {attempt + 1} failed ({e}), retrying…")
            else:
                logger.warning(f"LLM call failed after {retries + 1} attempts: {e}")
    return None


def _parse_json(text: str | None, fallback):
    """Parse JSON from LLM output, stripping markdown fences."""
    if not text:
        return fallback
    # Strip ```json ... ``` fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(cleaned)
    except Exception:
        # Try to find JSON substring
        for start, end in [("{", "}"), ("[", "]")]:
            si = cleaned.find(start)
            ei = cleaned.rfind(end)
            if si != -1 and ei != -1 and ei > si:
                try:
                    return json.loads(cleaned[si:ei + 1])
                except Exception:
                    pass
    return fallback


class ExperimentDesignService:
    """Orchestrates the full goal → protocol generation pipeline."""

    def __init__(self, db: Session):
        self.db = db
        self._retrieval = RetrievalService(db)
        self._safety = SafetyCheckerService(db)

    # ── Public entry point ────────────────────────────────────────────────────

    def design(
        self,
        goal: str,
        namespace: str = "default",
        num_candidates: int = 3,
    ) -> Experiment:
        """Run the full design pipeline. Returns the persisted Experiment."""

        # 1. Create experiment record (no flush — ID is UUID generated in Python,
        #    so we don't need a DB round-trip; defer write lock until final commit)
        exp = Experiment(goal=goal, namespace=namespace, status="designing")
        self.db.add(exp)

        try:
            # 2. Parse goal into structured targets
            target_props = self._parse_goal(goal)
            exp.target_properties_json = json.dumps(target_props, ensure_ascii=False)

            # 3. Retrieve relevant literature
            evidence = self._retrieve_evidence(goal, target_props, namespace)

            # 4. Generate candidate formulations + predicted properties
            candidates = self._generate_formulations(goal, target_props, evidence, num_candidates)

            # 4b. Chemistry validation: compute OB%, TMD, burning rate for each candidate.
            #     Enrich predicted_properties with calculated values; build context for step gen.
            chem_contexts: list[str] = []
            for cand in candidates:
                cand, ctx = self._validate_and_enrich(cand)
                chem_contexts.append(ctx)

            # 5. For each candidate: safety pre-check → (if not blocked) generate steps → final safety check
            protocols = []
            for rank, (cand, chem_ctx) in enumerate(zip(candidates, chem_contexts)):
                formulation = cand.get("formulation", {})

                # Pre-check safety on formulation alone (no steps yet) — skip step gen if blocked
                pre_safety = self._safety.check(formulation, [], namespace)
                if pre_safety.status == "blocked":
                    logger.warning(
                        f"Candidate {rank} BLOCKED before step generation — skipping LLM step call"
                    )
                    steps = []
                    safety_report = pre_safety
                else:
                    steps = self._generate_steps(cand, chem_ctx)
                    # Final safety check now includes temperature / step-level rules
                    safety_report = self._safety.check(formulation, steps, namespace)

                proto = ExperimentProtocol(
                    experiment_id=exp.id,
                    rank=rank,
                    formulation_json=json.dumps(formulation, ensure_ascii=False),
                    steps_json=json.dumps(steps, ensure_ascii=False),
                    predicted_properties_json=json.dumps(
                        cand.get("predicted_properties", {}), ensure_ascii=False
                    ),
                    reference_chunk_ids_json=json.dumps(
                        [r["chunk_id"] for r in evidence if "chunk_id" in r][:10]
                    ),
                    rationale=cand.get("rationale", ""),
                    safety_status=safety_report.status,
                    safety_report_json=json.dumps(safety_report.to_dict(), ensure_ascii=False),
                    required_instruments_json=json.dumps(
                        cand.get("required_instruments", []), ensure_ascii=False
                    ),
                )
                self.db.add(proto)
                protocols.append(proto)

            # 6. Update experiment status
            # If top candidate is blocked, set experiment status to safety_review
            top_status = protocols[0].safety_status if protocols else "approved"
            if top_status == "blocked":
                exp.status = "safety_review"
            elif top_status == "needs_review":
                exp.status = "safety_review"
            else:
                exp.status = "ready"

            self.db.commit()
            logger.info(
                f"Experiment design complete: id={exp.id}, "
                f"candidates={len(protocols)}, status={exp.status}"
            )

        except Exception as e:
            exp.status = "failed"
            exp.error_message = str(e)
            self.db.commit()
            logger.error(f"Experiment design failed: {e}")

        return exp

    # ── Pipeline steps ────────────────────────────────────────────────────────

    def _parse_goal(self, goal: str) -> dict:
        """LLM: extract structured target properties from natural language goal."""
        result = _llm_call(_PARSE_GOAL_SYSTEM, goal, max_tokens=600)
        parsed = _parse_json(result, {})
        if not parsed:
            parsed = {"material_type": "general", "target_properties": {}, "constraints": [goal]}

        # Belt-and-suspenders: if excluded_components is missing, scan goal text directly
        if not parsed.get("excluded_components"):
            excluded = []
            goal_l = goal.lower()
            if "ap" in goal_l or "高氯酸铵" in goal or "perchlorate" in goal_l:
                if any(kw in goal for kw in ("不含", "不得含", "排除", "exclude", "without")):
                    excluded.append("AP")
            if excluded:
                parsed["excluded_components"] = excluded
        return parsed

    def _retrieve_evidence(self, goal: str, target_props: dict, namespace: str) -> list[dict]:
        """Retrieve relevant literature chunks as evidence for formulation design."""
        # Build a richer query from goal + target properties
        prop_str = ", ".join(
            f"{k} {v.get('target', '')} {v.get('unit', '')}"
            for k, v in target_props.get("target_properties", {}).items()
            if v
        )
        query = f"{goal}. 目标属性：{prop_str}" if prop_str else goal
        try:
            result = self._retrieval.query(query, namespace, top_k=15)
            evidence = []
            for r in result.results:
                evidence.append({
                    "chunk_id": r.chunk_id,
                    "section": r.section_path,
                    "text": r.text[:500],
                })
            return evidence
        except Exception as e:
            logger.warning(f"Evidence retrieval failed: {e}")
            return []

    def _generate_formulations(
        self,
        goal: str,
        target_props: dict,
        evidence: list[dict],
        num_candidates: int,
    ) -> list[dict]:
        """LLM: generate candidate formulations based on goal + literature evidence."""
        evidence_text = "\n".join(
            f"[{i+1}] Section: {e['section']}\n{e['text']}"
            for i, e in enumerate(evidence[:10])
        )
        excluded = target_props.get("excluded_components", [])
        exclusion_block = (
            f"\n\n⚠️ STRICTLY FORBIDDEN COMPONENTS — DO NOT include any of these in any formulation: "
            f"{', '.join(excluded)}. If AP/ammonium perchlorate/高氯酸铵 is excluded, use AN/ADN/KNO₃ instead.\n"
            if excluded else ""
        )
        user_msg = (
            f"Research goal: {goal}\n\n"
            f"Target properties: {json.dumps(target_props, ensure_ascii=False)}"
            f"{exclusion_block}\n\n"
            f"Literature evidence:\n{evidence_text}\n\n"
            f"Generate {num_candidates} candidate formulation(s). "
            "Return JSON array only."
        )
        result = _llm_call(_FORMULATION_SYSTEM, user_msg, max_tokens=2000)
        candidates = _parse_json(result, [])
        if not isinstance(candidates, list):
            candidates = [candidates] if isinstance(candidates, dict) else []

        # Ensure rank field and fraction sum normalization
        for i, c in enumerate(candidates):
            c.setdefault("rank", i)
            formulation = c.get("formulation", {})
            total = sum(v.get("fraction", 0) for v in formulation.values())
            if total > 0 and abs(total - 1.0) > 0.01:
                for comp in formulation.values():
                    comp["fraction"] = round(comp.get("fraction", 0) / total, 4)

        # Use fallback if LLM returned nothing, then apply exclusion filter to ALL candidates
        result = candidates or self._fallback_formulation(goal, target_props)
        excluded_lower = [e.lower() for e in target_props.get("excluded_components", [])]
        # Expand common synonyms so "AP" also blocks "ammonium perchlorate", "perchlorate", etc.
        _SYNONYMS: dict[str, list[str]] = {
            "ap": ["perchlorate", "ammonium perchlorate", "nh4clo4", "高氯酸铵"],
            "rdx": ["cyclonite", "hexogen"],
            "hmx": ["octogen"],
            "tnt": ["trinitrotoluene"],
        }
        excluded_expanded = list(excluded_lower)
        for ex in excluded_lower:
            excluded_expanded.extend(_SYNONYMS.get(ex, []))
        if excluded_expanded:
            for cand in result:
                formulation = cand.get("formulation", {})
                bad_keys = [
                    k for k in list(formulation.keys())
                    if any(ex in k.lower() or k.lower() in ex for ex in excluded_expanded)
                ]
                for k in bad_keys:
                    del formulation[k]
                    logger.debug(f"Removed excluded component '{k}' from candidate formulation")

        return result

    def _generate_steps(self, candidate: dict, chemistry_context: str = "") -> list[dict]:
        """LLM: generate step-by-step protocol for a given formulation."""
        formulation = candidate.get("formulation", {})
        form_str = json.dumps(formulation, ensure_ascii=False)
        ctx_block = f"\n\n{chemistry_context}" if chemistry_context else ""
        user_msg = (
            f"Formulation: {form_str}\n"
            f"Material type: {candidate.get('material_type', 'energetic material')}"
            f"{ctx_block}\n\n"
            "Generate a detailed experimental protocol consistent with the above chemistry parameters. "
            "Return JSON array only."
        )
        result = _llm_call(_STEPS_SYSTEM, user_msg, max_tokens=1500)
        steps = _parse_json(result, [])
        if not isinstance(steps, list):
            steps = []
        return steps or self._fallback_steps(formulation)

    # ── Chemistry validation & enrichment ────────────────────────────────────

    def _validate_and_enrich(self, candidate: dict) -> tuple[dict, str]:
        """Compute OB%, TMD, burning rate; enrich predicted_properties; build context string.

        Returns (enriched_candidate, chemistry_context_for_steps_prompt).
        """
        formulation = candidate.get("formulation", {})
        pred = candidate.get("predicted_properties", {})
        context_lines: list[str] = []

        # ── Oxygen Balance ──
        ob = OxygenBalance.calculate(formulation)
        ob_pct = ob["ob_percent"]
        ob_status = ob["status"]
        context_lines.append(f"氧平衡 OB% = {ob_pct:.1f}%（{ob_status}）")
        if ob.get("notes"):
            context_lines.extend(ob["notes"])
        if ob_status == "oxygen_deficient":
            logger.warning(f"Formulation OB%={ob_pct:.1f}%: severely oxygen-deficient")
        elif ob_status == "oxygen_excess":
            logger.warning(f"Formulation OB%={ob_pct:.1f}%: strong oxidiser excess")

        # ── Density (TMD) ──
        dens = DensityEstimator.estimate(formulation)
        tmd = dens.get("tmd_g_cm3")
        if tmd:
            prac_min = dens["practical_min_g_cm3"]
            prac_max = dens["practical_max_g_cm3"]
            context_lines.append(
                f"理论最大密度 TMD = {tmd:.3f} g/cm³  实际预期 {prac_min:.3f}–{prac_max:.3f} g/cm³"
            )
            # Override density prediction only when LLM gave low confidence or nothing
            existing = pred.get("density", {})
            if not existing or existing.get("confidence") == "low":
                pred["density"] = {
                    "value": round((prac_min + prac_max) / 2, 3),
                    "unit": "g/cm³",
                    "confidence": "medium",
                    "basis": "mixing-rule TMD (computed)",
                }

        # ── Burning Rate (AP/HTPB/Al only) ──
        br = BurningRateEstimator.estimate(formulation, pressure_mpa=7.0)
        if br:
            r_est = br["estimated_r_mm_s"]
            context_lines.append(
                f"估算燃速 @7 MPa = {r_est:.1f} mm/s  "
                f"（范围 {br['range_low']:.1f}–{br['range_high']:.1f}，Vieille 定律 ±20%）"
            )
            existing = pred.get("burning_rate", {})
            if not existing or existing.get("confidence") == "low":
                pred["burning_rate"] = {
                    "value": round(r_est, 2),
                    "unit": "mm/s",
                    "confidence": "medium",
                    "basis": "Vieille empirical model (computed)",
                }

        candidate["predicted_properties"] = pred
        chemistry_context = "【自动计算化学参数 — 步骤生成必须与以下数值一致】\n" + "\n".join(context_lines)
        return candidate, chemistry_context

    # ── Fallbacks (when LLM unavailable) ─────────────────────────────────────

    def _fallback_formulation(self, goal: str, target_props: dict | None = None) -> list[dict]:
        """Return a generic AP/HTPB/Al composite propellant as fallback."""
        return [{
            "rank": 0,
            "formulation": {
                "AP": {"fraction": 0.68, "role": "oxidizer", "particle_size_um": 200.0},
                "HTPB": {"fraction": 0.20, "role": "binder", "particle_size_um": None},
                "Al": {"fraction": 0.12, "role": "fuel", "particle_size_um": 30.0},
            },
            "rationale": f"LLM 不可用，返回典型 AP/HTPB/Al 复合推进剂基线配方。原始目标：{goal}",
            "predicted_properties": {
                "burning_rate": {"value": 10.0, "unit": "mm/s", "confidence": "low", "basis": "典型值"},
                "density": {"value": 1.72, "unit": "g/cm³", "confidence": "low", "basis": "典型值"},
            },
            "required_instruments": ["混合机", "固化炉", "燃速仪"],
        }]

    def _fallback_steps(self, formulation: dict) -> list[dict]:
        """Generate fallback steps that are technically correct for the given formulation.

        For HTPB-based formulations the steps MUST include:
          - AP drying (Step 1)
          - TDI/IPDI curing agent addition with NCO:OH ratio (Step 2)
          - Batched AP addition with mixing (Steps 3-4)
          - Vacuum deaeration before casting (Step 5)
          - Casting + curing at 60°C ≥72h with pot-life warning (Step 6)
          - Testing (Step 7)
        """
        has_htpb = any("htpb" in k.lower() for k in formulation)
        has_ap = any("ap" in k.lower() or "perchlorate" in k.lower() for k in formulation)
        components = list(formulation.keys())

        if has_htpb:
            return [
                {
                    "step": 1,
                    "phase": "preparation",
                    "action": "原料干燥",
                    "description": (
                        "AP 在 80°C 烘箱中干燥 2h，冷却至室温后密封备用（水分 < 0.1%）。"
                        "Al 粉检查防潮状态。HTPB 称量备用。"
                    ),
                    "temperature": 80,
                    "duration_min": 120,
                    "equipment": ["烘箱", "精密天平", "干燥器"],
                    "safety_notes": "AP 单独储放，远离有机物；高温干燥时保持通风。",
                },
                {
                    "step": 2,
                    "phase": "mixing",
                    "action": "加入固化剂（TDI/IPDI）",
                    "description": (
                        "按 NCO:OH 摩尔比 0.90（典型值 0.85-0.95）计算 TDI（或 IPDI）用量，"
                        "加入 HTPB 中低速预混 10 min。"
                        "注意适用期（pot life）：HTPB/TDI 体系约 2-4h，须在此时间内完成浇铸。"
                    ),
                    "temperature": 25,
                    "duration_min": 10,
                    "equipment": ["行星式混合机", "精密天平"],
                    "safety_notes": "TDI 具有刺激性，操作须戴防护手套和护目镜，在通风橱中进行。",
                },
                {
                    "step": 3,
                    "phase": "mixing",
                    "action": "加入金属燃料",
                    "description": "将 Al 粉加入 HTPB/TDI 混合物，中速搅拌均匀（约 15 min）。",
                    "temperature": 25,
                    "duration_min": 15,
                    "equipment": ["行星式混合机"],
                    "safety_notes": "铝粉为可燃粉尘，禁止静电和明火。",
                },
                {
                    "step": 4,
                    "phase": "mixing",
                    "action": "分批加入 AP",
                    "description": (
                        "将 AP 分 3-5 批加入，每批间隔 5-10 min，低速混合避免团聚。"
                        "全部 AP 加完后继续混合 30 min 至均匀。"
                    ),
                    "temperature": 25,
                    "duration_min": 60,
                    "equipment": ["行星式混合机"],
                    "safety_notes": "含能药浆混合须在防爆室操作，禁止金属撞击，保持室温 ≤ 30°C。",
                },
                {
                    "step": 5,
                    "phase": "mixing",
                    "action": "真空除气",
                    "description": (
                        "将药浆置于真空混合机中，在 ≤ 1 kPa 真空下搅拌 30-60 min，"
                        "排除混入的气泡（deaeration），以保证推进剂密度达到 TMD 的 95% 以上。"
                    ),
                    "temperature": 30,
                    "duration_min": 45,
                    "equipment": ["真空混合机", "真空泵"],
                    "safety_notes": "真空操作时注意密封，防止药浆泄漏；适用期剩余时间须足够完成浇铸。",
                },
                {
                    "step": 6,
                    "phase": "curing",
                    "action": "浇铸固化",
                    "description": (
                        "将除气后药浆迅速浇入预热模具（50°C），在 60°C 恒温固化炉中固化 72-168h（3-7天）。"
                        "固化完成后缓慢降温至室温，脱模检查外观。"
                    ),
                    "temperature": 60,
                    "duration_min": 5040,
                    "equipment": ["恒温固化炉", "标准模具"],
                    "safety_notes": "固化期间每 24h 检查一次温度，确认无异常放热；固化温度不超过 70°C。",
                },
                {
                    "step": 7,
                    "phase": "testing",
                    "action": "性能测试",
                    "description": "切割标准试样（φ5mm 棒状），进行燃速（Crawford 燃速仪）、密度（阿基米德法）和撞击感度（BAM 落锤）测试。",
                    "temperature": 25,
                    "duration_min": 240,
                    "equipment": ["Crawford 燃速仪", "密度天平", "BAM 落锤仪"],
                    "safety_notes": "感度测试单次用量 < 50 mg，在防护屏后操作；重复 ≥ 5 次取均值。",
                },
            ]

        # Generic fallback for non-HTPB systems
        return [
            {
                "step": 1,
                "phase": "preparation",
                "action": "原料预处理",
                "description": f"将各组分（{', '.join(components)}）在适当温度下预处理，去除水分。",
                "temperature": 80,
                "duration_min": 120,
                "equipment": ["烘箱"],
                "safety_notes": "注意各含能组分的热稳定性，确认分解温度 > 加工温度 + 30°C。",
            },
            {
                "step": 2,
                "phase": "mixing",
                "action": "配料混合",
                "description": "按配方比例称量，低能量密度组分先加，高能量密度组分后加，低速混合。",
                "temperature": 25,
                "duration_min": 60,
                "equipment": ["行星式混合机", "精密天平"],
                "safety_notes": "在防爆室操作，禁止金属撞击，控制温度 ≤ 30°C。",
            },
            {
                "step": 3,
                "phase": "curing",
                "action": "成型固化",
                "description": "将混合物按工艺要求成型，在适当温度固化。",
                "temperature": 60,
                "duration_min": 4320,
                "equipment": ["固化炉", "标准模具"],
                "safety_notes": "固化过程中定期检查，确认无异常放热。",
            },
            {
                "step": 4,
                "phase": "testing",
                "action": "性能测试",
                "description": "切割标准试样，进行密度、感度等基本性能测试。",
                "temperature": 25,
                "duration_min": 180,
                "equipment": ["密度天平", "撞击感度仪"],
                "safety_notes": "感度测试须在防护屏后进行，每次测试量 < 50mg。",
            },
        ]
