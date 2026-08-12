from app.pipeline.domain_extractor import extract_entities_from_text, extract_properties_from_text

SAMPLE_TEXT = """
RDX (Cyclotrimethylenetrinitramine) is an important energetic compound.
The density of RDX was measured as 1.82 g/cm³ under standard conditions.
The detonation velocity of RDX is 8750 m/s.
HTPB/AP/Al propellant showed a burning rate of 8.5 mm/s at 7 MPa.
HMX has a density of 1.91 g/cm³.
"""


def test_extract_entities():
    entities = extract_entities_from_text(SAMPLE_TEXT)
    names = [e.canonical_name for e in entities]
    assert "RDX" in names
    assert "HMX" in names
    assert "HTPB" in names
    assert "AP" in names


def test_extract_density():
    entities = extract_entities_from_text(SAMPLE_TEXT)
    props = extract_properties_from_text(SAMPLE_TEXT, entities)
    density_props = [p for p in props if p.property_name == "density"]
    assert len(density_props) >= 1
    rdx_density = next((p for p in density_props if p.entity_name == "RDX"), None)
    assert rdx_density is not None
    assert abs(rdx_density.value_numeric - 1.82) < 0.01


def test_extract_detonation_velocity():
    entities = extract_entities_from_text(SAMPLE_TEXT)
    props = extract_properties_from_text(SAMPLE_TEXT, entities)
    det_props = [p for p in props if p.property_name == "detonation_velocity"]
    assert len(det_props) >= 1


def test_extract_burning_rate():
    entities = extract_entities_from_text(SAMPLE_TEXT)
    props = extract_properties_from_text(SAMPLE_TEXT, entities)
    br_props = [p for p in props if p.property_name == "burning_rate"]
    assert len(br_props) >= 1
    assert br_props[0].value_numeric == 8.5


def test_evidence_quote_not_empty():
    entities = extract_entities_from_text(SAMPLE_TEXT)
    props = extract_properties_from_text(SAMPLE_TEXT, entities)
    for p in props:
        assert p.evidence_quote


TABLE_TEXT = """Table 1. Performance data.
Compounds | D, m/s | d, g/cm3 | Tp,℃
RDX | 8748 | 1.82 | 204
HMX | 9100 | 1.91 | 280
PETN | 8300 | 1.77 | 141"""


def test_table_entity_extraction():
    from app.pipeline.domain_extractor import _extract_from_table
    results = _extract_from_table(TABLE_TEXT)
    assert len(results) > 0
    entities_found = {r[0] for r in results}
    assert "RDX" in entities_found
    assert "HMX" in entities_found
    assert "PETN" in entities_found


def test_table_property_values():
    from app.pipeline.domain_extractor import _extract_from_table
    results = _extract_from_table(TABLE_TEXT)
    rdx_dv = next((r for r in results if r[0] == "RDX" and r[1] == "detonation_velocity"), None)
    assert rdx_dv is not None
    assert abs(rdx_dv[2] - 8748) < 1

    hmx_density = next((r for r in results if r[0] == "HMX" and r[1] == "density"), None)
    assert hmx_density is not None
    assert abs(hmx_density[2] - 1.91) < 0.01


def test_new_entities_recognized():
    text = "PETN is a powerful explosive. FOX-7 (DADNE) is insensitive. NTO is used in IMX. TKX-50 (HA-BTO) is novel."
    entities = extract_entities_from_text(text)
    names = {e.canonical_name for e in entities}
    assert "PETN" in names
    assert "FOX-7" in names
    assert "NTO" in names
    assert "TKX-50" in names


def test_condition_extraction():
    from app.pipeline.domain_extractor import extract_condition_from_context
    ctx = "The burning rate of RDX at 7 MPa was measured."
    cond = extract_condition_from_context(ctx)
    assert cond is not None
    assert abs(cond["pressure_MPa"] - 7.0) < 0.01


def test_condition_temperature():
    from app.pipeline.domain_extractor import extract_condition_from_context
    ctx = "density measured at 25°C"
    cond = extract_condition_from_context(ctx)
    assert cond is not None
    assert abs(cond["temperature_C"] - 25.0) < 0.01
