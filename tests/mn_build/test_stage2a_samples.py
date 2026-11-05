"""Small fixed samples; no real Mongolia data required (run in build/convert venv)."""
import math
from types import SimpleNamespace

import pytest

pypsa = pytest.importorskip("pypsa", reason="Stage 2A fixed samples require the separate build environment")
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, MultiLineString

from pipelines.mn import audit_mn35k_parameters as audit, mn35k_lengths as lengths
from pipelines.mn import mn35k_refinement as refine, mn35k_powsybl as conv


PROFILE = dict(r20_ohm_per_km=.194, diameter_mm=17.1, adjacent_spacing_m=1.8,
               temperature_c=50., i_nom_ka=.47, cross_section_mm2=150)
COMMON = dict(frequency_hz=50., resistance_alpha_per_k=.00403,
              epsilon0_f_per_m=8.8541878128e-12, gmr_radius_factor=.7788007830714049)
TRAFO = dict(tap_ratio=1., tap_side=0, phase_shift_deg=0., g_pu=0., b_pu=0., model="t")


def test_distance_units_and_projection():
    assert audit.geodesic_length_km(LineString([(0, 0), (1, 0)])) == pytest.approx(111.319490793, rel=1e-9)
    geometry = LineString([(100, 48), (101, 48)])
    assert 74 < audit.geodesic_length_km(geometry) < 75
    assert lengths._metric(geometry).length / 1000 > 1.49 * audit.geodesic_length_km(geometry)
    assert audit.haversine_m(0, 0, 1, 0) == pytest.approx(111195.0802335329)


@pytest.mark.parametrize("value,expected", [("10-1_0", "10"), ("50_2", "50"), (123, "123"), (None, ""), (float("nan"), "")])
def test_source_identifiers(value, expected):
    assert audit.source_way_id(value) == expected


@pytest.mark.parametrize("geometry,issue", [
    (MultiLineString([[(100, 48), (100.5, 48)], [(100.6, 48), (101, 48)]]), "disconnected_geometry"),
    (LineString([(100, 48), (100.5, 48.1), (101, 48), (100, 48)]), "branched_or_looped_geometry"),
    (LineString([(100, 48), (100.5, 48), (101, 48), (100, 48)]), "self_intersecting_geometry"),
    (MultiLineString([[(100, 48), (100.5, 48)], [(100.5, 48), (101, 48)], [(100.5, 48), (100.5, 48.1)]]), "branched_or_looped_geometry"),
])
def test_invalid_chains_are_not_repaired(geometry, issue):
    result = lengths.inspect_chain_geometry(geometry, (100, 48), (101, 48))
    assert not result["geometry_simple_chain"]
    assert result["geometry_issue"] == issue


def test_numerical_slivers_and_connector_units():
    geometry = MultiLineString([[(100, 48), (100.5, 48)], [(100.5+1e-13, 48), (101, 48)],
                                [(100.5, 48), (100.5+1e-13, 48)]])
    check = lengths.inspect_chain_geometry(geometry, (101, 48), (100, 48))
    assert check["endpoint_match"] and check["geometry_simple_chain"]
    assert check["topology_ignored_fragment_km"] < 1e-8
    clean = LineString([(100, 48), (101, 48)])
    final = LineString([(99.99, 48), (100, 48), (101, 48), (101.01, 48)])
    parts = lengths.decompose_route_length(final, clean)
    assert parts["clean_route_geodesic_km"] == pytest.approx(audit.geodesic_length_km(clean))
    assert parts["virtual_connector_geodesic_km"] > 1
    assert sum(parts.values()) == pytest.approx(audit.geodesic_length_km(final), abs=1e-9)


def test_line_and_transformer_units_are_unchanged():
    line = refine.line_type_parameters(PROFILE, COMMON)
    assert line["r_per_length"] == pytest.approx(.2174546)
    assert line["x_per_length"] == pytest.approx(.36635115490919057)
    assert line["c_per_length"] == pytest.approx(9.9688039680739)
    t = refine.transformer_parameters(dict(s_nom_mva=25., uk_percent=10.5, load_loss_kw=105.), TRAFO)
    assert t["r"] == pytest.approx(.0042)
    assert t["x"] == pytest.approx(.10491596637309308)
    assert t["r"] * t["s_nom"] * 1000 == pytest.approx(105.)
    assert math.hypot(t["r"], t["x"]) == pytest.approx(.105)


@pytest.mark.parametrize("field,value", [("diameter_mm", 0), ("r20_ohm_per_km", float("nan")), ("adjacent_spacing_m", .001)])
def test_invalid_line_values_still_fail(field, value):
    profile = {**PROFILE, field: value}
    with pytest.raises(ValueError):
        refine.line_type_parameters(profile, COMMON)


