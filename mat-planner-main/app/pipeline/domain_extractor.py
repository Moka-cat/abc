import re
from dataclasses import dataclass, field


@dataclass
class ExtractedEntity:
    canonical_name: str
    entity_type: str  # compound, propellant, ingredient, material
    aliases: list[str] = field(default_factory=list)


@dataclass
class ExtractedProperty:
    entity_name: str
    property_name: str
    value_text: str
    value_numeric: float | None
    unit: str | None
    condition: dict | None
    evidence_quote: str
    evidence_offset: int  # char offset in chunk text


# 已知实体注册表（可扩展）
KNOWN_ENTITIES: dict[str, dict] = {
    "RDX": {"type": "compound", "aliases": ["黑索金", "Cyclotrimethylenetrinitramine", "Hexogen"]},
    "HMX": {"type": "compound", "aliases": ["奥克托今", "Octogen", "Cyclotetramethylene-tetranitramine"]},
    "TNT": {"type": "compound", "aliases": ["三硝基甲苯", "Trinitrotoluene"]},
    "PETN": {"type": "compound", "aliases": ["季戊四醇四硝酸酯", "Pentaerythritol tetranitrate"]},
    "TATB": {"type": "compound", "aliases": ["三氨基三硝基苯", "1,3,5-triamino-2,4,6-trinitrobenzene"]},
    "CL-20": {"type": "compound", "aliases": ["HNIW", "Hexanitrohexaazaisowurtzitane", "ε-CL-20"]},
    "FOX-7": {"type": "compound", "aliases": ["DADNE", "1,1-diamino-2,2-dinitroethylene", "DADE"]},
    "NTO": {"type": "compound", "aliases": ["ONTA", "3-nitro-1,2,4-triazol-5-one", "硝基三唑酮"]},
    "TKX-50": {"type": "compound", "aliases": ["dihydroxylammonium 5,5'-bistetrazole-1,1'-diolate", "HA-BTO"]},
    "HTPB": {"type": "ingredient", "aliases": ["端羟基聚丁二烯", "Hydroxyl-terminated polybutadiene"]},
    "IPDI": {"type": "ingredient", "aliases": ["异佛尔酮二异氰酸酯"]},
    "AP": {"type": "ingredient", "aliases": ["高氯酸铵", "Ammonium perchlorate"]},
    "Al": {"type": "ingredient", "aliases": ["铝粉", "aluminum powder", "aluminium"]},
    "AN": {"type": "ingredient", "aliases": ["硝酸铵", "Ammonium nitrate"]},
    "GAP": {"type": "ingredient", "aliases": ["聚叠氮缩水甘油醚"]},
    "DNDA": {"type": "compound", "aliases": []},
    # Additional energetic compounds
    "NG": {"type": "compound", "aliases": ["硝化甘油", "Nitroglycerin", "Glyceryl trinitrate", "GTN"]},
    "NC": {"type": "compound", "aliases": ["硝化纤维素", "Nitrocellulose", "Guncotton"]},
    "DNTF": {"type": "compound", "aliases": ["3,4-二硝基呋咱基氧化呋咱", "3,4-dinitrolurazol-furazan"]},
    "ADN": {"type": "oxidizer", "aliases": ["二硝酰胺铵", "Ammonium dinitramide"]},
    "DNAN": {"type": "compound", "aliases": ["二硝基苯甲醚", "Dinitroanisole", "2,4-dinitroanisole"]},
    "LLM-105": {"type": "compound", "aliases": ["2,6-diamino-3,5-dinitropyrazine-1-oxide", "ANPZO"]},
    "BTF": {"type": "compound", "aliases": ["Benzotrifuroxan", "苯三呋咱", "1,3,5-trinitrosobenzene trifuroxan"]},
    "BNCP": {"type": "compound", "aliases": ["四氨铜(II)高氯酸盐", "tetraammine copper(II) perchlorate"]},
    "NQ": {"type": "compound", "aliases": ["硝基胍", "Nitroguanidine", "picrite"]},
    "Tetryl": {"type": "compound", "aliases": ["特屈儿", "2,4,6-trinitrophenylmethylnitramine", "N-methyl-N,2,4,6-tetranitroaniline"]},
    "DNT": {"type": "compound", "aliases": ["二硝基甲苯", "Dinitrotoluene", "2,4-DNT"]},
    "EGDN": {"type": "compound", "aliases": ["乙二醇二硝酸酯", "Ethylene glycol dinitrate", "nitroglycol"]},
    "TMETN": {"type": "compound", "aliases": ["三羟甲基乙烷三硝酸酯", "Trimethylolethane trinitrate"]},
    "DEGDN": {"type": "compound", "aliases": ["二乙二醇二硝酸酯", "Diethylene glycol dinitrate"]},
    "DIPAM": {"type": "compound", "aliases": ["二苦酰胺", "3,3'-diamino-2,2',4,4',6,6'-hexanitrodiphenylamine"]},
    "DINGU": {"type": "compound", "aliases": ["二硝基甘脲", "Dinitroglycoluril", "DNGU"]},
    "HNF": {"type": "oxidizer", "aliases": ["硝仿肼", "Hydrazinium nitroformate"]},
    "TNB": {"type": "compound", "aliases": ["三硝基苯", "Trinitrobenzene", "1,3,5-trinitrobenzene"]},
    "PA": {"type": "compound", "aliases": ["苦味酸", "Picric acid", "2,4,6-trinitrophenol", "TNP"]},
    "RDX/TNT": {"type": "compound", "aliases": ["Composition B", "Comp B", "cyclotol"]},
    "PBXN-110": {"type": "compound", "aliases": []},
    "PBXN-109": {"type": "compound", "aliases": []},
    "IMX-101": {"type": "compound", "aliases": ["DNAN/NTO/DNAN"]},
    "IMX-104": {"type": "compound", "aliases": []},
    # Additional oxidizers
    "KP": {"type": "oxidizer", "aliases": ["高氯酸钾", "Potassium perchlorate"]},
    "KNO3": {"type": "oxidizer", "aliases": ["硝酸钾", "Potassium nitrate", "saltpeter"]},
    "NaNO3": {"type": "oxidizer", "aliases": ["硝酸钠", "Sodium nitrate", "Chile saltpeter"]},
    # Additional binders
    "PBAN": {"type": "binder", "aliases": ["聚丁二烯-丙烯腈", "Polybutadiene-acrylonitrile"]},
    "CTPB": {"type": "binder", "aliases": ["端羧基聚丁二烯", "Carboxyl-terminated polybutadiene"]},
    "PBAA": {"type": "binder", "aliases": ["聚丁二烯丙烯酸", "Polybutadiene-acrylic acid"]},
    "CAB": {"type": "binder", "aliases": ["醋酸丁酸纤维素", "Cellulose acetate butyrate"]},
    "PEG": {"type": "binder", "aliases": ["聚乙二醇", "Polyethylene glycol"]},
    "Estane": {"type": "binder", "aliases": ["聚氨酯弹性体", "Estane 5703"]},
    "PDMS": {"type": "binder", "aliases": ["聚二甲基硅氧烷", "Polydimethylsiloxane", "silicone rubber"]},
    # Additional plasticizers
    "DOS": {"type": "plasticizer", "aliases": ["癸二酸二辛酯", "Dioctyl sebacate"]},
    "DOA": {"type": "plasticizer", "aliases": ["己二酸二辛酯", "Dioctyl adipate"]},
    "TEGDN": {"type": "plasticizer", "aliases": ["三乙二醇二硝酸酯", "Triethylene glycol dinitrate"]},
    "Bu-NENA": {"type": "plasticizer", "aliases": ["N-丁基硝氧乙基硝胺", "n-butyl-NENA"]},
    "BDNPA/F": {"type": "plasticizer", "aliases": ["双(2,2-二硝基丙基)缩醛/缩甲醛"]},
    "DOP": {"type": "plasticizer", "aliases": ["邻苯二甲酸二辛酯", "Dioctyl phthalate"]},
    # Additional metal fuels
    "Mg": {"type": "fuel", "aliases": ["镁粉", "magnesium powder", "magnesium"]},
    "B": {"type": "fuel", "aliases": ["硼粉", "boron powder", "boron"]},
    "Zr": {"type": "fuel", "aliases": ["锆粉", "zirconium powder", "zirconium"]},
    "Ti": {"type": "fuel", "aliases": ["钛粉", "titanium powder", "titanium"]},
    "W": {"type": "fuel", "aliases": ["钨粉", "tungsten powder", "tungsten"]},
    # Burning rate catalysts
    "Fe2O3": {"type": "catalyst", "aliases": ["氧化铁", "iron oxide", "ferric oxide", "hematite"]},
    "CuO": {"type": "catalyst", "aliases": ["氧化铜", "copper oxide", "cupric oxide"]},
    "Cu2Cr2O5": {"type": "catalyst", "aliases": ["铬酸铜", "copper chromite"]},
    "MnO2": {"type": "catalyst", "aliases": ["二氧化锰", "manganese dioxide"]},
    "Fe3O4": {"type": "catalyst", "aliases": ["四氧化三铁", "magnetite", "ferroferric oxide"]},
}

