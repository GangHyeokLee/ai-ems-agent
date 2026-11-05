"""Validator parity: actual source models are read-only; no PF or slack discovery."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys

import pytest

pypsa = pytest.importorskip("pypsa", reason="Stage 2B validators require the separate build environment")
import pandas as pd

from pipelines.mn import validate_mn_grid as grid, validate_mn35k_poc as passive
from pipelines.mn.inventory import load_contract, sha256


@pytest.fixture(scope="module", autouse=True)
def no_power_flow_or_control_assignment():
    with pytest.MonkeyPatch.context() as patch:
        def forbidden(*a, **k):
            raise AssertionError("Stage 2B must not run PF/LPF or assign controls")
        for cls in (pypsa.Network, pypsa.SubNetwork):
            for name in ("pf", "lpf"):
                patch.setattr(cls, name, forbidden)
        patch.setattr(pypsa.SubNetwork, "find_bus_controls", forbidden)
        yield


@pytest.fixture(scope="module")
def reference_validators(source_paths, original_modules):
    modules = {}
    pins = {s["path"]:s["sha256"] for s in load_contract()["files"] if s["root"] == "source"}
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "audit_mn35k_parameters", original_modules.audit_mn35k_parameters)
        patch.setitem(sys.modules, "mn35k_refinement", original_modules.mn35k_refinement)
        for name in ("validate_mn_grid", "validate_mn35k_poc"):
            file = source_paths.source_project / "scripts" / (name + ".py")
            assert sha256(file) == pins["scripts/" + name + ".py"]
            spec = importlib.util.spec_from_file_location("reference_stage2b_"+name, file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules[name] = module
        # Original passive validator has a function-local bare import.
        yield modules


def test_passive_validator_matches_original(source_paths, reference_validators):
    original = pypsa.Network(source_paths.earth_checkout / "networks/MN_35k/base.nc")
    model = pypsa.Network(source_paths.earth_checkout / "networks/MN_35k_poc/base.nc")
    config = json.loads((source_paths.source_project / "configs/mn35k_poc.json").read_text())
    expected = reference_validators["validate_mn35k_poc"].validate_model(model, original, config)
    actual = passive.validate_model(model, original, config)
    assert actual == expected
    assert actual["valid"] and len(actual["ybus"]) == 5
    assert [len(c) for c in actual["components"]] == [166, 3, 2, 1, 1]


def test_generic_validator_csv_and_report_parity(source_paths, reference_validators, tmp_path, monkeypatch):
    """Reference I/O redirected in memory; never modify the original project."""
    old = reference_validators["validate_mn_grid"]
    virtual = tmp_path / "virtual"
    # A symlink root is safe here: reference reports are under scratch only.
    virtual.mkdir()
    (virtual / "pypsa-earth").symlink_to(source_paths.earth_checkout, target_is_directory=True)
    with monkeypatch.context() as patch:
        patch.setattr(old, "_probe_empty_network_pf", lambda n:"NOT_EXECUTED_STAGE2B_NO_PF")
        expected = old.validate(virtual)
    paths = replace(source_paths, output_dir=tmp_path / "generated")
    actual = grid.validate(paths)
    assert {k:v for k,v in actual.items() if k != "output_dir"} == {k:v for k,v in expected.items() if k != "output_dir"}
    for name in grid.VALIDATION_FILES:
        assert (actual["output_dir"] / name).read_bytes() == (expected["output_dir"] / name).read_bytes(), name
    with pytest.raises(ValueError, match="overwrite"):
        grid.validate(paths)


def test_small_validator_helpers_preserve_units_and_topology():
    assert grid.haversine_km(0, 0, 0, 1) == pytest.approx(111.1950802335329)
    assert grid._normalise_name("  Plant   A ") == "plant a"
    lines = pd.DataFrame(dict(bus0=["1"], bus1=["2"], active=[True]))
    components, mapping = grid._component_map(["1", "2", "3"], lines)
    assert [c["bus_count"] for c in components] == [2, 1]
    assert mapping["1"] == mapping["2"] != mapping["3"]


def test_missing_generic_input_fails_without_creating_output(sample_paths):
    with pytest.raises(FileNotFoundError, match="Required validation inputs missing"):
        grid.validate(sample_paths)
    assert not sample_paths.output_dir.exists()


@pytest.mark.parametrize("changed", [False, True])
def test_generic_input_snapshot_is_rechecked_before_publication(sample_paths, monkeypatch, changed):
    paths = sample_paths
    for relative in ("resources/MN/powerplants.csv", "resources/MN/powerplants_osm2pm.csv",
                     "resources/MN/osm/clean/all_clean_generators.csv", "resources/MN/shapes/country_shapes.geojson"):
        file = paths.earth_checkout / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("small fixed input")
    def render(paths, staged):
        for name in grid.VALIDATION_FILES:
            (staged / name).write_text("value\n1\n")
        if changed:
            (paths.earth_checkout / "resources/MN/powerplants.csv").write_text("changed during audit")
        return {}
    monkeypatch.setattr(grid, "_validate", render)
    if changed:
        with pytest.raises(ValueError, match="inputs changed"):
            grid.validate(paths)
        assert not list(paths.output_dir.iterdir())
    else:
        result = grid.validate(paths)
        assert {p.name for p in result["output_dir"].iterdir()} == set(grid.VALIDATION_FILES)