@pytest.mark.parametrize("field,value", [("s_nom_mva", 0), ("load_loss_kw", 3000), ("uk_percent", float("inf"))])
def test_invalid_transformer_values_still_fail(field, value):
    profile = dict(s_nom_mva=25., uk_percent=10.5, load_loss_kw=105.)
    profile[field] = value
    with pytest.raises(ValueError):
        refine.transformer_parameters(profile, TRAFO)


def passive_sample():
    n = pypsa.Network()
    n.set_snapshots([pd.Timestamp("2013-01-01")])
    for bus, voltage in (("1", 35), ("2", 35), ("3", 110)):
        n.add("Bus", bus, v_nom=voltage)
    n.add("Line", "L12", bus0="1", bus1="2", v_nom=35., length=1., type="243-AL1/39-ST1A 110.0", s_nom=100.)
    n.add("Transformer", "T23", bus0="2", bus1="3", r=0., x=.1, s_nom=2000.)
    config = dict(required_pypsa_version="1.2.4", schema_version=1, input_model="sample.nc",
                  protected_models={"sample.nc":"0"*64}, line_35kv={"reference":PROFILE}, line_common=COMMON,
                  transformers={"35_110":{"reference":dict(s_nom_mva=25., uk_percent=10.5, load_loss_kw=105.)}}, transformer_common=TRAFO)
    table = pd.DataFrame(dict(length_before_km=[1.], length_after_km=[1.], correction_applied=[False], status=["UNRESOLVED"]), index=["L12"])
    return n, config, table


def test_small_passive_copy_preserves_inputs_and_own_mva_base():
    n, config, table = passive_sample()
    before = n.lines.copy(deep=True)
    result = refine.build_model(n, config, table)
    pd.testing.assert_frame_equal(n.lines, before)
    assert result is not n
    t = result.transformers.loc["T23"]
    assert t.r_pu == pytest.approx(t.r / 25.)
    assert t.x_pu == pytest.approx(t.x / 25.)
    assert not len(result.generators) and not len(result.loads)
    assert result.meta["poc_refinement"]["ac_power_flow_executed"] is False


def test_small_exports_are_deterministic_and_keep_derived_fields(sample_paths):
    n, config, table = passive_sample()
    result = refine.build_model(n, config, table)
    first, second = (sample_paths.output_dir / name for name in ("a.nc", "b.nc"))
    refine.export_deterministic(result, first, paths=sample_paths)
    refine.export_deterministic(result, second, paths=sample_paths)
    assert first.read_bytes() == second.read_bytes()
    restored = pypsa.Network(first)
    for name in ("lines", "transformers"):
        for field in ("r", "x", "b", "g", "r_pu", "x_pu", "g_pu", "b_pu"):
            np.testing.assert_array_equal(getattr(restored, name)[field], getattr(result, name)[field])
    with pytest.raises(ValueError, match="overwrite"):
        refine.export_deterministic(result, first, paths=sample_paths)


def test_audit_paths_do_not_assume_earth_is_inside_source(sample_paths):
    inputs = audit.AuditInputs.from_paths(sample_paths)
    assert inputs.network == sample_paths.earth_checkout / "networks/MN_35k/base.nc"
    assert inputs.plant_mapping_csv == sample_paths.source_project / "reports/expansion/mn_35k_plant_bus_mapping.csv"
    assert not inputs.network.is_relative_to(sample_paths.source_project)


@pytest.fixture
def synthetic_line_inputs(monkeypatch):
    geometry = LineString([(100, 48), (100.5, 48), (101, 48)])
    record = dict(line_id="10-1_0", bus0="a", bus1="b", voltage=35000,
                  length=120000., geometry=geometry)
    data = dict(build=gpd.GeoDataFrame([record], crs=4326),
                csv=pd.DataFrame([{**record, "geometry":geometry.wkt}]),
                clean=gpd.GeoDataFrame([dict(line_id="10-1", voltage=35000, geometry=geometry)], crs=4326),
                raw=gpd.GeoDataFrame([{"id":"10", "tags.voltage":"35000", "geometry":geometry}], crs=4326))
    monkeypatch.setattr(lengths, "_inputs", lambda paths: data)
    network = SimpleNamespace(
        lines=pd.DataFrame([dict(bus0="a", bus1="b", length=120., v_nom=35., geometry=geometry.wkt)], index=["10-1_0"]),
        buses=pd.DataFrame(dict(x=[100., 101.], y=[48., 48.]), index=["a", "b"]))
    return data, network