# 属性-单位模式（可扩展）
PROPERTY_PATTERNS: list[dict] = [
    {
        "property": "density",
        "pattern": r"densit(?:y|ies)[^\d]*?([\d]+\.?[\d]*)\s*(g[/·]cm[³3]|kg[/·]m[³3]|g/cc)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "burning_rate",
        "pattern": r"burning\s+rate[^\d]*?([\d]+\.?[\d]*)\s*(mm[/·]s|cm[/·]s|in[/·]s)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "detonation_velocity",
        "pattern": r"detonation\s+velocit(?:y|ies)[^\d]*?([\d]+\.?[\d]*)\s*(m[/·]s|km[/·]s|fps)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "heat_of_explosion",
        "pattern": r"heat\s+of\s+explosion[^\d]*?([\d]+\.?[\d]*)\s*(kJ[/·]kg|kcal[/·]kg|MJ[/·]kg|cal[/·]g)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "detonation_pressure",
        "pattern": r"detonation\s+pressure[^\d]*?([\d]+\.?[\d]*)\s*(GPa|MPa|kbar|bar)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "melting_point",
        "pattern": r"melting\s+point[^\d]*?([\d]+\.?[\d]*)\s*(°C|°F|K|℃)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "particle_size",
        "pattern": r"particle\s+size[^\d]*?([\d]+\.?[\d]*)\s*(μm|um|nm|mm|micron)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "oxygen_balance",
        "pattern": r"oxygen\s+balance[^\d]*?([+-]?[\d]+\.?[\d]*)\s*(%|percent)",
        "unit_group": 2,
        "value_group": 1,
    },
    # Table-style column patterns (e.g. "d, g/cm3" or "ρ (g cm−3)")
    {
        "property": "density",
        "pattern": r"(?:^|\|)\s*(?:[\d]+\.[\d]+)\s*(?=\s*\||\s*$).*?(?:d,?\s*g[/·]cm[³3]|ρ\s*[,(]?\s*g[/·\s]?cm[−\-]?[³3])",
        "unit_group": 0,
        "value_group": 0,
        "_skip": True,  # complex; handled via alternative below
    },
    # "D, m/s" detonation velocity column  (e.g. "8748 m/s" in table)
    {
        "property": "detonation_velocity",
        "pattern": r"(?:D\s*[,=]\s*|VOD\s*[,=]\s*)([\d]{4,5}(?:\.[\d]+)?)\s*(m[/·]s|m\s*/\s*s)",
        "unit_group": 2,
        "value_group": 1,
    },
    # "d = 1.82 g/cm3" pattern
    {
        "property": "density",
        "pattern": r"(?:d|ρ)\s*[=:]\s*([\d]+\.[\d]+)\s*(g[/·]cm[³3]|g/cc|g\s+cm[−\-]3)",
        "unit_group": 2,
        "value_group": 1,
    },
    # "Tp = 226 °C" decomposition / melting
    {
        "property": "melting_point",
        "pattern": r"(?:Tm|mp|m\.p\.?)\s*[=:]\s*([\d]+(?:\.[\d]+)?)\s*(°C|°F|K|℃)",
        "unit_group": 2,
        "value_group": 1,
    },
    {
        "property": "melting_point",
        "pattern": r"(?:Td|Tp|Tdec)\s*[=:]\s*([\d]+(?:\.[\d]+)?)\s*(°C|°F|K|℃)",
        "unit_group": 2,
        "value_group": 1,
    },
    # Detonation pressure "P = 34.9 GPa"
    {
        "property": "detonation_pressure",
        "pattern": r"(?:P(?:det)?|PCJ)\s*[=:]\s*([\d]+(?:\.[\d]+)?)\s*(GPa|MPa|kbar)",
        "unit_group": 2,
        "value_group": 1,
    },
    # Burning rate in Chinese/mixed context
    {
        "property": "burning_rate",
        "pattern": r"燃速[^\d]*([\d]+\.?[\d]*)\s*(mm[/·]s|cm[/·]s)",
        "unit_group": 2,
        "value_group": 1,
    },
    # Impact sensitivity (drop hammer, J)
    {
        "property": "impact_sensitivity",
        "pattern": r"impact\s+sensitivit(?:y|ies)[^\d]*?([\d]+\.?[\d]*)\s*(J|joule)",
        "unit_group": 2,
        "value_group": 1,
    },
    # Friction sensitivity (Julius Peters, N)
    {
        "property": "friction_sensitivity",
        "pattern": r"friction\s+sensitivit(?:y|ies)[^\d]*?([\d]+\.?[\d]*)\s*(N|newton)",
        "unit_group": 2,
        "value_group": 1,
    },
    # Specific impulse (Isp, s)
    {
        "property": "specific_impulse",
        "pattern": r"specific\s+impulse[^\d]*?([\d]+\.?[\d]*)\s*(s|sec)\b",
        "unit_group": 2,
        "value_group": 1,
    },
    # Burn rate pressure exponent n (dimensionless)
    {
        "property": "burn_rate_exponent",
        "pattern": r"pressure\s+exponent\s*[n=:]\s*([\d]+\.[\d]+)|([\d]+\.[\d]+)\s*(?:is\s+)?(?:the\s+)?pressure\s+exponent",
        "unit_group": 0,
        "value_group": 1,
    },
    # Decomposition temperature (Td, °C) — distinct from melting_point
    {
        "property": "decomposition_temperature",
        "pattern": r"decomposition\s+temperatur[^\d]*?([\d]+\.?[\d]*)\s*(°C|°F|K|℃)",
        "unit_group": 2,
        "value_group": 1,
    },
    # Glass transition temperature (Tg)
    {
        "property": "glass_transition_temperature",
        "pattern": r"glass\s+transition[^\d]*?([-]?[\d]+\.?[\d]*)\s*(°C|°F|K|℃)",
        "unit_group": 2,
        "value_group": 1,
    },
]


