from app.pipeline.formulation_extractor import extract_formulations


def test_slash_pattern():
    text = "The composite propellant AP/HTPB/Al = 68/20/12 wt% was prepared."
    results = extract_formulations(text)
    assert len(results) == 1
    f = results[0]
    assert f.formulation_type == "composite_propellant"
    names = {c.component_name for c in f.components}
    assert "AP" in names
    assert "HTPB" in names
    assert "Al" in names


def test_wt_percent_pattern():
    text = "A formulation containing 78 wt% RDX, 18 wt% HTPB, and 4 wt% DOS was tested."
    results = extract_formulations(text)
    assert len(results) == 1
    names = {c.component_name for c in results[0].components}
    assert "RDX" in names
    assert "HTPB" in names


def test_inline_pattern():
    text = "The mixture AP(68%)/HTPB(20%)/Al(12%) showed improved performance."
    results = extract_formulations(text)
    assert len(results) == 1
    names = {c.component_name for c in results[0].components}
    assert "AP" in names


def test_unknown_entity_rejected():
    """Slash pattern should reject if any component is unknown."""
    text = "XYZ/HTPB/Al = 70/20/10 wt%"
    results = extract_formulations(text)
    # XYZ is not a known entity, so pattern A should fail
    # But HTPB and Al appear in wt% pattern? Actually no - need 2+ consecutive wt% items
    # So result should be empty
    assert len(results) == 0


def test_dedup():
    """Same formulation mentioned twice should appear only once."""
    text = ("AP/HTPB/Al = 68/20/12 wt% was used. "
            "The AP/HTPB/Al = 68/20/12 propellant was characterized.")
    results = extract_formulations(text)
    assert len(results) == 1


def test_formulation_type_plastic_explosive():
    text = "RDX/HTPB/DOS = 78/18/4 wt% composition was evaluated."
    results = extract_formulations(text)
    assert len(results) == 1
    assert results[0].formulation_type == "plastic_explosive"
