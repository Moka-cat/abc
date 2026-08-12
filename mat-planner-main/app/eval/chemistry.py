"""chemistry.py — 含能材料化学计算工具库（评估用）。

提供：
  OxygenBalance         — 组分 OB% 计算（经验公式，基于元素组成）
  BurningRateEstimator  — AP/HTPB/Al 燃速经验关联模型（Vieille 定律）
  DensityEstimator      — 混合规则密度估算
  ProcessabilityChecker — 工艺可行性检查（固化体系、适用期、除气等）
  FormulationValidator  — 汇总所有化学合理性检查

数据来源：
  - 标准含能材料手册数据（ICT 数据库、Jane's 等）
  - AP/HTPB/Al 燃速文献数据拟合（Sutton & Biblarz 第 9 版）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# 组分数据库（分子式、密度、OB%）
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ComponentData:
    name: str
    formula: str          # 分子式（简写）
    density: float        # g/cm³ (晶体或本体密度)
    ob_percent: float     # 氧平衡 %（100 × (氧原子 - 需氧量)/分子量 × 1600）
    role: str             # oxidizer / binder / fuel / explosive / plasticizer
    notes: str = ""


# 氧平衡计算公式：OB% = 1600/M × (2x + y/2 - z)
# 其中 CxHyNzOw，OB = 1600/M × (w - 2x - y/2)
COMPONENT_DB: dict[str, ComponentData] = {
    # 氧化剂
    "AP": ComponentData("Ammonium Perchlorate", "NH4ClO4", 1.95, +34.04, "oxidizer",
                        "标准粗粉 200μm；细粉 20μm 燃速提升显著"),
    "AN": ComponentData("Ammonium Nitrate", "NH4NO3", 1.72, +19.99, "oxidizer"),
    "KNO3": ComponentData("Potassium Nitrate", "KNO3", 2.11, +39.6, "oxidizer"),
    "KClO4": ComponentData("Potassium Perchlorate", "KClO4", 2.52, +46.2, "oxidizer"),
    "NTO": ComponentData("3-Nitro-1,2,4-triazol-5-one", "C2H2N4O3", 1.93, -24.7, "oxidizer/explosive"),

    # 粘合剂
    "HTPB": ComponentData("Hydroxyl-Terminated Polybutadiene", "C4H6", 0.90, -319.0, "binder",
                          "固化剂：TDI 或 IPDI，NCO:OH=0.85-0.95；固化温度 50-70°C"),
    "GAP": ComponentData("Glycidyl Azide Polymer", "C3H5N3O", 1.30, -101.0, "binder",
                         "含能粘合剂，自身 OB=-101%，优于 HTPB"),
    "BAMO": ComponentData("Bis-Azidomethyl Oxetane", "C5H8N6O", 1.30, -107.0, "binder"),
    "NC": ComponentData("Nitrocellulose", "C6H7N3O11", 1.67, -28.0, "binder/explosive",
                        "硝化度影响 OB%；13.45% N 时 OB=-28%"),
    "PBAN": ComponentData("Polybutadiene Acrylonitrile", "C4H6", 0.92, -315.0, "binder"),

    # 燃料/金属
    "Al": ComponentData("Aluminum", "Al", 2.70, -89.0, "fuel",
                        "30μm 标准；纳米铝（<100nm）燃速提升 2-5×"),
    "Mg": ComponentData("Magnesium", "Mg", 1.74, -49.4, "fuel"),
    "B": ComponentData("Boron", "B", 2.34, -266.0, "fuel", "高能量密度，但点火困难"),

    # 单质炸药
    "RDX": ComponentData("RDX", "C3H6N6O6", 1.82, -21.6, "explosive",
                         "VOD=8750 m/s @ρ=1.82; IS=7.5 J; P_det=34.9 GPa"),
    "HMX": ComponentData("HMX", "C4H8N8O8", 1.91, -21.6, "explosive",
                         "VOD=9110 m/s @ρ=1.91; IS=7.4 J; P_det=39.0 GPa"),
    "TNT": ComponentData("TNT", "C7H5N3O6", 1.65, -73.97, "explosive",
                         "VOD=6950 m/s @ρ=1.64; mp=80.9°C; IS=15 J"),
    "PETN": ComponentData("PETN", "C5H8N4O12", 1.77, -10.1, "explosive",
                          "VOD=8400 m/s @ρ=1.77; IS=3 J（高感度）"),
    "CL-20": ComponentData("CL-20 ε", "C6H6N12O12", 2.04, -10.95, "explosive",
                           "VOD=9400 m/s @ρ=2.04; IS=4 J"),
    "TATB": ComponentData("TATB", "C6H6N6O6", 1.94, -55.8, "explosive",
                          "极钝感：IS>50 J；VOD=7980 m/s"),
    "FOX-7": ComponentData("FOX-7", "C2H4N4O4", 1.89, -21.6, "explosive",
                           "低感度，IS>30 J；VOD=8870 m/s"),
    "DNAN": ComponentData("DNAN", "C7H6N2O5", 1.56, -62.0, "explosive/plasticizer",
                          "钝感熔铸基质，mp=94.5°C"),
    # 增塑剂
    "NG": ComponentData("Nitroglycerin", "C3H5N3O9", 1.60, +3.5, "plasticizer/explosive",
                        "IS=0.2 J（极高感度）；沸点 50°C 分解"),
    "DEGDN": ComponentData("DEGDN", "C4H8N2O7", 1.38, -30.1, "plasticizer"),
    "TMETN": ComponentData("TMETN", "C5H9N3O9", 1.47, -17.2, "plasticizer"),

    # 催化剂/添加剂
    "Fe2O3": ComponentData("Iron Oxide", "Fe2O3", 5.24, 0.0, "catalyst",
                           "燃速催化剂，典型用量 0.1-0.5%，提速 15-30%"),
    "CuO": ComponentData("Copper Oxide", "CuO", 6.31, 0.0, "catalyst"),
    "carbon_black": ComponentData("Carbon Black", "C", 1.80, -266.7, "additive"),
}


# ─────────────────────────────────────────────────────────────────────────────
# 氧平衡计算
# ─────────────────────────────────────────────────────────────────────────────

class OxygenBalance:
    """混合配方的氧平衡计算。

    配方为 {组分名: 质量分数}，OB% = 加权平均各组分 OB%。
    结果判据（复合推进剂）：
      最佳范围  -10% ~ -30%  → good
      可接受    -5% ~ -40%   → acceptable
      偏离      < -40% 或 > 0%  → poor
    """

    # 已知 OB% 的组分别名映射
    _ALIASES: dict[str, str] = {
        "ammonium perchlorate": "AP",
        "ammonium nitrate": "AN",
        "aluminum": "Al",
        "aluminium": "Al",
        "rdx": "RDX",
        "hmx": "HMX",
        "tnt": "TNT",
        "cl20": "CL-20",
        "cl-20": "CL-20",
        "htpb": "HTPB",
        "gap": "GAP",
        "tatb": "TATB",
        "petn": "PETN",
        "fox-7": "FOX-7",
        "ng": "NG",
        "dnan": "DNAN",
    }

    @classmethod
    def _lookup(cls, name: str) -> ComponentData | None:
        key = name.strip()
        if key in COMPONENT_DB:
            return COMPONENT_DB[key]
        lower = key.lower()
        canonical = cls._ALIASES.get(lower)
        if canonical:
            return COMPONENT_DB.get(canonical)
        # Fuzzy: check if any db key is contained in name
        for k, v in COMPONENT_DB.items():
            if k.lower() in lower or lower in k.lower():
                return v
        return None

    @classmethod
    def calculate(cls, formulation: dict[str, dict | float]) -> dict:
        """
        formulation: {name: {"fraction": 0.68, ...}} or {name: 0.68}
        Returns dict with ob_percent, status, component_obs, notes.
        """
        weighted_ob = 0.0
        total_fraction = 0.0
        component_obs = {}
        unknown = []

        for name, val in formulation.items():
            frac = val.get("fraction", val) if isinstance(val, dict) else float(val)
            data = cls._lookup(name)
            if data:
                component_obs[name] = {"ob": data.ob_percent, "fraction": frac, "role": data.role}
                weighted_ob += data.ob_percent * frac
                total_fraction += frac
            else:
                unknown.append(name)
                total_fraction += frac

        if total_fraction > 0 and abs(total_fraction - 1.0) > 0.02:
            weighted_ob = weighted_ob / total_fraction  # normalize

        # Classify — 区分炸药体系和推进剂体系
        # 炸药（高能密度）：OB -30% ~ +5% 为好
        # 复合推进剂（AP/HTPB/Al）：OB -75% ~ -30% 为正常（燃料富余）
        # 熔铸炸药（TNT 基）：OB -80% ~ -30% 为正常
        # 通用判据（无法区分体系时）
        if -75.0 <= weighted_ob <= -5.0:
            status = "good"        # 广义推进剂/炸药均合理
        elif -90.0 <= weighted_ob < -75.0:
            status = "acceptable"  # 偏富燃，可接受
        elif weighted_ob > -5.0:
            status = "oxygen_excess"
        else:
            status = "oxygen_deficient"

        notes = []
        if unknown:
            notes.append(f"未知组分（无法计算OB）：{', '.join(unknown)}")
        if status == "oxygen_excess":
            notes.append("OB% > -5%：配方整体氧化性过强，可能燃烧过于剧烈或存在氧化风险。")
        if status == "oxygen_deficient":
            notes.append("OB% < -40%：氧化剂严重不足，可能导致不完全燃烧，能量利用率低。")

        return {
            "ob_percent": round(weighted_ob, 2),
            "status": status,
            "component_obs": component_obs,
            "unknown_components": unknown,
            "notes": notes,
            "fraction_sum": round(total_fraction, 4),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 密度估算（混合规则）
# ─────────────────────────────────────────────────────────────────────────────

class DensityEstimator:
    """用混合规则（倒数加权）估算配方理论最大密度（TMD）。

    1/ρ_mix = Σ(wᵢ / ρᵢ)
    实际加工密度通常为 TMD × 0.92-0.98（含气孔）。
    """

    @classmethod
    def estimate(cls, formulation: dict[str, dict | float]) -> dict:
        sum_inv = 0.0
        total_fraction = 0.0
        component_densities = {}
        unknown = []

        for name, val in formulation.items():
            frac = val.get("fraction", val) if isinstance(val, dict) else float(val)
            data = OxygenBalance._lookup(name)
            if data and data.density > 0:
                sum_inv += frac / data.density
                component_densities[name] = {"density": data.density, "fraction": frac}
            else:
                unknown.append(name)
            total_fraction += frac

        recognized_fraction = sum(v["fraction"] for v in component_densities.values())
        if sum_inv <= 0 or (total_fraction > 0 and recognized_fraction / total_fraction < 0.5):
            # Less than 50% of the formulation was recognized — TMD would be
            # physically meaningless (harmonic mean only works on complete mixtures).
            return {"tmd_g_cm3": None, "practical_min_g_cm3": None,
                    "practical_max_g_cm3": None,
                    "component_densities": component_densities,
                    "unknown_components": unknown}

        tmd = 1.0 / sum_inv
        return {
            "tmd_g_cm3": round(tmd, 4),
            "practical_min_g_cm3": round(tmd * 0.92, 4),   # 含较多气孔
            "practical_max_g_cm3": round(tmd * 0.98, 4),   # 真空浇铸，气孔少
            "component_densities": component_densities,
            "unknown_components": unknown,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 燃速经验关联（AP/HTPB/Al 体系）
# ─────────────────────────────────────────────────────────────────────────────

class BurningRateEstimator:
    """AP/HTPB/Al 复合推进剂燃速经验估算。

    基于 Vieille 定律: r = a × P^n (mm/s, MPa)
    参数来源（Sutton & Biblarz, 第9版 Table 12-3 + 文献拟合）：

    AP 含量和粒径是决定燃速的两个最关键参数：
      高 AP（70%+）+ 细粉（< 50μm）→ r ≈ 20-35 mm/s @7MPa
      标准（68%）+ 混合粒径 → r ≈ 10-18 mm/s @7MPa
      低 AP（60%）+ 粗粒（200μm）→ r ≈ 4-8 mm/s @7MPa

    Al 含量 8-18% 时对燃速影响有限（±10%）；
    超过 20% 时可能因熔融 Al 液相覆盖导致燃速下降。
    """

    # (AP wt%, particle_type) → (a, n, r_at_7MPa)
    # particle_type: 'coarse' (>100μm), 'medium' (50-100μm), 'fine' (<50μm), 'bimodal'
    _TABLE = {
        # AP%, particle,  a,     n,    r@7MPa
        (70,  "fine"):    (8.5,  0.35, 28.0),
        (70,  "bimodal"): (6.5,  0.33, 22.0),
        (70,  "medium"):  (5.0,  0.32, 16.5),
        (70,  "coarse"):  (3.8,  0.30, 12.0),
        (68,  "fine"):    (7.5,  0.35, 24.5),
        (68,  "bimodal"): (5.5,  0.33, 18.0),
        (68,  "medium"):  (4.2,  0.32, 13.5),
        (68,  "coarse"):  (3.2,  0.30, 10.0),
        (65,  "fine"):    (6.0,  0.35, 19.5),
        (65,  "bimodal"): (4.5,  0.33, 14.5),
        (65,  "medium"):  (3.5,  0.32, 11.0),
        (65,  "coarse"):  (2.7,  0.30,  8.5),
        (60,  "fine"):    (4.5,  0.35, 14.5),
        (60,  "bimodal"): (3.5,  0.33, 11.0),
        (60,  "medium"):  (2.8,  0.32,  8.5),
        (60,  "coarse"):  (2.0,  0.30,  6.0),
    }
    # Fe2O3 催化剂效果：每 0.1% 提升约 4-8%
    _CATALYST_BOOST_PER_0_1PCT = 0.06  # 6% per 0.1 wt% Fe2O3

    @classmethod
    def estimate(
        cls,
        formulation: dict[str, dict | float],
        pressure_mpa: float = 7.0,
    ) -> dict | None:
        """估算燃速。只对 AP/HTPB/Al 类配方有效。"""
        # Extract AP fraction and particle size
        ap_frac = 0.0
        al_frac = 0.0
        ap_particle_um = None
        fe2o3_frac = 0.0

        for name, val in formulation.items():
            v = val if isinstance(val, dict) else {"fraction": float(val)}
            frac = v.get("fraction", 0.0)
            name_lower = name.lower()
            if "ap" in name_lower or "ammonium perchlorate" in name_lower:
                ap_frac = frac
                ap_particle_um = v.get("particle_size_um")
            elif name_lower in ("al", "aluminum", "aluminium"):
                al_frac = frac
            elif "fe2o3" in name_lower or "iron oxide" in name_lower:
                fe2o3_frac = frac

        if ap_frac < 0.50:
            return None  # 不适用此模型

        # Determine particle type
        if ap_particle_um is not None:
            if ap_particle_um < 30:
                particle_type = "fine"
            elif ap_particle_um < 100:
                particle_type = "medium"
            else:
                particle_type = "coarse"
        else:
            particle_type = "bimodal"  # default assumption

        # Snap AP% to nearest table entry
        ap_pct = round(ap_frac * 100)
        snap_ap = min([60, 65, 68, 70], key=lambda x: abs(x - ap_pct))
        key = (snap_ap, particle_type)

        if key not in cls._TABLE:
            key = (snap_ap, "bimodal")
        if key not in cls._TABLE:
            return None

        a, n, r7 = cls._TABLE[key]

        # Calculate at given pressure using r7 as calibrated reference at 7 MPa
        # r = r7 * (P/7)^n ensures the model matches calibrated data at 7 MPa
        r_estimated = r7 * ((pressure_mpa / 7.0) ** n)

        # Al correction (8-18% is normal; outside this range apply penalty)
        al_pct = al_frac * 100
        al_correction = 1.0
        if al_pct > 20:
            al_correction = 0.90  # liquid phase damping
        elif al_pct < 5:
            al_correction = 0.95  # less heat feedback

        # Catalyst correction
        catalyst_boost = 1.0 + (fe2o3_frac * 1000) * cls._CATALYST_BOOST_PER_0_1PCT

        r_final = r_estimated * al_correction * catalyst_boost

        # Uncertainty band ±20%
        return {
            "estimated_r_mm_s": round(r_final, 2),
            "range_low": round(r_final * 0.80, 2),
            "range_high": round(r_final * 1.20, 2),
            "vieille_a": round(a, 3),
            "vieille_n": round(n, 3),
            "ap_pct_used": snap_ap,
            "particle_type_assumed": particle_type,
            "al_correction": al_correction,
            "catalyst_boost": round(catalyst_boost, 3),
            "pressure_mpa": pressure_mpa,
            "model": "AP/HTPB/Al Vieille empirical ±20%",
            "notes": (
                f"AP={snap_ap}%, 粒径={particle_type}, Al={al_pct:.0f}%, "
                f"催化剂={fe2o3_frac*100:.2f}%"
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 工艺完整性检查
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProcessIssue:
    severity: str   # critical / warning / info
    check: str
    description: str
    recommendation: str


class ProcessabilityChecker:
    """检查 HTPB 推进剂工艺方案的技术完整性。

    实验员视角的核查清单：
      1. 固化体系：有无固化剂（TDI/IPDI/MDI），NCO:OH 比是否合理
      2. 固化温度：应在 50-70°C 范围内
      3. 固化时间：HTPB 在 60°C 下最少 72h；50°C 下最少 120h
      4. 真空除气：药浆浇铸前必须真空除气（减少气孔）
      5. 混合顺序：粘合剂先加，氧化剂后加，金属燃料最后
      6. 适用期控制：HTPB/TDI 体系适用期约 2-4h，需注明
      7. 原料干燥：AP 水分 < 0.1%，Al 粉防潮
    """

    def check(self, steps: list[dict], formulation: dict) -> list[ProcessIssue]:
        issues = []
        steps_text = " ".join(
            (s.get("action", "") + " " + s.get("description", "")).lower()
            for s in steps
        )
        has_htpb = any("htpb" in k.lower() for k in formulation)
        has_ap = any("ap" in k.lower() or "ammonium perchlorate" in k.lower() for k in formulation)

        if has_htpb:
            # 1. 固化剂检查
            curing_keywords = ["tdi", "ipdi", "mdi", "isocyanate", "固化剂", "diisocyanate"]
            if not any(kw in steps_text for kw in curing_keywords):
                issues.append(ProcessIssue(
                    severity="critical",
                    check="curing_agent",
                    description="方案中未提及 HTPB 固化剂（TDI/IPDI）。HTPB 需要异氰酸酯固化剂交联，缺少此步骤方案无法执行。",
                    recommendation="在混合步骤中加入 TDI（甲苯二异氰酸酯）或 IPDI，NCO:OH 摩尔比控制在 0.85-0.95。",
                ))

            # 2. 固化温度检查
            cure_temps = [
                s.get("temperature") for s in steps
                if s.get("phase") in ("curing", "固化") or "cure" in s.get("action", "").lower()
                or "固化" in s.get("action", "")
            ]
            cure_temps = [t for t in cure_temps if t is not None]
            if cure_temps:
                for t in cure_temps:
                    if t < 40 or t > 80:
                        issues.append(ProcessIssue(
                            severity="critical",
                            check="cure_temperature",
                            description=f"固化温度 {t}°C 超出 HTPB 推进剂合理范围（50-70°C）。",
                            recommendation="HTPB 推进剂标准固化温度：60°C，持续 72-168h；低于 40°C 固化不完全，高于 80°C 可能导致 HTPB 降解。",
                        ))
            else:
                issues.append(ProcessIssue(
                    severity="warning",
                    check="cure_temperature",
                    description="方案中未明确固化阶段的温度参数。",
                    recommendation="HTPB 标准固化：60°C，72-168h。",
                ))

            # 3. 固化时间检查
            cure_durations = []
            for s in steps:
                if s.get("phase") in ("curing", "固化") or "cure" in s.get("action", "").lower() \
                   or "固化" in s.get("action", ""):
                    d = s.get("duration_min")
                    if d:
                        cure_durations.append(d)
            if cure_durations:
                total_h = sum(cure_durations) / 60
                if total_h < 48:
                    issues.append(ProcessIssue(
                        severity="warning",
                        check="cure_duration",
                        description=f"固化时间仅 {total_h:.1f}h，可能不足（HTPB @60°C 最少 72h）。",
                        recommendation="建议固化时间至少 72h（3天），完全固化需 7-14天。",
                    ))

            # 4. 真空除气
            vacuum_keywords = ["vacuum", "真空", "vacuuming", "deaeration", "除气", "degassing"]
            if not any(kw in steps_text for kw in vacuum_keywords):
                issues.append(ProcessIssue(
                    severity="warning",
                    check="vacuum_deaeration",
                    description="方案中未提及真空除气操作。未除气会导致推进剂内部气孔，影响密度和燃烧均匀性。",
                    recommendation="浇铸前在真空（<1 kPa）下搅拌 30-60 min 除去气泡。",
                ))

        if has_ap:
            # 5. AP 干燥检查
            dry_keywords = ["dry", "干燥", "drying", "moisture", "water content", "水分"]
            if not any(kw in steps_text for kw in dry_keywords):
                issues.append(ProcessIssue(
                    severity="warning",
                    check="ap_drying",
                    description="方案中未提及 AP 原料干燥处理。AP 吸湿性强，水分影响固化和燃速。",
                    recommendation="AP 使用前在 80°C 烘箱干燥 2h，冷却后立即使用；含水量应 < 0.1%。",
                ))

            # 6. 混合顺序检查
            mix_steps = [s for s in steps if s.get("phase") == "mixing" or "mix" in s.get("action","").lower() or "混合" in s.get("action","")]
            if len(mix_steps) < 2:
                issues.append(ProcessIssue(
                    severity="info",
                    check="mixing_sequence",
                    description="混合步骤仅有一步，建议分步添加组分（粘合剂 → 金属燃料 → 分批加 AP）。",
                    recommendation="正确顺序：粘合剂+固化剂先混 → 加 Al 混均 → 分 3-5 批加 AP，每批间隔 5-10 min。",
                ))

        # 7. 适用期（pot life）
        potlife_keywords = ["pot life", "适用期", "working life", "gel point", "凝胶"]
        if has_htpb and not any(kw in steps_text for kw in potlife_keywords):
            issues.append(ProcessIssue(
                severity="info",
                check="pot_life",
                description="方案未提及药浆适用期控制。HTPB/TDI 体系适用期约 2-4h，超过后粘度急增无法浇铸。",
                recommendation="控制药浆温度 ≤ 30°C；分批次混合；计划好浇铸时间，确保在适用期内完成。",
            ))

        return issues

    def score(self, issues: list[ProcessIssue]) -> dict:
        critical = sum(1 for i in issues if i.severity == "critical")
        warnings = sum(1 for i in issues if i.severity == "warning")
        infos = sum(1 for i in issues if i.severity == "info")
        # 100分制：critical -25分，warning -10分，info -3分
        score = max(0, 100 - critical * 25 - warnings * 10 - infos * 3)
        return {
            "score": score,
            "critical_count": critical,
            "warning_count": warnings,
            "info_count": infos,
            "level": "excellent" if score >= 90 else "good" if score >= 70 else "fair" if score >= 50 else "poor",
        }


# ─────────────────────────────────────────────────────────────────────────────
# 诊断建议方向验证
# ─────────────────────────────────────────────────────────────────────────────

class DiagnosisValidator:
    """验证分析报告中的调整建议方向是否符合材料科学规律。

    每个规则定义：
      trigger   — 触发条件（哪个属性偏差多少）
      correct   — 正确的建议方向（关键词应出现在报告中）
      incorrect — 错误方向（若出现则扣分）
    """

    RULES = [
        # 燃速低 → 建议增加细AP或催化剂
        {
            "name": "burning_rate_low",
            "trigger": lambda devs: (devs.get("burning_rate", {}).get("deviation_pct") or 0) < -10,
            "correct_keywords": ["细粉", "fine ap", "particle size", "粒径", "催化剂", "catalyst",
                                  "iron oxide", "氧化铁", "fine", "小粒径", "增加 al", "增加al"],
            "incorrect_keywords": ["减少 ap", "降低ap", "remove al", "减少铝"],
            "explanation": "燃速偏低时，正确措施是：减小AP粒径（增加细粉比例）、添加燃速催化剂（Fe₂O₃）或适当增加Al含量（<18%）",
        },
        # 燃速高 → 建议增大AP粒径或使用燃速抑制剂
        {
            "name": "burning_rate_high",
            "trigger": lambda devs: (devs.get("burning_rate", {}).get("deviation_pct") or 0) > 10,
            "correct_keywords": ["粗粉", "coarse ap", "大粒径", "抑制剂", "suppressant",
                                  "oxamide", "草酰胺", "减少细粉"],
            "incorrect_keywords": ["增加细粉", "add fine", "catalyst", "催化剂"],
            "explanation": "燃速偏高时，正确措施是：增大AP粒径（粗粉比例提高）、添加燃速抑制剂（草酰胺）",
        },
        # 密度低 → 建议真空除气或检查气孔
        {
            "name": "density_low",
            "trigger": lambda devs: (devs.get("density", {}).get("deviation_pct") or 0) < -5,
            "correct_keywords": ["真空", "vacuum", "气孔", "void", "porosity", "气泡",
                                  "bubble", "deaeration", "除气", "密实"],
            "incorrect_keywords": ["增加al", "increase al", "提高AP"],
            "explanation": "密度低于预测通常是工艺问题（气孔/气泡），应检查真空除气工序",
        },
        # 感度比预测高（更敏感）→ 建议检查工艺（晶体质量、粒径）或减少高感度组分
        {
            "name": "sensitivity_higher",
            "trigger": lambda devs: (
                (devs.get("impact_sensitivity", {}).get("deviation_pct") or 0) < -15
            ),
            "correct_keywords": ["晶体", "crystal", "粒径", "particle", "纯度", "purity",
                                  "减少", "decrease", "substitute", "替换", "TATB", "FOX-7"],
            "incorrect_keywords": ["增加rdx", "add hmx", "increase cl-20"],
            "explanation": "感度高于预测，应检查高感度组分的粒径分布（细粒更敏感）或考虑替换为钝感材料",
        },
    ]

    @classmethod
    def validate(cls, deviations: dict, analysis_report: str) -> dict:
        report_lower = analysis_report.lower()
        results = []

        for rule in cls.RULES:
            if not rule["trigger"](deviations):
                continue

            correct_found = [kw for kw in rule["correct_keywords"] if kw.lower() in report_lower]
            incorrect_found = [kw for kw in rule["incorrect_keywords"] if kw.lower() in report_lower]

            direction_correct = len(correct_found) > 0
            direction_wrong = len(incorrect_found) > 0

            results.append({
                "rule": rule["name"],
                "triggered": True,
                "direction_correct": direction_correct and not direction_wrong,
                "correct_keywords_found": correct_found,
                "incorrect_keywords_found": incorrect_found,
                "explanation": rule["explanation"],
                "score": 3 if (direction_correct and not direction_wrong)
                         else 1 if direction_correct
                         else 0,
            })

        if not results:
            return {"triggered_rules": [], "total_score": None,
                    "note": "无触发规则（偏差均在可接受范围内）"}

        total = sum(r["score"] for r in results)
        max_score = len(results) * 3
        return {
            "triggered_rules": results,
            "total_score": total,
            "max_score": max_score,
            "direction_accuracy": round(total / max_score, 3) if max_score > 0 else None,
        }