def extract_entities_from_text(text: str) -> list[ExtractedEntity]:
    """从文本中识别已知实体"""
    found: dict[str, ExtractedEntity] = {}
    text_lower = text.lower()

    for canonical, info in KNOWN_ENTITIES.items():
        all_names = [canonical] + info["aliases"]
        for name in all_names:
            if name.lower() in text_lower:
                if canonical not in found:
                    found[canonical] = ExtractedEntity(
                        canonical_name=canonical,
                        entity_type=info["type"],
                        aliases=info["aliases"],
                    )
                break

    return list(found.values())


# Header keywords → (property_name, canonical_unit, km_scale)
# km_scale=True means values in column are km/s and should be multiplied ×1000 to store as m/s
_TABLE_COLUMN_MAP: list[tuple[re.Pattern, str, str, bool]] = [
    # Density: header must contain "g" followed (with possible gap) by "cm"
    # Covers: "d, g/cm3"  "ρ (g cm−3)"  "density (g/cm³)"  "d (g·cm-3)"
    (re.compile(r"g[\s/·-]?cm|g\s+cm", re.I), "density", "g/cm³", False),
    # Detonation velocity — km/s variant (values like 8.748 → multiply ×1000)
    # Covers: "D, km/s"  "VOD (km/s)"  "detonation velocity (km/s)"
    (re.compile(r"km[/\s·]?s", re.I), "detonation_velocity", "m/s", True),
    # Detonation velocity — m/s or "m s−1" variants, NOT km/s
    # Covers: "D, m/s"  "D (m s−1)"  "VOD (m/s)"
    (re.compile(r"(?<!k)m[/\s·]?s(?!.*g.*cm)", re.I), "detonation_velocity", "m/s", False),
    (re.compile(r"Pdet|P\s*CJ|detonation.pressure.*GPa|PCJ.*GPa", re.I), "detonation_pressure", "GPa", False),
    # Melting point: require explicit "melting", "Tm", "mp" — not bare "Tp" which is too generic
    (re.compile(r"melting.point|Tm\b|mp\s*[,/°(]", re.I), "melting_point", "°C", False),
    # Decomp temp: Tdec, Td (with word boundary), not "Tp" alone
    (re.compile(r"Tdec\b|Td\b|T_?dec|decomp.*temp", re.I), "melting_point", "°C", False),
    (re.compile(r"burning.rate|burn.rate|燃速", re.I), "burning_rate", "mm/s", False),
    (re.compile(r"heat.of.explos|Qdet|Qexp", re.I), "heat_of_explosion", "kJ/kg", False),
    (re.compile(r"oxygen.balance|OB[,%\s]", re.I), "oxygen_balance", "%", False),
    (re.compile(r"impact.sens|IS\b|h50|drop.hammer", re.I), "impact_sensitivity", "J", False),
    (re.compile(r"friction.sens|FS\b|friction.test", re.I), "friction_sensitivity", "N", False),
    (re.compile(r"specific.impulse|Isp\b|I_sp", re.I), "specific_impulse", "s", False),
    (re.compile(r"decomp.*temp|Tdec\b|T_?d\b", re.I), "decomposition_temperature", "°C", False),
    (re.compile(r"glass.trans|Tg\b|T_?g\b", re.I), "glass_transition_temperature", "°C", False),
]

