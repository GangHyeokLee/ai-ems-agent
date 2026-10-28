"""Fixed two-bus passive samples; no original Mongolia files required."""
import pytest

pypsa = pytest.importorskip("pypsa", reason="Stage 2B samples require the separate build environment")
import numpy as np
import pandas as pd
from pipelines.mn.mn35k_refinement import build_model
from pipelines.mn.validate_mn35k_poc import validate_model
from pipelines.mn.build_mn35k_poc import compare_reference


@pytest.fixture
def sample(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("No PF/LPF or slack discovery in a passive sample")
    for cls in (pypsa.Network, pypsa.SubNetwork):
        for name in ("pf", "lpf"):
            monkeypatch.setattr(cls, name, forbidden)
    monkeypatch.setattr(pypsa.SubNetwork, "find_bus_controls", forbidden)
    n = pypsa.Network()
    n.set_snapshots([pd.Timestamp("2013-01-01")])
    for bus in ("a", "b"):
        n.add("Bus", bus, v_nom=35.)
    n.add("Line", "ab", bus0="a", bus1="b", v_nom=35., length=1., type="243-AL1/39-ST1A 110.0", s_nom=100.)
    config = dict(required_pypsa_version="1.2.4", schema_version=1, input_model="sample.nc", protected_models={"sample.nc":"0"*64},
                  line_35kv={"reference":dict(r20_ohm_per_km=.194, diameter_mm=17.1, adjacent_spacing_m=1.8, temperature_c=50., i_nom_ka=.47, cross_section_mm2=150)},
                  line_common=dict(frequency_hz=50., resistance_alpha_per_k=.00403, epsilon0_f_per_m=8.8541878128e-12, gmr_radius_factor=.7788007830714049),
                  transformers={}, transformer_common=dict(tap_ratio=1., phase_shift_deg=0.), type_voltage_kv={}, screening_limits={},
                  expected_structure=dict(buses=2, lines=1, transformers=0, generators=0, loads=0, component_sizes=[2], isolated_buses=[], line_voltage_counts={"35":1}))
    lengths = pd.DataFrame(dict(length_before_km=[1.], length_after_km=[1.], correction_applied=[False], status=["UNRESOLVED"]), index=["ab"])
    return n, build_model(n, config, lengths), config


def test_two_bus_ybus_matches_hand_pi_equivalent(sample):
    source, model, config = sample
    matrices = {}
    result = validate_model(model, source, config, matrices=matrices)
    assert result["valid"]
    assert result == validate_model(model, source, config)
    row = model.lines.loc["ab"]
    series, shunt = 1/(row.r_pu+1j*row.x_pu), 1j*row.b_pu/2
    expected = np.array([[series+shunt, -series], [-series, series+shunt]])
    np.testing.assert_allclose(matrices["poc_ybus_0"][1].toarray(), expected, rtol=1e-12, atol=1e-14)
    assert model.buses.control.eq("PQ").all()


@pytest.mark.parametrize("field,value", [("r", 0.), ("x", -1.), ("b", -1.), ("bus1", "missing"), ("length", float("nan"))])
def test_small_invalid_branch_never_reaches_ybus(sample, field, value):
    source, model, config = sample
    model.lines.loc["ab", field] = value
    result = validate_model(model, source, config)
    assert not result["valid"] and result["ybus"] == []


def test_reference_comparison_does_not_widen_tolerance(sample):
    source, model, config = sample
    changed = model.copy()
    changed.lines.loc["ab", "r"] *= 1+1e-13
    with pytest.raises(AssertionError):
        compare_reference(changed, model, source, config)