def test_exact_id_length_audit_preserves_input_and_metre_conversion(synthetic_line_inputs, sample_paths):
    _, n = synthetic_line_inputs
    before = n.lines.copy(deep=True)
    row = lengths.audit_line_lengths(n, sample_paths).loc["10-1_0"]
    assert row.status == "GEOMETRY_DERIVED" and row.correction_applied
    assert 74 < row.length_after_km < 75
    assert row.build_stored_km == 120.
    assert not row.length_physical_verified
    pd.testing.assert_frame_equal(n.lines, before, check_exact=True)


@pytest.mark.parametrize("problem", ["missing_id", "duplicate_id", "endpoint", "geometry", "units", "voltage", "invalid_wkt"])
def test_ambiguous_length_inputs_keep_original_value(synthetic_line_inputs, sample_paths, problem):
    data, n = synthetic_line_inputs
    if problem == "missing_id":
        data["build"]["line_id"] = "other"
    elif problem == "duplicate_id":
        data["csv"] = pd.concat([data["csv"], data["csv"]])
    elif problem == "endpoint":
        n.buses.loc["b", "x"] = 102.
    elif problem == "geometry":
        n.lines.loc["10-1_0", "geometry"] = "LINESTRING (100 48, 102 48)"
    elif problem == "units":
        data["csv"]["length"] = 120.
    elif problem == "voltage":
        data["clean"]["voltage"] = 110000
    else:
        n.lines.loc["10-1_0", "geometry"] = "bad geometry"
    row = lengths.audit_line_lengths(n, sample_paths).loc["10-1_0"]
    assert row.status == "UNRESOLVED" and row.unresolved_reason
    assert not row.correction_applied
    assert row.length_before_km == row.length_after_km == 120.


def test_length_summary_uses_stored_metres_not_geodataframe_length(synthetic_line_inputs, sample_paths):
    _, n = synthetic_line_inputs
    rows = lengths.summarize_length_stages(n, sample_paths)
    row = rows.loc[rows.stage.eq("Network build geometry")].iloc[0]
    assert row.stored_length_km == 120.
    assert 74 < row.geometry_geodesic_km < 75


def operating_sample():
    n = pypsa.Network()
    n.set_snapshots([pd.Timestamp("2013-01-01")])
    for bus, voltage in (("A", 220.), ("B", 220.), ("C", 110.)):
        n.add("Bus", bus, v_nom=voltage)
    n.add("Line", "AB", bus0="A", bus1="B", r=7.302, x=36.633, b=.001912, s_nom=983.)
    n.add("Transformer", "TBC", bus0="B", bus1="C", r=.0026, x=.12, s_nom=100., model="t")
    n.add("Generator", "GA", bus="A", control="Slack", p_set=1., p_nom=100.)
    n.add("Generator", "GB", bus="B", control="PV", p_set=5., p_nom=100.)
    n.add("Load", "LC", bus="C", p_set=8., q_set=2.6)
    n.add("ShuntImpedance", "RB", bus="B", g=0., b=-20./220**2, sign=-1.)
    assert n.pf(x_tol=1e-10, distribute_slack=False, use_seed=False)["converged"].values.all()
    return n


def test_tiny_conversion_units_ids_and_pf_agree():
    pytest.importorskip("pypowsybl", reason="Two-engine sample requires separate conversion environment")
    n = operating_sample()
    model = conv.extract_model(n, q_limits=(-100., 100.))
    net = conv.build_network(model)
    line = net.get_lines().loc["L_AB"]
    assert line.r == 7.302 and line.x == 36.633
    assert line.b1 == line.b2 == .001912/2
    t = net.get_2_windings_transformers().loc["T_TBC"]
    assert t.r == pytest.approx(.0026 * 110**2 / 100)
    assert t.x == pytest.approx(.12 * 110**2 / 100)
    assert model.shunts.q_absorbed_at_1pu_mvar.iloc[0] == 20.
    assert not model.mapping.iidm_id.duplicated().any()
    lf = conv.run_ac(net, "VL_A", conv_eps_per_eq=1e-12)
    assert lf["converged"]
    reference = conv.pypsa_results(n)
    actual = conv.powsybl_results(net, model.mapping, "GA", lf["slack_active_power_mismatch_mw"])
    for component, key, fields in (("buses", "bus_id", ["v_mag_pu", "v_ang_deg"]),
                                   ("lines", "line_id", ["p0", "q0", "p1", "q1"]),
                                   ("transformers", "transformer_id", ["p0", "q0", "p1", "q1"]),
                                   ("generators", "generator_id", ["p_mw", "q_mvar"]),
                                   ("shunts", "shunt_id", ["q_absorbed_mvar"])):
        frame = conv.compare(reference[component], actual[component], key, fields)
        assert frame.comparable.all()
        for field in fields:
            assert conv.error_stats(frame, key, field)["max_abs_error"] < 1e-7