# Physical plausibility ranges — values outside these are rejected (after unit conversion)
_PLAUSIBILITY: dict[str, tuple[float, float]] = {
    "density":             (0.5,    5.0),    # g/cm³
    "detonation_velocity": (3000.0, 12000.0),# m/s  (after km→m conversion)
    "detonation_pressure": (1.0,    100.0),  # GPa
    "melting_point":       (-200.0, 1500.0), # °C   (wide for organics & metals)
    "burning_rate":        (0.1,    500.0),  # mm/s
    "heat_of_explosion":   (100.0,  25000.0),# kJ/kg
    "oxygen_balance":      (-200.0, 100.0),  # %
    "particle_size":       (0.001,  10000.0),# μm
    "impact_sensitivity":          (0.1,  100.0),   # J
    "friction_sensitivity":        (0.0,  360.0),   # N
    "specific_impulse":            (150.0, 400.0),  # s
    "burn_rate_exponent":          (0.0,   2.0),    # dimensionless
    "decomposition_temperature":   (50.0, 800.0),   # °C
    "glass_transition_temperature":(-200.0, 100.0), # °C
}

# Unit normalization table:
# raw_unit (case-insensitive stripped) → (canonical_unit, multiply, add_offset)
# Applied at extraction time so all stored values use canonical units.
_UNIT_NORM: dict[str, tuple[str, float, float]] = {
    # Density display variants (same value, just display fix)
    "g/cc":      ("g/cm³", 1.0,   0.0),
    "g/cm3":     ("g/cm³", 1.0,   0.0),
    "g·cm⁻³":   ("g/cm³", 1.0,   0.0),
    "g cm⁻³":   ("g/cm³", 1.0,   0.0),
    "g·cm-3":   ("g/cm³", 1.0,   0.0),
    "g cm-3":   ("g/cm³", 1.0,   0.0),
    # Pressure
    "kbar":      ("GPa",   0.1,   0.0),   # 1 kbar = 0.1 GPa
    "gpa":       ("GPa",   1.0,   0.0),   # case fix
    "mpa":       ("GPa",   0.001, 0.0),   # 1 MPa = 0.001 GPa
    # Temperature: Kelvin → Celsius
    "k":         ("°C",    1.0,   -273.15),
    "°k":        ("°C",    1.0,   -273.15),
    # Heat of explosion
    "cal/g":     ("kJ/kg", 4.184, 0.0),   # 1 cal/g = 4.184 kJ/kg
    "kcal/kg":   ("kJ/kg", 4.184, 0.0),   # 1 kcal/kg = 4.184 kJ/kg
    "mj/kg":     ("kJ/kg", 1000.0, 0.0),  # 1 MJ/kg = 1000 kJ/kg
    # Particle size
    "nm":        ("μm",    0.001, 0.0),   # 1 nm = 0.001 μm
    "um":        ("μm",    1.0,   0.0),   # alias
    "mm":        ("μm",    1000.0, 0.0),  # 1 mm = 1000 μm  (for particle size only)
    # Burning rate display fix
    "mm·s":      ("mm/s",  1.0,   0.0),
    "mm·s⁻¹":   ("mm/s",  1.0,   0.0),
    "cm/s":      ("mm/s",  10.0,  0.0),   # 1 cm/s = 10 mm/s
}

