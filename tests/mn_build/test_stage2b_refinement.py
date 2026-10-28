"""Physical, preservation and failure-path tests for the passive PoC model.

These tests deliberately prohibit PF/LPF. They use the existing MN_35k input
read-only and export only into pytest temporary directories.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pypsa = pytest.importorskip("pypsa", reason="Stage 2B validator parity requires the separate build environment")
from dataclasses import replace
from pipelines.mn.mn35k_lengths import audit_line_lengths
from pipelines.mn.mn35k_refinement import (build_model, export_deterministic,
    line_type_parameters, transformer_parameters, osm_line_evidence)
from pipelines.mn.validate_mn35k_poc import assert_electrical_equal, validate_model


@pytest.fixture(scope="module", autouse=True)
def prohibit_power_flow_and_protect_sources(source_paths):
    """The requested preparation must never silently run a power flow."""
    patch = pytest.MonkeyPatch()

    def prohibited(*args, **kwargs):
        raise AssertionError("PF/LPF execution is forbidden during model preparation")

    for cls in (pypsa.Network, pypsa.SubNetwork):
        for name in ("pf", "lpf"):
            patch.setattr(cls, name, prohibited)
    sources = [source_paths.earth_checkout / "networks" / area / "base.nc" for area in ("MN", "MN_35k")]
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    yield
    patch.undo()
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources} == hashes


@pytest.fixture(scope="module")
def config(source_paths):
    return json.loads((source_paths.source_project / "configs/mn35k_poc.json").read_text())


@pytest.fixture(scope="module")
def original(source_paths):
    return pypsa.Network(source_paths.earth_checkout / "networks/MN_35k/base.nc")


@pytest.fixture(scope="module")
def lengths(original, source_paths):
    return audit_line_lengths(original, source_paths)


@pytest.fixture(scope="module")
def refined(original, config, lengths):
    return build_model(original, config, lengths)


def test_line_geometry_and_temperature_units(config):
    result = line_type_parameters(config["line_35kv"]["reference"], config["line_common"])
    # Independently hand-checked for horizontal 1.8/1.8/3.6 m spacing,
    # 17.1 mm diameter, 50 Hz and 50 C (resistance referenced to 20 C).
    assert result["r_per_length"] == pytest.approx(0.2174546)
    assert result["x_per_length"] == pytest.approx(0.36635115490919057)
    assert result["c_per_length"] == pytest.approx(9.9688039680739)
    assert result["i_nom"] == pytest.approx(0.47)
    assert result["f_nom"] == 50.0


@pytest.mark.parametrize("field,value", [
    ("r20_ohm_per_km", 0.0), ("r20_ohm_per_km", float("nan")),
    ("diameter_mm", 0.0), ("diameter_mm", -1.0),
    ("adjacent_spacing_m", 0.0), ("adjacent_spacing_m", float("inf")),
    ("adjacent_spacing_m", 0.001),  # Phases overlap: log(D/r) is invalid.
    ("i_nom_ka", -1.0), ("temperature_c", float("nan")),
])
def test_invalid_line_profiles_rejected(config, field, value):
    profile = copy.deepcopy(config["line_35kv"]["reference"])
    profile[field] = value
    with pytest.raises(ValueError):
        line_type_parameters(profile, config["line_common"])


def test_transformer_test_report_conversion(config):
    profile = {"s_nom_mva": 25.0, "uk_percent": 10.5, "load_loss_kw": 105.0}
    actual = transformer_parameters(profile, config["transformer_common"])
    # At rated current Pcu = r_pu * S_base; uk is |z|, not x alone.
    assert actual["s_nom"] == 25.0
    assert actual["r"] == pytest.approx(0.0042)
    assert actual["x"] == pytest.approx(0.10491596637309308)
    assert math.hypot(actual["r"], actual["x"]) == pytest.approx(0.105)
    assert actual["r"] * actual["s_nom"] * 1000 == pytest.approx(105.0)


@pytest.mark.parametrize("field,value", [
    ("s_nom_mva", 0.0), ("s_nom_mva", -1.0), ("s_nom_mva", float("nan")),
    ("uk_percent", 0.0), ("uk_percent", -1.0), ("uk_percent", float("inf")),
    ("load_loss_kw", -1.0), ("load_loss_kw", float("nan")),
    ("load_loss_kw", 3000.0),  # r = .12 exceeds |z| = .105.
])
def test_impossible_transformer_test_values_rejected(config, field, value):
    profile = {"s_nom_mva": 25.0, "uk_percent": 10.5, "load_loss_kw": 105.0}
    profile[field] = value
    with pytest.raises(ValueError):
        transformer_parameters(profile, config["transformer_common"])


def test_build_preserves_source_object(original, config, lengths):
    before = original.copy()
    inputs = copy.deepcopy(config)
    old_lengths = lengths.copy(deep=True)
    built = build_model(original, config, lengths)
    for table in ("buses", "lines", "transformers", "generators", "loads", "line_types"):
        pd.testing.assert_frame_equal(getattr(original, table), getattr(before, table))
    assert original.meta == before.meta
    assert original.snapshots.equals(before.snapshots)
    assert config == inputs
    pd.testing.assert_frame_equal(lengths, old_lengths)
    assert built is not original


def test_actual_network_structure_and_ybus(refined, original, config):
    before = refined.copy()
    result = validate_model(refined, original, config)
    assert result["valid"], [check for check in result["checks"] if not check["passed"]]
    assert (len(refined.buses), len(refined.lines), len(refined.transformers)) == (173, 159, 27)
    assert len(refined.generators) == len(refined.loads) == 0
    assert sorted(map(len, result["components"]), reverse=True) == [166, 3, 2, 1, 1]
    assert sorted(result["isolated_buses"]) == ["118", "124"]
    assert sorted(item["shape"][0] for item in result["ybus"]) == [1, 1, 2, 3, 166]
    assert all(item["finite"] for item in result["ybus"])
    assert sum(item["isolated_zero_matrix"] for item in result["ybus"]) == 2
    assert result["ac_power_flow_executed"] is False
    assert result["convergence_verified"] is False
    assert_electrical_equal(refined, before)


def test_transformer_rating_bases_for_both_voltage_pairs(refined):
    low = refined.transformers.bus0.map(refined.buses.v_nom).eq(35)
    assert low.sum() == 17
    assert (~low).sum() == 10
    for mask, s_nom, r, z in ((low, 25.0, .0042, .105), (~low, 100.0, .0026, .12)):
        rows = refined.transformers.loc[mask]
        np.testing.assert_allclose(rows.s_nom, s_nom)
        np.testing.assert_allclose(rows.r, r)
        np.testing.assert_allclose(np.hypot(rows.r, rows.x), z)
        np.testing.assert_allclose(rows.r_pu, r / s_nom)
        np.testing.assert_allclose(rows.x_pu, np.sqrt(z*z-r*r) / s_nom)
    assert refined.transformers.type.eq("").all()
    assert refined.transformers.tap_ratio.eq(1).all()
    assert refined.transformers.phase_shift.eq(0).all()


def test_parallel_circuits_have_physical_scaling(original, config, lengths, refined):
    changed = original.copy()
    line_id = changed.lines.index[changed.lines.v_nom.eq(35)][0]
    changed.lines.loc[line_id, "num_parallel"] *= 2
    parallel = build_model(changed, config, lengths)
    a, b = refined.lines.loc[line_id], parallel.lines.loc[line_id]
    assert b.length == a.length
    assert b.r == pytest.approx(a.r / 2)
    assert b.x == pytest.approx(a.x / 2)
    assert b.b == pytest.approx(a.b * 2)
    assert b.s_nom == pytest.approx(a.s_nom * 2)


@pytest.mark.parametrize("bad_count", [0.0, -1.0, float("nan")])
def test_build_rejects_invalid_circuit_count(original, config, lengths, bad_count):
    changed = original.copy()
    changed.lines.loc[changed.lines.index[0], "num_parallel"] = bad_count
    with pytest.raises(ValueError):
        build_model(changed, config, lengths)


def test_build_rejects_missing_line_length(original, config, lengths):
    incomplete = lengths.iloc[1:].copy()
    with pytest.raises(ValueError):
        build_model(original, config, incomplete)


def test_build_rejects_changes_to_unresolved_length(original, config, lengths):
    changed = lengths.copy()
    name = changed.index[~changed.correction_applied][0]
    changed.loc[name, "length_after_km"] *= 2
    with pytest.raises(ValueError):
        build_model(original, config, changed)


def test_custom_types_survive_recalculation_and_netcdf(refined, tmp_path, source_paths):
    repeated = refined.copy()
    for _ in range(3):
        repeated.calculate_dependent_values()
        assert_electrical_equal(refined, repeated)
    path = tmp_path / "base.nc"
    export_deterministic(repeated, path, paths=replace(source_paths, output_dir=tmp_path))
    reloaded = pypsa.Network(path)
    assert_electrical_equal(refined, reloaded)
    reloaded.calculate_dependent_values()
    assert_electrical_equal(refined, reloaded)
    names = reloaded.lines.loc[reloaded.lines.v_nom.eq(35), "type"]
    assert names.str.startswith("POC_35kV_").all()
    assert names.isin(reloaded.line_types.index).all()


def test_rebuild_and_exports_are_deterministic(original, config, lengths, refined, tmp_path, source_paths):
    another = build_model(original, config, lengths)
    assert_electrical_equal(refined, another)
    first, second = tmp_path / "first.nc", tmp_path / "second.nc"
    export_deterministic(refined, first, paths=replace(source_paths, output_dir=tmp_path))
    export_deterministic(another, second, paths=replace(source_paths, output_dir=tmp_path))
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("scenario", ["low_impedance", "high_impedance"])
def test_sensitivity_models_are_valid_without_operating_scenarios(original, config, lengths, scenario, refined):
    model = build_model(original, config, lengths, scenario=scenario)
    result = validate_model(model, original, config)
    assert result["valid"], [check for check in result["checks"] if not check["passed"]]
    changed = model.lines.loc[model.lines.v_nom.eq(35)]
    base = refined.lines.reindex(changed.index)
    if scenario == "low_impedance":
        assert (changed.r < base.r).all()
        assert (changed.x < base.x).all()
        assert (model.transformers.x_pu < refined.transformers.x_pu).all()
    else:
        assert (changed.r > base.r).all()
        assert (changed.x > base.x).all()
        assert (model.transformers.x_pu > refined.transformers.x_pu).all()
    assert len(model.generators) == len(model.loads) == 0


def test_length_application_is_id_based_and_preserves_unresolved(refined, original, lengths):
    selected = lengths.reindex(refined.lines.index)
    np.testing.assert_allclose(refined.lines.length, selected.length_after_km)
    unresolved = selected.index[~selected.correction_applied]
    assert len(unresolved) > 0
    np.testing.assert_array_equal(refined.lines.loc[unresolved, "length"], original.lines.loc[unresolved, "length"])


@pytest.mark.parametrize("component,field,value", [
    ("lines", "r", 0.0), ("lines", "x", -0.1), ("lines", "b", -1e-5),
    ("lines", "s_nom", 0.0), ("lines", "length", -1.0),
    ("lines", "num_parallel", 0.0), ("lines", "r", float("nan")),
    ("transformers", "r", -0.1), ("transformers", "x", float("inf")),
    ("transformers", "s_nom", 0.0), ("transformers", "tap_ratio", 0.0),
    ("transformers", "tap_side", 99), ("transformers", "phase_shift", float("nan")),
])
def test_invalid_electrical_parameters_are_reported(refined, original, config, component, field, value):
    broken = refined.copy()
    table = getattr(broken, component)
    table.loc[table.index[0], field] = value
    result = validate_model(broken, original, config)
    assert not result["valid"]
    assert result["ybus"] == []


@pytest.mark.parametrize("component", ["lines", "transformers"])
def test_missing_endpoint_reported_without_crash(refined, original, config, component):
    broken = refined.copy()
    table = getattr(broken, component)
    table.loc[table.index[0], "bus1"] = "missing_bus"
    result = validate_model(broken, original, config)
    assert not result["valid"]
    assert any(check["check"] == component + "_endpoints_exist" and not check["passed"] for check in result["checks"])


def test_unexpected_voltage_pair_reported_without_crash(refined, original, config):
    broken = refined.copy()
    bus = broken.transformers.iloc[0].bus0
    broken.buses.loc[bus, "v_nom"] = 132.0
    result = validate_model(broken, original, config)
    assert not result["valid"]
    assert result["ybus"] == []


def test_osm_nested_tags_distinguish_circuits_and_bundle(original, source_paths):
    evidence = osm_line_evidence(original, source_paths.earth_checkout / "resources/MN_35k/osm/raw/all_raw_lines.geojson")
    double = evidence.loc["216218243-1_0"]
    assert double.raw_circuits == "2"
    assert double.circuit_evidence == "EXPLICIT_OSM_CIRCUITS"
    assert evidence.loc[evidence.voltage_kv.eq(35), "circuit_evidence"].value_counts().to_dict() == {
        "EXPLICIT_OSM_CIRCUITS": 1, "INFERRED_CABLES_DIVIDED_BY_THREE": 5, "DEFAULT_SINGLE_CIRCUIT": 42}
    assert set(evidence.index[evidence.bundle_uncertainty]) == {"291610988-1_0", "751270980-1_0"}
    assert evidence.loc["484276912-1_0", "circuit_evidence"] == "UNRESOLVED_MULTI_VOLTAGE_CIRCUITS"


@pytest.mark.parametrize("area", ["MN", "MN_35k"])
def test_export_refuses_source_and_symlink(refined, tmp_path, area, source_paths):
    source = source_paths.earth_checkout / "networks" / area / "base.nc"
    with pytest.raises(ValueError, match="읽기 전용|protected"):
        export_deterministic(refined, source, paths=replace(source_paths, output_dir=tmp_path))
    alias = tmp_path / "alias.nc"
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="읽기 전용|protected"):
        export_deterministic(refined, alias, paths=replace(source_paths, output_dir=tmp_path))


def test_ybus_does_not_assign_bus_controls(refined, original, config, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No slack/operating control discovery is allowed")
    monkeypatch.setattr(pypsa.SubNetwork, "find_bus_controls", forbidden)
    assert validate_model(refined, original, config)["valid"]


@pytest.mark.parametrize("voltage", [35, 110, 220])
def test_consistent_but_untracked_line_type_edit_is_rejected(refined, original, config, voltage):
    broken = refined.copy()
    name = broken.lines.loc[broken.lines.v_nom.eq(voltage), "type"].iloc[0]
    broken.line_types.loc[name, "r_per_length"] *= 2
    broken.calculate_dependent_values()
    result = validate_model(broken, original, config)
    assert not result["valid"]
    assert result["ybus"] == []
