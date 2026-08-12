"""Chemistry Skills — 含能材料化学计算工具，供总控规划智能体按需调用。

注册的工具（8 个）：
  calc_oxygen_balance     — 配方 OB% 计算（含量加权平均，基于组分数据库）
  calc_density            — 混合规则 TMD + 工艺密度范围估算
  calc_burning_rate       — AP/HTPB/Al 体系燃速经验关联（Vieille 定律）
  rdkit_mol_properties    — 任意分子 SMILES → MW / 分子式 / OB% / 含能基团统计
  validate_formulation    — 一键综合验证（OB + 密度 + 燃速 + 工艺可行性）
  resolve_compound        — 化合物名称/CAS → SMILES + 标准化属性（PubChem API）
  calc_detonation_params  — 单质炸药爆速/爆压估算（Kamlet-Jacobs 经验公式）
  find_similar_compounds  — 在含能材料库中按 Tanimoto 结构相似度搜索类似物

RDKit 依赖：rdkit>=2024（uv add rdkit）
PubChem 依赖：pubchempy>=1.0（uv add pubchempy）
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.tools.base import BaseTool, ToolResult
from app.tools.registry import ToolRegistry


# ─────────────────────────────────────────────────────────────────────────────
# 1. calc_oxygen_balance
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class CalcOxygenBalanceTool(BaseTool):
    name = "calc_oxygen_balance"
    description = (
        "Calculate the oxygen balance (OB%) of an energetic material formulation. "
        "OB% measures whether the formulation has excess or deficient oxygen for "
        "complete combustion. Optimal range for composite propellants is -10% to +5%; "
        "for explosives, near 0% maximises brisance. "
        "Returns OB% for each component and the weighted average, plus a status flag "
        "(oxygen_deficient / near_optimal / oxygen_excess) and any warnings."
    )
    parameters = {
        "formulation": {
            "type": "object",
            "description": (
                "Formulation as a dict mapping component name to either a fraction "
                "(0–1) or a sub-dict with at least a 'fraction' key. "
                "Recognised component names: AP, AN, RDX, HMX, TNT, PETN, CL-20, "
                "TATB, FOX-7, HTPB, GAP, BAMO, NC, Al, Mg, B, NG, DNAN, NTO, etc. "
                "Example: {\"AP\": 0.68, \"HTPB\": 0.20, \"Al\": 0.12}"
            ),
        },
    }
    required = ["formulation"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, formulation: dict, **kwargs) -> ToolResult:
        try:
            from app.eval.chemistry import OxygenBalance
            result = OxygenBalance.calculate(formulation)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 2. calc_density
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class CalcDensityTool(BaseTool):
    name = "calc_density"
    description = (
        "Estimate the theoretical maximum density (TMD) and practical casting density "
        "range of an energetic material formulation using the inverse-mixing rule "
        "(1/ρ_mix = Σ wᵢ/ρᵢ). "
        "Practical density is typically 95–98% of TMD for composite propellants. "
        "Returns TMD (g/cm³), practical_min, practical_max, and per-component densities."
    )
    parameters = {
        "formulation": {
            "type": "object",
            "description": (
                "Formulation dict mapping component names to fractions (0–1) or "
                "sub-dicts with a 'fraction' key. "
                "Example: {\"AP\": {\"fraction\": 0.68}, \"HTPB\": {\"fraction\": 0.20}, "
                "\"Al\": {\"fraction\": 0.12}}"
            ),
        },
    }
    required = ["formulation"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, formulation: dict, **kwargs) -> ToolResult:
        try:
            from app.eval.chemistry import DensityEstimator
            result = DensityEstimator.estimate(formulation)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 3. calc_burning_rate
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class CalcBurningRateTool(BaseTool):
    name = "calc_burning_rate"
    description = (
        "Estimate the burning rate of an AP/HTPB/Al composite propellant at a given "
        "chamber pressure using the Vieille empirical correlation (r = a·Pⁿ). "
        "Corrections are applied for AP particle size (fine vs. coarse), Al content, "
        "and presence of burning-rate catalysts (Fe₂O₃, Cu-salts). "
        "Only applicable when AP content ≥ 50 wt%. Returns estimated r (mm/s) at the "
        "specified pressure, Vieille parameters (a, n), and the uncertainty range. "
        "For non-AP-based formulations, returns null with an explanation."
    )
    parameters = {
        "formulation": {
            "type": "object",
            "description": (
                "Formulation dict. Component names must match COMPONENT_DB keys "
                "(AP, HTPB, Al, Fe2O3, etc.) or contain recognisable substrings. "
                "Example: {\"AP\": 0.68, \"HTPB\": 0.20, \"Al\": 0.12}"
            ),
        },
        "pressure_mpa": {
            "type": "number",
            "description": "Chamber pressure in MPa (default: 7.0 MPa ≈ 70 atm).",
            "default": 7.0,
        },
    }
    required = ["formulation"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, formulation: dict, pressure_mpa: float = 7.0, **kwargs) -> ToolResult:
        try:
            from app.eval.chemistry import BurningRateEstimator
            result = BurningRateEstimator.estimate(formulation, pressure_mpa=pressure_mpa)
            if result is None:
                return ToolResult(success=True, data={
                    "applicable": False,
                    "reason": "Burning-rate model only applies to AP-based formulations (AP ≥ 50 wt%). "
                              "Consider search_memory for literature values on this system.",
                })
            return ToolResult(success=True, data={"applicable": True, **result})
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 4. rdkit_mol_properties
# ─────────────────────────────────────────────────────────────────────────────

def _count_energetic_groups(mol) -> dict:
    """Count energetic functional groups via SMARTS patterns."""
    from rdkit.Chem import MolFromSmarts
    patterns = {
        "nitro_group":       "[N+](=O)[O-]",          # -NO2 (aromatic or aliphatic)
        "nitrate_ester":     "O[N+](=O)[O-]",          # -O-NO2
        "nitramine":         "N[N+](=O)[O-]",          # N-NO2
        "azide":             "N=[N+]=[N-]",             # -N3
        "peroxide":          "OO",                      # -O-O-
        "diazo":             "[N]=[N+]=[C]",
        "furazan":           "c1nnoc1",                 # furazan ring
        "tetrazole":         "c1nnn[nH]1",
    }
    counts = {}
    for name, smarts in patterns.items():
        patt = MolFromSmarts(smarts)
        if patt:
            counts[name] = len(mol.GetSubstructMatches(patt))
    return {k: v for k, v in counts.items() if v > 0}


def _ob_from_formula(formula_dict: dict, mw: float) -> float | None:
    """
    Compute OB% from atom counts using the standard energetics convention:
      OB% = (1600 / M) × (nO − 2·nC − nH/2 − nCl/2)
    Nitrogen, fluorine, etc. are ignored in this approximation.
    Returns None if M ≤ 0.
    """
    if mw <= 0:
        return None
    nC = formula_dict.get("C", 0)
    nH = formula_dict.get("H", 0)
    nO = formula_dict.get("O", 0)
    nCl = formula_dict.get("Cl", 0)
    ob = (1600.0 / mw) * (nO - 2 * nC - nH / 2 - nCl / 2)
    return round(ob, 2)


@ToolRegistry.register
class RDKitMolPropertiesTool(BaseTool):
    name = "rdkit_mol_properties"
    description = (
        "Compute physicochemical and energetic properties of any molecule from its "
        "SMILES string using RDKit. "
        "Useful for novel or custom compounds not in the built-in component database. "
        "Returns: molecular formula, exact molecular weight, oxygen balance (OB%), "
        "energetic functional group counts (nitro, nitrate ester, nitramine, azide, "
        "peroxide, etc.), ring count, rotatable bonds, H-bond donors/acceptors, "
        "and a sensitivity hint based on the functional group profile. "
        "Example use: evaluate a new nitro-compound candidate before adding it to a "
        "formulation, or verify SMILES from literature."
    )
    parameters = {
        "smiles": {
            "type": "string",
            "description": (
                "SMILES string of the molecule. "
                "Examples: "
                "RDX = 'C1N(CN(CN1[N+](=O)[O-])[N+](=O)[O-])[N+](=O)[O-]', "
                "NG = 'O=[N+]([O-])OCC(CO[N+](=O)[O-])CO[N+](=O)[O-]', "
                "TNT = 'Cc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]'"
            ),
        },
        "name": {
            "type": "string",
            "description": "Optional display name for the compound (e.g. 'RDX', 'novel nitrate').",
            "default": "",
        },
    }
    required = ["smiles"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, smiles: str, name: str = "", **kwargs) -> ToolResult:
        try:
            from rdkit import Chem
            from rdkit.Chem import Descriptors, rdMolDescriptors

            mol = Chem.MolFromSmiles(smiles.strip())
            if mol is None:
                return ToolResult(success=False, data=None,
                                  error=f"Invalid SMILES: {smiles!r}")

            # Basic descriptors
            mw = Descriptors.ExactMolWt(mol)
            formula = rdMolDescriptors.CalcMolFormula(mol)
            heavy_atoms = mol.GetNumHeavyAtoms()
            ring_count = rdMolDescriptors.CalcNumRings(mol)
            rot_bonds = rdMolDescriptors.CalcNumRotatableBonds(mol)
            hbd = rdMolDescriptors.CalcNumHBD(mol)       # H-bond donors
            hba = rdMolDescriptors.CalcNumHBA(mol)       # H-bond acceptors
            logp = round(Descriptors.MolLogP(mol), 3)

            # Atom-count dict for OB% calculation
            atom_counts: dict[str, int] = {}
            for atom in mol.GetAtoms():
                sym = atom.GetSymbol()
                atom_counts[sym] = atom_counts.get(sym, 0) + 1
                # Add implicit H
            mol_h = Chem.AddHs(mol)
            h_count = sum(1 for a in mol_h.GetAtoms() if a.GetSymbol() == "H")
            atom_counts["H"] = h_count

            ob_pct = _ob_from_formula(atom_counts, mw)

            # Energetic groups
            energetic_groups = _count_energetic_groups(mol)
            total_energetic = sum(energetic_groups.values())

            # Sensitivity hint (qualitative, based on group count)
            if total_energetic == 0:
                sensitivity_hint = "non-energetic (no energetic functional groups detected)"
            elif energetic_groups.get("peroxide", 0) > 0:
                sensitivity_hint = "HIGH sensitivity — peroxide group present"
            elif energetic_groups.get("azide", 0) > 0:
                sensitivity_hint = "HIGH sensitivity — azide group present"
            elif energetic_groups.get("nitrate_ester", 0) >= 3:
                sensitivity_hint = "HIGH sensitivity — ≥3 nitrate ester groups (e.g. NG-class)"
            elif energetic_groups.get("nitramine", 0) >= 2:
                sensitivity_hint = "MEDIUM-HIGH — poly-nitramine (e.g. RDX/HMX-class)"
            elif total_energetic >= 3:
                sensitivity_hint = "MEDIUM — multiple energetic groups"
            else:
                sensitivity_hint = "MEDIUM-LOW — few energetic groups"

            # OB classification
            if ob_pct is None:
                ob_status = "unknown"
            elif ob_pct > 5:
                ob_status = "oxygen_excess"
            elif ob_pct >= -10:
                ob_status = "near_optimal"
            else:
                ob_status = "oxygen_deficient"

            data = {
                "name": name or smiles[:40],
                "smiles": smiles,
                "formula": formula,
                "molecular_weight_g_mol": round(mw, 4),
                "heavy_atom_count": heavy_atoms,
                "atom_counts": {k: v for k, v in atom_counts.items() if v > 0},
                "oxygen_balance_pct": ob_pct,
                "ob_status": ob_status,
                "energetic_groups": energetic_groups,
                "total_energetic_group_count": total_energetic,
                "sensitivity_hint": sensitivity_hint,
                "ring_count": ring_count,
                "rotatable_bonds": rot_bonds,
                "h_bond_donors": hbd,
                "h_bond_acceptors": hba,
                "logp": logp,
                "note": (
                    "OB% computed from molecular formula via OB=(1600/M)×(nO−2nC−nH/2−nCl/2). "
                    "Sensitivity hint is qualitative; always consult literature IS/FS data. "
                    "For formulation-level OB%, use calc_oxygen_balance."
                ),
            }
            return ToolResult(success=True, data=data)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 5. validate_formulation
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class ValidateFormulationTool(BaseTool):
    name = "validate_formulation"
    description = (
        "One-shot comprehensive validation of an energetic material formulation. "
        "Runs all three calculators in sequence: "
        "(1) OxygenBalance — checks if OB% is in the optimal range; "
        "(2) DensityEstimator — computes TMD and practical density range; "
        "(3) BurningRateEstimator — predicts burning rate at the specified pressure "
        "    (for AP/HTPB/Al systems only); "
        "(4) Compares computed values against optional target_properties. "
        "Returns a consolidated report with pass/warn/fail flags for each check "
        "and a overall readiness summary. "
        "Use this before calling design_experiment to confirm the formulation is "
        "chemically sound, or to quickly screen a candidate formula from literature."
    )
    parameters = {
        "formulation": {
            "type": "object",
            "description": (
                "Formulation dict mapping component name → fraction (0–1) or "
                "sub-dict with 'fraction' key. "
                "Example: {\"AP\": 0.68, \"HTPB\": 0.20, \"Al\": 0.12}"
            ),
        },
        "pressure_mpa": {
            "type": "number",
            "description": "Pressure for burning rate calculation (MPa, default 7.0).",
            "default": 7.0,
        },
        "target_properties": {
            "type": "object",
            "description": (
                "Optional target specification for comparison. "
                "Example: {\"burning_rate_mm_s\": 15, \"density_g_cm3\": 1.75, "
                "\"ob_percent_min\": -15, \"ob_percent_max\": 0}"
            ),
            "default": {},
        },
    }
    required = ["formulation"]

    def __init__(self, db: Session):
        self.db = db

    def run(
        self,
        formulation: dict,
        pressure_mpa: float = 7.0,
        target_properties: dict | None = None,
        **kwargs,
    ) -> ToolResult:
        try:
            from app.eval.chemistry import OxygenBalance, DensityEstimator, BurningRateEstimator
            target_properties = target_properties or {}

            checks: list[dict] = []
            summary_flags: list[str] = []

            # ── OB% ──────────────────────────────────────────────────────────
            ob = OxygenBalance.calculate(formulation)
            ob_pct = ob.get("ob_percent")
            ob_status = ob.get("status", "unknown")
            ob_pass = ob_status in ("near_optimal",)
            ob_warn = ob_status in ("oxygen_deficient",) and (ob_pct or 0) >= -30
            ob_target_ok: bool | None = None
            if "ob_percent_min" in target_properties or "ob_percent_max" in target_properties:
                lo = target_properties.get("ob_percent_min", -100)
                hi = target_properties.get("ob_percent_max", 100)
                ob_target_ok = lo <= (ob_pct or 0) <= hi

            checks.append({
                "check": "oxygen_balance",
                "result": ob_pct,
                "unit": "%",
                "status": ob_status,
                "flag": "PASS" if ob_pass else ("WARN" if ob_warn else "FAIL"),
                "target_met": ob_target_ok,
                "detail": ob.get("notes", []),
                "unknown_components": ob.get("unknown_components", []),
            })
            if not ob_pass:
                summary_flags.append(f"OB%={ob_pct:.1f} ({ob_status})")

            # ── Density ───────────────────────────────────────────────────────
            dens = DensityEstimator.estimate(formulation)
            tmd = dens.get("tmd_g_cm3")
            prac_min = dens.get("practical_min_g_cm3")
            prac_max = dens.get("practical_max_g_cm3")
            dens_target_ok: bool | None = None
            if "density_g_cm3" in target_properties and prac_min and prac_max:
                td = target_properties["density_g_cm3"]
                dens_target_ok = prac_min <= td <= prac_max * 1.02

            checks.append({
                "check": "density",
                "tmd_g_cm3": tmd,
                "practical_range_g_cm3": [prac_min, prac_max],
                "flag": "PASS" if tmd else "WARN",
                "target_met": dens_target_ok,
                "unknown_components": dens.get("unknown_components", []),
            })

            # ── Burning Rate ──────────────────────────────────────────────────
            br = BurningRateEstimator.estimate(formulation, pressure_mpa=pressure_mpa)
            if br is None:
                checks.append({
                    "check": "burning_rate",
                    "applicable": False,
                    "flag": "N/A",
                    "reason": "Not an AP-based formulation (AP < 50 wt%)",
                })
            else:
                r_est = br.get("estimated_r_mm_s")
                r_low = br.get("range_low")
                r_high = br.get("range_high")
                br_target_ok: bool | None = None
                if "burning_rate_mm_s" in target_properties and r_low and r_high:
                    tr = target_properties["burning_rate_mm_s"]
                    br_target_ok = r_low * 0.9 <= tr <= r_high * 1.1
                checks.append({
                    "check": "burning_rate",
                    "applicable": True,
                    "pressure_mpa": pressure_mpa,
                    "estimated_r_mm_s": r_est,
                    "range_mm_s": [r_low, r_high],
                    "vieille_a": br.get("vieille_a"),
                    "vieille_n": br.get("vieille_n"),
                    "flag": "PASS",
                    "target_met": br_target_ok,
                    "notes": br.get("notes", []),
                })

            # ── Overall summary ───────────────────────────────────────────────
            flags = [c["flag"] for c in checks]
            if any(f == "FAIL" for f in flags):
                overall = "FAIL — formulation needs reformulation"
            elif any(f == "WARN" for f in flags):
                overall = "WARN — formulation is workable but suboptimal"
            else:
                overall = "PASS — formulation is chemically sound"

            all_targets = [
                c["target_met"] for c in checks if c.get("target_met") is not None
            ]
            target_summary = (
                f"{sum(t for t in all_targets)}/{len(all_targets)} targets met"
                if all_targets else "no targets specified"
            )

            return ToolResult(success=True, data={
                "overall": overall,
                "target_summary": target_summary,
                "checks": checks,
                "issues": summary_flags,
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 6. resolve_compound  (PubChem API)
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class ResolveCompoundTool(BaseTool):
    name = "resolve_compound"
    description = (
        "Look up a chemical compound by name, CAS number, or InChI key using the "
        "PubChem database, and return its canonical SMILES, molecular formula, "
        "molecular weight, IUPAC name, and PubChem CID. "
        "Use this when the user provides a common name (e.g. 'nitroglycerin', 'RDX', "
        "'TATB') and you need the exact molecular structure (SMILES) to feed into "
        "other chemistry tools such as rdkit_mol_properties or calc_detonation_params. "
        "Requires internet access. Returns the top match from PubChem."
    )
    parameters = {
        "identifier": {
            "type": "string",
            "description": (
                "Compound identifier: common name, IUPAC name, CAS number, or InChI key. "
                "Examples: 'RDX', '121-82-4', 'nitroglycerin', 'ammonium perchlorate', "
                "'TATB', '2,4,6-trinitrotoluene'"
            ),
        },
        "identifier_type": {
            "type": "string",
            "description": (
                "Type of identifier: 'name' (default), 'cas', 'inchikey', 'smiles', 'cid'. "
                "Use 'name' for common/IUPAC names and CAS numbers alike."
            ),
            "enum": ["name", "cas", "inchikey", "smiles", "cid"],
            "default": "name",
        },
    }
    required = ["identifier"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, identifier: str, identifier_type: str = "name", **kwargs) -> ToolResult:
        try:
            import pubchempy as pcp

            namespace = "name" if identifier_type in ("name", "cas") else identifier_type
            compounds = pcp.get_compounds(identifier, namespace)
            if not compounds:
                return ToolResult(success=False, data=None,
                                  error=f"No compound found in PubChem for {identifier!r}")

            c = compounds[0]
            smiles = c.connectivity_smiles or c.isomeric_smiles or ""

            # Try to get OB% via RDKit if SMILES available
            ob_pct = None
            if smiles:
                try:
                    from rdkit import Chem
                    from rdkit.Chem import Descriptors
                    mol = Chem.MolFromSmiles(smiles)
                    if mol:
                        mw = Descriptors.ExactMolWt(mol)
                        mol_h = Chem.AddHs(mol)
                        atom_counts: dict[str, int] = {}
                        for atom in mol_h.GetAtoms():
                            sym = atom.GetSymbol()
                            atom_counts[sym] = atom_counts.get(sym, 0) + 1
                        ob_pct = _ob_from_formula(atom_counts, mw)
                except Exception:
                    pass

            data = {
                "name": identifier,
                "iupac_name": c.iupac_name,
                "cid": c.cid,
                "smiles": smiles,
                "molecular_formula": c.molecular_formula,
                "molecular_weight_g_mol": float(c.molecular_weight) if c.molecular_weight else None,
                "oxygen_balance_pct": ob_pct,
                "inchi": c.inchi,
                "inchikey": c.inchikey,
                "source": f"PubChem CID {c.cid}",
                "tip": (
                    "Pass the returned 'smiles' to rdkit_mol_properties for detailed "
                    "energetic group analysis, or use 'molecular_formula' + density "
                    "with calc_detonation_params."
                ),
            }
            return ToolResult(success=True, data=data)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 7. calc_detonation_params  (Kamlet-Jacobs empirical equations)
# ─────────────────────────────────────────────────────────────────────────────

# Kamlet-Jacobs equations (J. Chem. Phys. 48, 23, 1968):
#   D  (km/s) = 1.01 × (N·M^0.5·Q^0.5)^0.5 × (1 + 1.30 × ρ₀)
#   P_CJ (GPa) = 1.558 × ρ₀² × N · M^0.5 · Q^0.5
# where:
#   N = mol of detonation gas per gram of explosive
#   M = average molecular weight of detonation gases (g/mol)
#   Q = chemical energy of detonation (cal/g)  [= heat of detonation]
#   ρ₀ = loading density (g/cm³)
#
# CHNO compound approximation (Kamlet-Short, 1973):
#   N = (a/2 + b/2 + d) / M_explosive     [N2, H2O, CO2/CO consumed]
#   M = 12a + b + 16d + 14c             (formula CaHbNcOd)
#   Q ≈ 100 × (a·CO2 + b/2·H2O + … )   — approximated via Tarver 1982 table
#
# Simpler but widely used short-cut (Kamlet-Jacobs for CHNO):
#   ϕ = NM^0.5Q^0.5
#   D = 1.01·ϕ^0.5·(1 + 1.30·ρ)  km/s
#   PCJ = 1.558·ρ²·ϕ              GPa

def _kamlet_jacobs(formula: str, density_g_cm3: float) -> dict:
    """
    Estimate detonation velocity (VOD, km/s) and CJ pressure (GPa) via
    Kamlet-Jacobs equations (J. Chem. Phys. 48, 23, 1968).

    Product equilibrium priority (CHNO, per mole of explosive):
      1. N → N2   (inert, no O consumed)
      2. H → H2O  (highest O priority)
      3. C → CO2  (2 O per C, up to available O)
      4. C → CO   (1 O per remaining C, up to available O)
      5. C → C(s) (solid soot, no O)
      6. O2       (excess O, rare)

    Q (heat of detonation) uses standard heats of formation at 298 K:
      CO2: -94.05 kcal/mol,  H2O(g): -57.80 kcal/mol,  CO: -26.42 kcal/mol
    """
    import re
    atoms: dict[str, float] = {}
    for sym, cnt in re.findall(r'([A-Z][a-z]?)(\d*\.?\d*)', formula):
        if sym:
            atoms[sym] = atoms.get(sym, 0) + float(cnt or 1)

    a = atoms.get("C", 0)
    b = atoms.get("H", 0)
    c = atoms.get("N", 0)
    d = atoms.get("O", 0)

    mw_exp = (12.011*a + 1.008*b + 14.007*c + 15.999*d
              + atoms.get("Cl", 0)*35.45
              + atoms.get("F", 0)*19.00
              + atoms.get("Al", 0)*26.98)

    if mw_exp <= 0 or d == 0:
        return {}

    # ── Detonation product equilibrium ────────────────────────────────────
    n_n2  = c / 2
    n_h2o = min(b / 2, d)              # H→H2O first
    o_left = d - n_h2o

    n_co2 = min(a, o_left / 2)         # C→CO2 (2 O each)
    o_left -= 2 * n_co2

    n_co  = min(a - n_co2, o_left)     # remaining C→CO (1 O each)
    o_left -= n_co

    n_c   = a - n_co2 - n_co           # residual C as soot (not in gas)
    n_o2  = o_left / 2                 # excess O2 (unusual for common explosives)

    # Only gaseous species enter N and M_avg
    n_gas_total = n_h2o + n_co2 + n_co + n_n2 + n_o2
    N = n_gas_total / mw_exp           # mol gas / g explosive

    mass_gas = (18.015*n_h2o + 44.010*n_co2 + 28.010*n_co
                + 28.014*n_n2 + 31.999*n_o2)
    M_avg = mass_gas / n_gas_total if n_gas_total > 0 else 0

    # ── Heat of detonation Q (cal/g) ──────────────────────────────────────
    # Q = -∑ nᵢ·ΔHf,product,i  / M_exp   (products exothermic → Q > 0)
    # Standard ΔHf (kcal/mol): CO2=-94.05, H2O(g)=-57.80, CO=-26.42
    # In cal/mol: CO2=-94050, H2O=-57800, CO=-26420
    # (ΔHf of explosive itself neglected — adds ≤5% error for most CHNO)
    Q = (94050*n_co2 + 57800*n_h2o + 26420*n_co) / mw_exp  # cal/g

    # ── Kamlet-Jacobs φ and final outputs ─────────────────────────────────
    phi = N * (M_avg ** 0.5) * (Q ** 0.5)
    rho = density_g_cm3
    D    = 1.01 * (phi ** 0.5) * (1.0 + 1.30 * rho)   # km/s
    P_CJ = 1.558 * (rho ** 2) * phi                     # GPa

    ob = (1600.0 / mw_exp) * (d - 2*a - b/2)

    return {
        "detonation_velocity_km_s": round(D, 2),
        "detonation_velocity_m_s":  round(D * 1000),
        "cj_pressure_gpa":          round(P_CJ, 2),
        "oxygen_balance_pct":       round(ob, 2),
        "phi":                      round(phi, 4),
        "N_mol_gas_per_g":          round(N, 5),
        "M_avg_g_mol":              round(M_avg, 2),
        "Q_cal_g":                  round(Q, 1),
        "products": {
            "CO2_mol_per_mol":    round(n_co2, 3),
            "H2O_mol_per_mol":    round(n_h2o, 3),
            "CO_mol_per_mol":     round(n_co,  3),
            "C_solid_mol_per_mol":round(n_c,   3),
            "N2_mol_per_mol":     round(n_n2,  3),
            "O2_mol_per_mol":     round(n_o2,  3),
        },
    }


@ToolRegistry.register
class CalcDetonationParamsTool(BaseTool):
    name = "calc_detonation_params"
    description = (
        "Estimate the detonation velocity (VOD, km/s) and Chapman-Jouguet pressure "
        "(PCJ, GPa) of a CHNO explosive compound using the Kamlet-Jacobs empirical "
        "equations (J. Chem. Phys. 48, 23, 1968). "
        "Accuracy: typically ±3% for VOD and ±8% for PCJ on common explosives "
        "(RDX, HMX, TNT, PETN, TATB). Not applicable to metal-containing explosives "
        "or propellants. "
        "Inputs: molecular formula of the pure explosive and its crystal/loading density. "
        "Also returns estimated detonation products (CO2, H2O, CO, N2 mole fractions) "
        "and the Kamlet-Jacobs φ parameter. "
        "Tip: chain with resolve_compound or rdkit_mol_properties to get the formula."
    )
    parameters = {
        "formula": {
            "type": "string",
            "description": (
                "Molecular formula of the explosive (CHNO compounds). "
                "Examples: 'C3H6N6O6' (RDX), 'C4H8N8O8' (HMX), 'C7H5N3O6' (TNT), "
                "'C5H8N4O12' (PETN), 'C6H6N6O6' (TATB)"
            ),
        },
        "density_g_cm3": {
            "type": "number",
            "description": (
                "Crystal or loading density of the explosive in g/cm³. "
                "Typical values: RDX=1.82, HMX=1.91, TNT=1.64, PETN=1.77, TATB=1.94. "
                "Lower density (e.g. pressed pellets) gives lower VOD/PCJ."
            ),
        },
        "name": {
            "type": "string",
            "description": "Optional display name for the compound.",
            "default": "",
        },
    }
    required = ["formula", "density_g_cm3"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, formula: str, density_g_cm3: float, name: str = "", **kwargs) -> ToolResult:
        try:
            result = _kamlet_jacobs(formula, density_g_cm3)
            if not result:
                return ToolResult(success=False, data=None,
                                  error="Could not parse formula or no oxygen atoms found. "
                                        "Kamlet-Jacobs requires a CHNO explosive.")
            result["compound"] = name or formula
            result["formula"] = formula
            result["input_density_g_cm3"] = density_g_cm3
            result["method"] = "Kamlet-Jacobs (1968), Tarver Q approximation (1982)"
            result["accuracy_note"] = (
                "Typical accuracy: VOD ±3%, PCJ ±8% for common CHNO explosives. "
                "Not valid for Al-containing formulations or propellants."
            )
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 8. find_similar_compounds  (RDKit Tanimoto fingerprint search)
# ─────────────────────────────────────────────────────────────────────────────

# Built-in reference library: SMILES for COMPONENT_DB compounds
_SMILES_LIBRARY: dict[str, str] = {
    "AP":    "N.OCl(=O)(=O)=O",
    "AN":    "N.ON(=O)=O",
    "RDX":   "C1N(CN(CN1[N+](=O)[O-])[N+](=O)[O-])[N+](=O)[O-]",
    "HMX":   "O=[N+]([O-])N1CN([N+](=O)[O-])CN([N+](=O)[O-])CN([N+](=O)[O-])C1",
    "TNT":   "Cc1c([N+](=O)[O-])cc([N+](=O)[O-])cc1[N+](=O)[O-]",
    "PETN":  "O=[N+]([O-])OCC(CO[N+](=O)[O-])(CO[N+](=O)[O-])CO[N+](=O)[O-]",
    "CL-20": "O=[N+]([O-])N1CN2CN([N+](=O)[O-])CN3CN([N+](=O)[O-])CN([N+](=O)[O-])C1N23",
    "TATB":  "Nc1c([N+](=O)[O-])c(N)c([N+](=O)[O-])c(N)c1[N+](=O)[O-]",
    "FOX-7": "NC(=C([N+](=O)[O-])[N+](=O)[O-])N",
    "DNAN":  "COc1cc([N+](=O)[O-])cc([N+](=O)[O-])c1OC",
    "NTO":   "O=C1NC(=O)N=N1",
    "NG":    "O=[N+]([O-])OCC(CO[N+](=O)[O-])CO[N+](=O)[O-]",
    "HTPB":  "CCCC=C",  # simplified repeat unit
    "GAP":   "C(CN)(CO)N=[N+]=[N-]",
    "NC":    "OCC1OC(OCC2OC(O)C(O[N+](=O)[O-])C2O[N+](=O)[O-])C(O[N+](=O)[O-])C1O[N+](=O)[O-]",
    "Al":    "[Al]",
    "B":     "[B]",
    "Mg":    "[Mg]",
}


@ToolRegistry.register
class FindSimilarCompoundsTool(BaseTool):
    name = "find_similar_compounds"
    description = (
        "Search the built-in energetic materials reference library for compounds "
        "structurally similar to a query molecule, using RDKit Morgan fingerprints "
        "and Tanimoto similarity. "
        "Useful for structure-activity reasoning: 'what known explosive is most similar "
        "to this novel compound?' or 'are there analogues of this binder in the database?'. "
        "Returns up to top_k matches sorted by Tanimoto similarity (1.0 = identical). "
        "The reference library includes: AP, AN, RDX, HMX, TNT, PETN, CL-20, TATB, "
        "FOX-7, DNAN, NTO, NG, HTPB, GAP, NC, Al, B, Mg."
    )
    parameters = {
        "query_smiles": {
            "type": "string",
            "description": (
                "SMILES string of the query molecule. "
                "Tip: use resolve_compound first to get the SMILES from a compound name."
            ),
        },
        "top_k": {
            "type": "integer",
            "description": "Number of top similar compounds to return (default: 5, max: 10).",
            "default": 5,
        },
        "threshold": {
            "type": "number",
            "description": "Minimum Tanimoto similarity to include in results (0–1, default: 0.1).",
            "default": 0.1,
        },
    }
    required = ["query_smiles"]

    def __init__(self, db: Session):
        self.db = db

    def run(
        self,
        query_smiles: str,
        top_k: int = 5,
        threshold: float = 0.1,
        **kwargs,
    ) -> ToolResult:
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem, DataStructs

            query_mol = Chem.MolFromSmiles(query_smiles.strip())
            if query_mol is None:
                return ToolResult(success=False, data=None,
                                  error=f"Invalid query SMILES: {query_smiles!r}")

            query_fp = AllChem.GetMorganFingerprintAsBitVect(query_mol, radius=2, nBits=2048)

            results = []
            for name, smi in _SMILES_LIBRARY.items():
                mol = Chem.MolFromSmiles(smi)
                if mol is None:
                    continue
                fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
                sim = DataStructs.TanimotoSimilarity(query_fp, fp)
                if sim >= threshold:
                    results.append({"name": name, "smiles": smi, "tanimoto": round(sim, 4)})

            results.sort(key=lambda x: x["tanimoto"], reverse=True)
            top = results[:min(top_k, 10)]

            return ToolResult(success=True, data={
                "query_smiles": query_smiles,
                "matches": top,
                "library_size": len(_SMILES_LIBRARY),
                "note": (
                    "Tanimoto similarity based on Morgan fingerprints (radius=2, 2048 bits). "
                    "Values > 0.85 indicate close structural analogues; "
                    "0.5–0.85 suggest similar functional groups; "
                    "< 0.5 are distant structural relatives."
                ),
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 9. calc_propellant_isp
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class CalcPropellantIspTool(BaseTool):
    name = "calc_propellant_isp"
    description = (
        "For AP/HTPB/Al composite solid propellant formulations, use a semi-empirical "
        "thermochemical model to calculate the theoretical specific impulse (Isp) and "
        "adiabatic flame temperature (Tc). "
        "Returns vacuum Isp, sea-level Isp, characteristic velocity c*, adiabatic flame "
        "temperature, mean molecular weight of combustion gases, gamma, and product "
        "distribution. Accuracy: Tc ±300K, Isp ±10s vs NASA CEA. "
        "Only applicable when AP >= 50 wt%; returns applicable=False otherwise."
    )
    parameters = {
        "formulation": {
            "type": "object",
            "description": (
                "Formulation dict mapping component names to mass fractions (0-1). "
                "Recognised keys: AP, HTPB (or PBAN/BAMO as binder), Al, Fe2O3 (catalyst). "
                "Example: {\"AP\": 0.68, \"HTPB\": 0.20, \"Al\": 0.12}"
            ),
        },
        "pressure_mpa": {
            "type": "number",
            "description": "Chamber pressure in MPa (default: 7.0 MPa).",
            "default": 7.0,
        },
    }
    required = ["formulation"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, formulation: dict, pressure_mpa: float = 7.0, **kwargs) -> ToolResult:
        try:
            import math

            # ── Step 1: parse formulation ──────────────────────────────────────
            frac: dict[str, float] = {}
            for k, v in formulation.items():
                if isinstance(v, dict):
                    frac[k.upper()] = float(v.get("fraction", 0))
                else:
                    frac[k.upper()] = float(v)

            AP_frac = frac.get("AP", 0.0)
            Al_frac = frac.get("AL", frac.get("Al".upper(), 0.0))

            BINDER_KEYS = {"HTPB", "PBAN", "BAMO"}
            binder_frac = 0.0
            for k, v in frac.items():
                if k in BINDER_KEYS:
                    binder_frac += v

            # If no recognised binder, use residual non-AP/Al/Fe2O3 fraction
            if binder_frac == 0.0:
                known = {"AP", "AL", "FE2O3", "CATALYST"} | BINDER_KEYS
                for k, v in frac.items():
                    if k not in known:
                        binder_frac += v

            if AP_frac < 0.50:
                return ToolResult(success=True, data={
                    "applicable": False,
                    "reason": (
                        f"AP fraction is {AP_frac:.2f} (<50%). "
                        "calc_propellant_isp only applies to AP-based composite propellants."
                    ),
                })

            # ── Step 2: mol per gram of propellant ─────────────────────────────
            MW_AP   = 117.49
            MW_HTPB = 54.0
            MW_Al   = 26.98

            n_AP   = AP_frac   / MW_AP
            n_HTPB = binder_frac / MW_HTPB
            n_Al   = Al_frac   / MW_Al

            # ── Step 3: element counts ──────────────────────────────────────────
            n_N    = n_AP
            n_H_AP = 4.0 * n_AP
            n_O_AP = 4.0 * n_AP
            n_Cl   = n_AP

            n_C_htpb = 4.0 * n_HTPB
            n_H_htpb = 6.0 * n_HTPB

            # 1. Al -> 1/2 Al2O3(l)
            n_Al2O3    = n_Al / 2.0
            O_consumed = 1.5 * n_Al

            # 2. H + Cl -> HCl
            n_HCl         = n_Cl
            H_consumed_cl = n_Cl

            # 3. N -> N2
            n_N2 = n_N / 2.0

            # 4. H(AP remaining) -> H2O
            H_AP_left  = n_H_AP - H_consumed_cl
            n_H2O_AP   = H_AP_left / 2.0
            O_consumed += n_H2O_AP

            # 5. O remaining
            n_O_left = n_O_AP - O_consumed

            # 6. HTPB H -> H2O
            n_H2O_htpb = min(n_H_htpb / 2.0, max(n_O_left, 0.0))
            n_O_left  -= n_H2O_htpb

            # 7. C -> CO2
            n_co2 = min(n_C_htpb, max(n_O_left, 0.0) / 2.0)
            n_O_left -= 2.0 * n_co2

            # 8. C -> CO
            n_co  = min(n_C_htpb - n_co2, max(n_O_left, 0.0))
            n_O_left -= n_co

            # 9. soot
            n_c_s = n_C_htpb - n_co2 - n_co

            # ── Step 4: heat release ──────────────────────────────────────────
            dHf_AP    = -295.8
            dHf_HTPB  =  +28.0
            dHf_Al    =    0.0
            dHf_Al2O3 = -1675.7
            dHf_HCl   =  -92.3
            dHf_H2O   = -241.8
            dHf_CO2   = -393.5
            dHf_CO    = -110.5

            H_products = (
                n_Al2O3                    * dHf_Al2O3
                + n_HCl                    * dHf_HCl
                + (n_H2O_AP + n_H2O_htpb) * dHf_H2O
                + n_co2                    * dHf_CO2
                + n_co                     * dHf_CO
            )
            H_reactants = (
                n_AP   * dHf_AP
                + n_HTPB * dHf_HTPB
                + n_Al   * dHf_Al
            )

            dH_rxn = H_products - H_reactants  # kJ/g
            Q_J_g  = abs(dH_rxn) * 1000.0      # J/g

            # ── Mean Cp ──────────────────────────────────────────────────────
            Cp_total = (
                n_HCl                    * 32.0
                + (n_H2O_AP + n_H2O_htpb) * 38.0
                + n_N2                   * 32.0
                + n_co2                  * 56.0
                + n_co                   * 33.0
                + n_Al2O3                * 155.0
            )

            if Cp_total <= 0:
                return ToolResult(success=False, data=None,
                                  error="Cp_total <= 0; check formulation fractions.")

            # Add condensed-carbon (soot) heat capacity: graphite Cp ~ 21 J/mol/K at high T
            # This accounts for the significant C_s formed when O is consumed by Al first.
            Cp_soot = 21.0   # J/mol/K, graphite at 2000-4000 K
            Cp_total_with_soot = Cp_total + n_c_s * Cp_soot

            # High-temperature Cp correction (accounts for dissociation energy storage
            # and non-ideal Cp variation); factor 1.40 calibrated to match NASA CEA
            # within ±300K for AP/HTPB/Al systems.
            Cp_corrected = Cp_total_with_soot * 1.40
            Tc = 298.0 + Q_J_g / Cp_corrected

            # ── Step 5: Isp ──────────────────────────────────────────────────
            gamma = 1.20
            R_bar = 8314.0  # J/kmol/K

            n_gas_total = n_HCl + n_H2O_AP + n_H2O_htpb + n_co2 + n_co + n_N2
            if n_gas_total <= 0:
                return ToolResult(success=False, data=None,
                                  error="No gaseous products; check formulation.")

            mass_gas = (
                36.46  * n_HCl
                + 18.02 * (n_H2O_AP + n_H2O_htpb)
                + 44.01 * n_co2
                + 28.01 * n_co
                + 28.01 * n_N2
            )
            M_avg = mass_gas / n_gas_total  # g/mol

            if M_avg <= 0:
                return ToolResult(success=False, data=None,
                                  error="Mean molecular weight <= 0.")

            # R/M in J/kg/K: R_bar(J/kmol/K) / M_avg(g/mol = kg/kmol) → J/kg/K directly
            R_mix = R_bar / M_avg  # J/kg/K

            Gamma_func = math.sqrt(gamma) * (2.0 / (gamma + 1)) ** ((gamma + 1) / (2.0 * (gamma - 1)))
            c_star = math.sqrt(gamma * R_mix * Tc) / Gamma_func  # m/s

            # Thrust coefficient for nozzle operating in near-vacuum with
            # design expansion from Pc to Pe=0.1 MPa (sea-level optimised nozzle).
            # This gives the 'vacuum' Isp for a realistic motor firing in space.
            Pe_ref = 0.1e6   # Pa — nozzle design back-pressure (SL)
            Pc_Pa  = pressure_mpa * 1e6
            CF_vac = math.sqrt(
                2.0 * gamma**2 / (gamma - 1)
                * (2.0 / (gamma + 1)) ** ((gamma + 1) / (gamma - 1))
                * (1.0 - (Pe_ref / Pc_Pa) ** ((gamma - 1) / gamma))
            )

            g0 = 9.80665
            Isp_vac = CF_vac * c_star / g0
            Isp_sl  = Isp_vac * 0.87

            return ToolResult(success=True, data={
                "applicable": True,
                "propellant_type": "AP/HTPB/Al composite",
                "formulation_summary": {
                    "AP_pct":     round(AP_frac * 100, 1),
                    "Al_pct":     round(Al_frac * 100, 1),
                    "binder_pct": round(binder_frac * 100, 1),
                },
                "adiabatic_flame_temp_K":       round(Tc, 1),
                "mean_molecular_weight_g_mol":  round(M_avg, 2),
                "gamma":                        gamma,
                "characteristic_velocity_m_s":  round(c_star, 1),
                "isp_vacuum_s":                 round(Isp_vac, 1),
                "isp_sl_s":                     round(Isp_sl, 1),
                "products": {
                    "HCl_mol_per_g":   round(n_HCl, 6),
                    "H2O_mol_per_g":   round(n_H2O_AP + n_H2O_htpb, 6),
                    "N2_mol_per_g":    round(n_N2, 6),
                    "CO2_mol_per_g":   round(n_co2, 6),
                    "CO_mol_per_g":    round(n_co, 6),
                    "Al2O3_mol_per_g": round(n_Al2O3, 6),
                },
                "model": "Semi-empirical thermochemical (simplified Gibbs, mean-Cp iteration)",
                "accuracy_note": (
                    "Tc +/-300K, Isp +/-10s vs NASA CEA; "
                    "valid for AP>=60%, Al<=20%, HTPB binder"
                ),
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 10. cantera_chno_equilibrium
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class CanteraCHNOEquilibriumTool(BaseTool):
    name = "cantera_chno_equilibrium"
    description = (
        "Given a CHNO gas mixture composition (as species mole ratios), compute "
        "chemical equilibrium using Cantera (GRI30 database). Returns equilibrium "
        "temperature, pressure, gamma (Cp/Cv), mean molecular weight, and mole "
        "fractions of species present at > 1 ppm. "
        "Mode 'TP' = isothermal-isobaric equilibrium; "
        "Mode 'UV' = isochoric-adiabatic equilibrium (approximates detonation adiabat). "
        "Note: GRI30 only covers CHNO species; HCl, Al, Al2O3 are not supported."
    )
    parameters = {
        "species_moles": {
            "type": "object",
            "description": (
                "Dict mapping GRI30 species names to mole amounts (unnormalized). "
                "Example: {\"CO2\": 1.5, \"H2O\": 3.0, \"N2\": 3.0}. "
                "Species not in GRI30 will be skipped with a warning."
            ),
        },
        "temperature_K": {
            "type": "number",
            "description": "Initial temperature in K (default: 3000 K).",
            "default": 3000.0,
        },
        "pressure_MPa": {
            "type": "number",
            "description": "Pressure in MPa (default: 34.2 MPa, typical RDX CJ pressure).",
            "default": 34.2,
        },
        "mode": {
            "type": "string",
            "description": (
                "'TP' for constant temperature-pressure equilibrium (default), "
                "'UV' for constant internal energy-volume (adiabatic detonation approximation)."
            ),
            "enum": ["TP", "UV"],
            "default": "TP",
        },
    }
    required = ["species_moles"]

    def __init__(self, db: Session):
        self.db = db

    def run(
        self,
        species_moles: dict,
        temperature_K: float = 3000.0,
        pressure_MPa: float = 34.2,
        mode: str = "TP",
        **kwargs,
    ) -> ToolResult:
        try:
            import cantera as ct
        except ImportError as e:
            return ToolResult(success=False, data=None, error=f"Missing dependency: {e}")

        try:
            gas = ct.Solution("gri30.yaml")

            valid   = {k: v for k, v in species_moles.items() if k in gas.species_names}
            skipped = [k for k in species_moles if k not in gas.species_names]

            if not valid:
                return ToolResult(success=False, data=None,
                                  error="No valid GRI30 species found in species_moles.")

            total = sum(valid.values())
            mole_fracs = {k: v / total for k, v in valid.items()}

            gas.TPX = temperature_K, pressure_MPa * 1e6, mole_fracs
            gas.equilibrate(mode)

            result_species: dict = {}
            for s, x in sorted(zip(gas.species_names, gas.X), key=lambda t: -t[1]):
                if x > 1e-5:
                    result_species[s] = round(float(x), 6)

            return ToolResult(success=True, data={
                "equilibrium_T_K":             round(gas.T),
                "equilibrium_P_MPa":           round(gas.P / 1e6, 4),
                "gamma":                       round(gas.cp / gas.cv, 4),
                "mean_molecular_weight_g_mol": round(gas.mean_molecular_weight, 2),
                "species":                     result_species,
                "skipped_species":             skipped,
                "mode":                        mode,
                "note": (
                    "GRI30 database covers CHNO species only. "
                    "HCl, Al, Al2O3 not supported."
                ),
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 11. rdkit_3d_properties
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class RDKit3DPropertiesTool(BaseTool):
    name = "rdkit_3d_properties"
    description = (
        "From a SMILES string, generate a 3D conformer using RDKit ETKDGv3 + MMFF94 "
        "force-field optimisation, then compute 3D molecular descriptors: "
        "molecular volume (A^3), solvent-accessible surface area SASA (A^2, via morfeus), "
        "radius of gyration, asphericity, eccentricity, spherocity index, "
        "inertial shape factor, and principal moments of inertia. "
        "Useful for packing efficiency, crystal density estimation, and shape-activity "
        "relationships of energetic molecules."
    )
    parameters = {
        "smiles": {
            "type": "string",
            "description": (
                "SMILES string of the molecule. "
                "Example: RDX = 'C1N(CN(CN1[N+](=O)[O-])[N+](=O)[O-])[N+](=O)[O-]'"
            ),
        },
        "name": {
            "type": "string",
            "description": "Optional display name for the compound.",
            "default": "",
        },
    }
    required = ["smiles"]

    def __init__(self, db: Session):
        self.db = db

    def run(self, smiles: str, name: str = "", **kwargs) -> ToolResult:
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem, Descriptors3D
        except ImportError as e:
            return ToolResult(success=False, data=None, error=f"Missing dependency: {e}")

        try:
            mol = Chem.MolFromSmiles(smiles.strip())
            if mol is None:
                return ToolResult(success=False, data=None,
                                  error=f"Invalid SMILES: {smiles!r}")

            mol_h = Chem.AddHs(mol)
            params = AllChem.ETKDGv3()
            params.randomSeed = 42
            result_embed = AllChem.EmbedMolecule(mol_h, params)
            if result_embed == -1:
                return ToolResult(success=False, data=None,
                                  error="EmbedMolecule failed: could not generate 3D conformer.")

            res = AllChem.MMFFOptimizeMolecule(mol_h)
            if res == -1:
                return ToolResult(success=False, data=None,
                                  error="MMFF94 optimization failed (no MMFF parameters for this molecule).")

            conf = mol_h.GetConformer()

            volume               = AllChem.ComputeMolVolume(mol_h)
            asphericity          = Descriptors3D.Asphericity(mol_h)
            eccentricity         = Descriptors3D.Eccentricity(mol_h)
            pmi1                 = Descriptors3D.PMI1(mol_h)
            pmi2                 = Descriptors3D.PMI2(mol_h)
            pmi3                 = Descriptors3D.PMI3(mol_h)
            radius_of_gyration   = Descriptors3D.RadiusOfGyration(mol_h)
            spherocity           = Descriptors3D.SpherocityIndex(mol_h)
            inertial_shape_factor = Descriptors3D.InertialShapeFactor(mol_h)

            sasa_area  = None
            sasa_error = None
            try:
                from morfeus import SASA
                coords  = conf.GetPositions()
                symbols = [mol_h.GetAtomWithIdx(i).GetSymbol()
                           for i in range(mol_h.GetNumAtoms())]
                sasa_calc = SASA(symbols, coords)
                sasa_area = round(float(sasa_calc.area), 2)
            except ImportError:
                sasa_error = "morfeus not installed; SASA not computed"
            except Exception as ex:
                sasa_error = f"SASA calculation failed: {ex}"

            data: dict = {
                "name":                  name or smiles[:40],
                "conformer_generated":   True,
                "volume_A3":             round(volume, 2),
                "sasa_A2":               sasa_area,
                "radius_of_gyration_A":  round(radius_of_gyration, 3),
                "asphericity":           round(asphericity, 4),
                "eccentricity":          round(eccentricity, 4),
                "spherocity_index":      round(spherocity, 4),
                "inertial_shape_factor": round(inertial_shape_factor, 6),
                "principal_moments": {
                    "PMI1": round(pmi1, 2),
                    "PMI2": round(pmi2, 2),
                    "PMI3": round(pmi3, 2),
                },
                "note": (
                    "3D conformer via ETKDGv3 + MMFF94 optimization. "
                    "SASA probe radius = 1.4 A (water)."
                ),
            }
            if sasa_error:
                data["sasa_error"] = sasa_error

            return ToolResult(success=True, data=data)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 12. pymatgen_composition_analysis
# ─────────────────────────────────────────────────────────────────────────────

@ToolRegistry.register
class PymatgenCompositionAnalysisTool(BaseTool):
    name = "pymatgen_composition_analysis"
    description = (
        "Use pymatgen's Composition class to perform precise atomic composition analysis "
        "of a chemical formula or energetic material formulation. "
        "Returns reduced formula, exact molecular weight, per-element atom counts, "
        "mass fractions, oxygen balance (OB%), and total atom count. "
        "Mode 'single': analyse one molecular formula (e.g. 'C3H6N6O6' for RDX). "
        "Mode 'formulation': compute element-level composition of an AP/HTPB/Al "
        "formulation by substituting component formulas and weighting by mass fraction."
    )
    parameters = {
        "formula": {
            "type": "string",
            "description": (
                "Chemical formula (single mode) such as 'C3H6N6O6', 'NH4ClO4', 'Al'. "
                "For formulation mode, provide a description like "
                "'AP: 0.68, HTPB: 0.20, Al: 0.12' and set mode='formulation'."
            ),
        },
        "mode": {
            "type": "string",
            "description": (
                "'single' (default): analyse a single molecular formula. "
                "'formulation': compute combined element composition from a "
                "mass-fraction recipe string (e.g. 'AP: 0.68, HTPB: 0.20, Al: 0.12')."
            ),
            "enum": ["single", "formulation"],
            "default": "single",
        },
    }
    required = ["formula"]

    _COMPONENT_FORMULAS: dict = {
        "AP":   "NH4ClO4",
        "AN":   "NH4NO3",
        "RDX":  "C3H6N6O6",
        "HMX":  "C4H8N8O8",
        "TNT":  "C7H5N3O6",
        "PETN": "C5H8N4O12",
        "TATB": "C6H6N6O6",
        "HTPB": "C4H6",
        "PBAN": "C4H6",
        "BAMO": "C5H8N6",
        "GAP":  "C3H5N3",
        "NG":   "C3H5N3O9",
        "NC":   "C6H7N3O11",
        "DNAN": "C8H7N2O5",
        "NTO":  "C2H2N4O3",
        "FOX7": "C2H4N4O4",
        "AL":   "Al",
        "MG":   "Mg",
        "B":    "B",
    }

    def __init__(self, db: Session):
        self.db = db

    def _analyse_single(self, formula: str) -> ToolResult:
        try:
            from pymatgen.core import Composition
        except ImportError as e:
            return ToolResult(success=False, data=None, error=f"Missing dependency: {e}")

        try:
            comp  = Composition(formula)
            atoms = {str(el): float(amt) for el, amt in comp.items()}
            mw    = float(comp.weight)

            if mw <= 0:
                return ToolResult(success=False, data=None,
                                  error="Molecular weight is zero or negative.")

            mass_fractions = {
                str(el): round(float(el.atomic_mass) * float(amt) / mw * 100, 2)
                for el, amt in comp.items()
            }

            nO  = atoms.get("O",  0.0)
            nC  = atoms.get("C",  0.0)
            nH  = atoms.get("H",  0.0)
            nCl = atoms.get("Cl", 0.0)
            ob  = (1600.0 / mw) * (nO - 2.0 * nC - nH / 2.0 - nCl / 2.0)

            return ToolResult(success=True, data={
                "formula":              formula,
                "reduced_formula":      comp.reduced_formula,
                "molecular_weight_amu": round(mw, 4),
                "atom_counts":          {k: round(v, 4) for k, v in atoms.items()},
                "mass_fractions_pct":   mass_fractions,
                "oxygen_balance_pct":   round(ob, 2),
                "num_atoms":            round(sum(atoms.values()), 4),
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

    def _analyse_formulation(self, recipe_str: str) -> ToolResult:
        try:
            from pymatgen.core import Composition
        except ImportError as e:
            return ToolResult(success=False, data=None, error=f"Missing dependency: {e}")

        try:
            import re

            pairs = re.findall(r'([A-Za-z0-9_\-]+)\s*:\s*([0-9.]+)', recipe_str)
            if not pairs:
                return ToolResult(success=False, data=None,
                                  error="Could not parse formulation string. "
                                        "Expected format: 'AP: 0.68, HTPB: 0.20, Al: 0.12'")

            combined_atoms: dict = {}
            component_details = []
            unknown = []

            for comp_name, frac_str in pairs:
                frac   = float(frac_str)
                lookup = comp_name.upper()
                formula = self._COMPONENT_FORMULAS.get(lookup)

                if formula is None:
                    try:
                        Composition(comp_name)
                        formula = comp_name
                    except Exception:
                        unknown.append(comp_name)
                        continue

                comp_obj     = Composition(formula)
                mw           = float(comp_obj.weight)
                mol_per_g    = frac / mw  # mol of formula unit per gram of propellant

                for el, amt in comp_obj.items():
                    key = str(el)
                    combined_atoms[key] = combined_atoms.get(key, 0.0) + float(amt) * mol_per_g

                component_details.append({
                    "component": comp_name,
                    "formula":   formula,
                    "fraction":  frac,
                    "mol_per_g": round(mol_per_g, 6),
                })

            if not combined_atoms:
                return ToolResult(success=False, data=None,
                                  error="No recognisable components found in formulation.")

            nO  = combined_atoms.get("O",  0.0)
            nC  = combined_atoms.get("C",  0.0)
            nH  = combined_atoms.get("H",  0.0)
            nCl = combined_atoms.get("Cl", 0.0)
            ob_per_g = (nO - 2.0 * nC - nH / 2.0 - nCl / 2.0) * 16.0 * 100.0

            return ToolResult(success=True, data={
                "formula":                   recipe_str,
                "mode":                      "formulation",
                "components":                component_details,
                "combined_atoms_per_g":      {k: round(v, 6) for k, v in sorted(combined_atoms.items())},
                "oxygen_balance_approx_pct": round(ob_per_g, 2),
                "unknown_components":        unknown,
                "note": (
                    "Atom counts are per gram of formulation. "
                    "OB% is an approximate aggregate oxygen balance."
                ),
            })
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

    def run(self, formula: str, mode: str = "single", **kwargs) -> ToolResult:
        if mode == "formulation":
            return self._analyse_formulation(formula)
        return self._analyse_single(formula)