# Properties where "mm" unit means particle_size (not burning_rate)
_PARTICLE_SIZE_PROPS = {"particle_size"}


# Condition extraction patterns
_CONDITION_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Pressure: "at 7 MPa", "at 6.89 MPa", "under 10 MPa", "7 MPa pressure"
    ("pressure_MPa", re.compile(
        r"(?:at|under|@)\s*([\d.]+)\s*(MPa|GPa|kPa|atm|psi|bar)\b|"
        r"([\d.]+)\s*(MPa|GPa|kPa|atm|psi|bar)\s+pressure",
        re.IGNORECASE
    )),
    # Temperature: "at 25°C", "at 298 K", "-40°C"
    ("temperature_C", re.compile(
        r"(?:at|@)\s*([+-]?[\d.]+)\s*(°C|°F|K|℃)\b",
        re.IGNORECASE
    )),
    # Initial temperature: "T0 = 294 K"
    ("initial_temperature_K", re.compile(
        r"T[_0]?\s*[=:]\s*([\d.]+)\s*(K|°C)\b",
        re.IGNORECASE
    )),
]

# Unit conversion for conditions
_COND_PRESSURE_TO_MPA: dict[str, float] = {
    "mpa": 1.0, "gpa": 1000.0, "kpa": 0.001,
    "atm": 0.101325, "psi": 0.006895, "bar": 0.1,
}


def extract_condition_from_context(context_window: str) -> dict | None:
    """Extract measurement conditions from the text window around a property value.

    Returns a dict like {"pressure_MPa": 7.0} or {"temperature_C": 25.0}
    or None if no conditions detected.
    """
    conditions: dict = {}
    for key, pat in _CONDITION_PATTERNS:
        m = pat.search(context_window)
        if not m:
            continue
        groups = [g for g in m.groups() if g is not None]
        if len(groups) < 2:
            continue
        try:
            val = float(groups[0])
            unit = groups[1].lower().strip()
        except (ValueError, IndexError):
            continue

        if key == "pressure_MPa":
            factor = _COND_PRESSURE_TO_MPA.get(unit, None)
            if factor:
                conditions["pressure_MPa"] = round(val * factor, 4)
        elif key == "temperature_C":
            if unit in ("°c", "℃"):
                conditions["temperature_C"] = val
            elif unit == "°f":
                conditions["temperature_C"] = round((val - 32) * 5 / 9, 2)
            elif unit == "k":
                conditions["temperature_C"] = round(val - 273.15, 2)
        elif key == "initial_temperature_K":
            if unit == "k":
                conditions["initial_temperature_K"] = val
            elif unit in ("°c", "℃"):
                conditions["initial_temperature_K"] = round(val + 273.15, 2)

    return conditions if conditions else None


def normalize_unit(
    property_name: str, value: float, raw_unit: str
) -> tuple[float, str]:
    """Convert value+unit to canonical form.

    Returns (converted_value, canonical_unit).
    If no conversion needed, returns (value, raw_unit) unchanged.

    Special case: "mm" is only converted for particle_size (1mm = 1000μm).
    For burning_rate, mm/s is already canonical so "mm" alone is skipped.
    """
    key = raw_unit.strip().lower()
    # Skip "mm" conversion for non-particle-size properties (mm/s is already fine)
    if key == "mm" and property_name not in _PARTICLE_SIZE_PROPS:
        return value, raw_unit
    if key not in _UNIT_NORM:
        return value, raw_unit
    canonical_unit, factor, offset = _UNIT_NORM[key]
    converted = value * factor + offset
    return converted, canonical_unit


def _extract_from_table(text: str) -> list[tuple[str, str, float | None, str, str]]:
    """
    Extract (entity_name, property_name, value_numeric, unit, quote) from
    pipe-delimited table chunks emitted by MinerUJsonParser / MarkdownParser.

    Returns list of (entity_name, property_name, value_numeric, unit, quote).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []

    # Find the header row (first pipe-delimited row with ≥2 non-empty cells)
    header_row: list[str] = []
    data_start = 0
    for i, line in enumerate(lines):
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) >= 2:
                header_row = cells
                data_start = i + 1
                break

    if not header_row:
        return []

    # Map column index → (property_name, canonical_unit, km_scale)
    col_props: dict[int, tuple[str, str, bool]] = {}
    for col_idx, header in enumerate(header_row):
        for pattern, prop_name, unit, km_scale in _TABLE_COLUMN_MAP:
            if pattern.search(header):
                col_props[col_idx] = (prop_name, unit, km_scale)
                break

    if not col_props:
        return []

    # Entity column: first column, but skip separator/alignment rows
    entity_col = 0

    results = []
    for line in lines[data_start:]:
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        # Skip markdown separator rows (---|---)
        if all(re.match(r"^[-:]+$", c) for c in cells if c):
            continue
        # Pad cells to header length
        while len(cells) < len(header_row):
            cells.append("")

        # Entity name from first column — must be a strict match (exact or alias)
        entity_raw = cells[entity_col] if cells else ""
        if not entity_raw:
            continue

        entity_name = _resolve_entity_strict(entity_raw)
        if entity_name is None:
            continue

        # Extract property values from mapped columns
        for col_idx, (prop_name, unit, km_scale) in col_props.items():
            if col_idx >= len(cells):
                continue
            val_str = cells[col_idx].replace(",", ".").strip()
            if not val_str or val_str in ("-", "—", "N/A", "n/a", "–", "···", ""):
                continue
            # Strip leading ">" or "~" (e.g. ">320")
            val_str = re.sub(r"^[>~≈<≤≥]+", "", val_str).strip()
            # Allow plain numeric optionally followed by unit text
            m = re.match(r"^([+-]?[\d]+\.?[\d]*)(?:\s*[a-zA-Z°/·³µ%\-]*)?$", val_str)
            if not m:
                continue
            try:
                value_numeric = float(m.group(1))
            except ValueError:
                continue

            # Convert km/s → m/s if column was labelled in km/s
            if km_scale:
                value_numeric *= 1000.0

            # Unit normalization: convert to canonical unit (kbar→GPa, K→°C, etc.)
            value_numeric, unit = normalize_unit(prop_name, value_numeric, unit)

            # Plausibility gate: reject physically impossible values
            lo, hi = _PLAUSIBILITY.get(prop_name, (-1e18, 1e18))
            if not (lo <= value_numeric <= hi):
                continue

            results.append((entity_name, prop_name, value_numeric, unit, line[:300]))

    return results


def _resolve_entity_strict(cell: str) -> str | None:
    """
    Return canonical entity name if `cell` is an exact or known-alias match.
    Uses strict matching: canonical name or alias must equal the cell exactly
    (case-insensitive) OR the cell must START with the canonical name followed
    by a non-alpha character (handles "RDX (hexogen)" etc.).
    Rejects partial substring matches to avoid "Al" matching "Al₂O₃" or "TATB".
    """
    cell_stripped = cell.strip()
    cell_lower = cell_stripped.lower()
    for canonical, info in KNOWN_ENTITIES.items():
        all_names = [canonical] + info["aliases"]
        for name in all_names:
            name_lower = name.lower()
            # Exact match
            if name_lower == cell_lower:
                return canonical
            # Cell starts with name + non-alphanumeric separator only
            # "RDX (hexogen)" → RDX  ✓
            # "Al2O3"         → None ✗  (digit follows, so skip)
            if cell_lower.startswith(name_lower) and len(cell_lower) > len(name_lower):
                next_char = cell_lower[len(name_lower)]
                if not next_char.isalnum():   # space, comma, (, / etc.
                    return canonical
    return None


def extract_properties_from_text(text: str, entities: list[ExtractedEntity]) -> list[ExtractedProperty]:
    """从文本中提取属性值，与已识别实体关联。支持 prose 和 table 格式。"""
    results: list[ExtractedProperty] = []

    # ── Table-aware extraction (pipe-delimited rows) ──────────────────────
    if "|" in text and len([ln for ln in text.splitlines() if "|" in ln]) >= 2:
        for entity_name, prop_name, value_numeric, unit, quote in _extract_from_table(text):
            results.append(ExtractedProperty(
                entity_name=entity_name,
                property_name=prop_name,
                value_text=f"{value_numeric} {unit}",
                value_numeric=value_numeric,
                unit=unit,
                condition=None,
                evidence_quote=quote,
                evidence_offset=0,
            ))

    # ── Prose regex extraction ─────────────────────────────────────────────
    for prop_info in PROPERTY_PATTERNS:
        if prop_info.get("_skip"):
            continue
        pattern = re.compile(prop_info["pattern"], re.IGNORECASE)
        for m in pattern.finditer(text):
            value_str = m.group(prop_info["value_group"])
            unit = m.group(prop_info["unit_group"])
            try:
                value_numeric = float(value_str)
            except ValueError:
                value_numeric = None

            # 在匹配前后 200 字符内找到实体名
            start = max(0, m.start() - 200)
            end = min(len(text), m.end() + 200)
            context = text[start:end]

            linked_entity = None
            for entity in entities:
                all_names = [entity.canonical_name] + entity.aliases
                for name in all_names:
                    if name.lower() in context.lower():
                        linked_entity = entity.canonical_name
                        break
                if linked_entity:
                    break

            if linked_entity is None and entities:
                linked_entity = entities[0].canonical_name  # 默认关联第一个实体

            if linked_entity:
                # Unit normalization: convert to canonical unit
                prop_name = prop_info["property"]
                if value_numeric is not None and unit:
                    value_numeric, unit = normalize_unit(prop_name, value_numeric, unit)

                # Plausibility gate (prose extraction)
                if value_numeric is not None:
                    lo, hi = _PLAUSIBILITY.get(prop_name, (-1e18, 1e18))
                    if not (lo <= value_numeric <= hi):
                        continue

                # Extract measurement conditions from context window
                condition = extract_condition_from_context(context)

                # 提取原文引用（匹配行）
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                quote = text[line_start:line_end if line_end != -1 else len(text)].strip()

                results.append(ExtractedProperty(
                    entity_name=linked_entity,
                    property_name=prop_name,
                    value_text=f"{value_numeric if value_numeric is not None else value_str} {unit}",
                    value_numeric=value_numeric,
                    unit=unit,
                    condition=condition,
                    evidence_quote=quote,
                    evidence_offset=m.start(),
                ))

    return results
