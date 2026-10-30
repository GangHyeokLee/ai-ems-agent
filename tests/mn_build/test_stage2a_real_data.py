"""Read-only actual-data parity against pinned original implementations.

The 173/166-bus models are only loaded, audited or extracted. They are never
rebuilt, solved, exported or activated. Only a three-bus sample is exported.
"""
from dataclasses import replace
import json
from pathlib import Path

import pytest

pypsa = pytest.importorskip("pypsa", reason="Stage 2A actual-data parity requires the separate build environment")
import pandas as pd
import xarray as xr

from pipelines.mn import audit_mn35k_parameters as audit, mn35k_lengths as lengths
from pipelines.mn import mn35k_refinement as refine, mn35k_powsybl as conv
from pipelines.mn.inventory import load_contract, sha256


@pytest.fixture(scope="module", autouse=True)
def forbid_real_model_power_flow():
    with pytest.MonkeyPatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("Actual-data parity must not execute PF/LPF")
        for cls in (pypsa.Network, pypsa.SubNetwork):
            for name in ("pf", "lpf"):
                patch.setattr(cls, name, forbidden)
        yield


@pytest.fixture(scope="module")
def original_network(source_paths):
    return pypsa.Network(source_paths.earth_checkout / "networks/MN_35k/base.nc")


@pytest.fixture(scope="module")
def length_rows(source_paths, original_network, original_modules):
    before = original_network.lines.copy(deep=True)
    expected = original_modules.mn35k_lengths.audit_line_lengths(original_network, source_paths.source_project)
    actual = lengths.audit_line_lengths(original_network, source_paths)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    pd.testing.assert_frame_equal(original_network.lines, before, check_exact=True)
    return actual


def test_actual_lengths_all_159_rows_are_exactly_equal(length_rows):
    assert len(length_rows) == 159
    assert (~length_rows.correction_applied).sum() == 18
    assert length_rows.exact_id_match.all()
    assert length_rows.loc["1378326336-1_1", "unresolved_reason"] == "disconnected_geometry"
    unresolved = length_rows.loc[~length_rows.correction_applied]
    assert (unresolved.length_before_km == unresolved.length_after_km).all()
    assert not length_rows.length_physical_verified.any()


def test_actual_voltage_summaries_and_audit_tables(source_paths, original_network, original_modules):
    old = original_modules.audit_mn35k_parameters
    expected, info = old.build_length_audit(original_network)
    actual, actual_info = audit.build_length_audit(original_network, source_paths)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert actual_info == info
    pd.testing.assert_frame_equal(lengths.summarize_length_stages(original_network, source_paths),
                                  original_modules.mn35k_lengths.summarize_length_stages(original_network, source_paths.source_project), check_exact=True)
    for name in ("line_parameter_rows", "transformer_parameter_rows"):
        pd.testing.assert_frame_equal(getattr(audit, name)(original_network), getattr(old, name)(original_network), check_exact=True)
    assert audit.finite_sign_summary(original_network) == old.finite_sign_summary(original_network)
    actual, info = audit.plant_and_bus_audit(original_network, source_paths)
    expected, old_info = old.plant_and_bus_audit(original_network)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert info == old_info
    assert len(actual) == 27


def test_actual_electrical_parameters_and_register(source_paths, original_network, original_modules, length_rows):
    config = json.loads((source_paths.source_project / "configs/mn35k_poc.json").read_text())
    old = original_modules.mn35k_refinement
    for scenario, profile in config["line_35kv"].items():
        assert refine.line_type_parameters(profile, config["line_common"]) == old.line_type_parameters(profile, config["line_common"])
        for pair in config["transformers"].values():
            assert refine.transformer_parameters(pair[scenario], config["transformer_common"]) == old.transformer_parameters(pair[scenario], config["transformer_common"])
    passive = pypsa.Network(source_paths.earth_checkout / "networks/MN_35k_poc/base.nc")
    actual = refine.parameter_tables(original_network, passive, length_rows)
    expected = old.parameter_tables(original_network, passive, length_rows)
    for a, e in zip(actual, expected):
        pd.testing.assert_frame_equal(a, e, check_exact=True)
    raw = source_paths.earth_checkout / "resources/MN_35k/osm/raw/all_raw_lines.geojson"
    evidence = refine.osm_line_evidence(original_network, raw)
    pd.testing.assert_frame_equal(evidence, old.osm_line_evidence(original_network, raw), check_exact=True)
    pd.testing.assert_frame_equal(refine.assumption_register(original_network, passive, config, length_rows, evidence),
                                  old.assumption_register(original_network, passive, config, length_rows, evidence), check_exact=True)


def test_actual_conversion_extraction_ids_units_and_controls(source_paths, original_modules):
    spec = next(s for s in load_contract()["files"] if s["role"] == "pf_model")
    path = source_paths.earth_checkout / spec["path"]
    n = pypsa.Network(path)
    n.determine_network_topology()
    for subnetwork in n.sub_networks.obj:
        subnetwork.find_bus_controls()
    a = conv.extract_model(n.copy(), q_limits=(-100., 100.))
    e = original_modules.mn35k_powsybl.extract_model(n.copy(), q_limits=(-100., 100.))
    for field in ("buses", "lines", "transformers", "generators", "loads", "shunts", "mapping"):
        pd.testing.assert_frame_equal(getattr(a, field), getattr(e, field), check_exact=True)
    assert [len(a.buses), len(a.lines), len(a.transformers), len(a.generators), len(a.loads), len(a.shunts)] == [166, 156, 27, 45, 166, 1]
    assert a.slack_bus == e.slack_bus == "25"
    assert sha256(source_paths.source_project / "scripts/mn35k_powsybl.py") == sha256(conv.__file__)


def test_tiny_build_and_export_match_original_bytes(source_paths, original_modules, tmp_path):
    """Exercise build/export only on three synthetic buses, not the live models."""
    config = json.loads((source_paths.source_project / "configs/mn35k_poc.json").read_text())
    n = pypsa.Network()
    n.set_snapshots([pd.Timestamp("2013-01-01")])
    for bus, voltage in (("1", 35.), ("2", 35.), ("3", 110.)):
        n.add("Bus", bus, v_nom=voltage)
    n.add("Line", "L12", bus0="1", bus1="2", v_nom=35., length=1., type="243-AL1/39-ST1A 110.0", s_nom=100.)
    n.add("Transformer", "T23", bus0="2", bus1="3", r=0., x=.1, s_nom=2000.)
    table = pd.DataFrame(dict(length_before_km=[1.], length_after_km=[1.], correction_applied=[False], status=["UNRESOLVED"]), index=["L12"])
    a = refine.build_model(n, config, table)
    e = original_modules.mn35k_refinement.build_model(n, config, table)
    assert a.meta == e.meta
    for field in ("buses", "lines", "transformers", "line_types"):
        pd.testing.assert_frame_equal(getattr(a, field), getattr(e, field), check_exact=True)
    paths = replace(source_paths, output_dir=tmp_path / "generated")
    first, second = paths.output_dir / "migrated.nc", tmp_path / "original.nc"
    refine.export_deterministic(a, first, paths=paths, protected_models=config["protected_models"])
    original_modules.mn35k_refinement.export_deterministic(e, second)
    with xr.open_dataset(first, engine="scipy") as af, xr.open_dataset(second, engine="scipy") as ef:
        xr.testing.assert_identical(af, ef)
    assert first.read_bytes() == second.read_bytes(), "Semantic comparison passed but serialized bytes differ"


@pytest.fixture(scope="module")
def report_args(source_paths, original_network):
    lengths_, info = audit.build_length_audit(original_network, source_paths)
    plants, bus_info = audit.plant_and_bus_audit(original_network, source_paths)
    return (original_network, audit.line_parameter_rows(original_network), audit.transformer_parameter_rows(original_network),
            lengths_, info, plants, bus_info, audit.finite_sign_summary(original_network))


def test_six_audit_reports_match_original_without_replacing_anything(source_paths, original_modules, original_network, tmp_path, monkeypatch, report_args):
    old = original_modules.audit_mn35k_parameters
    args = report_args
    lengths_ = args[3]
    prior = tmp_path / "reference-reports"
    prior.mkdir()
    monkeypatch.setattr(old, "REPORTS", prior)
    old.write_reports(*args)
    paths = replace(source_paths, output_dir=tmp_path / "generated")
    audit.write_reports(*args, paths=paths)
    generated = paths.output_dir / "parameter_review"
    assert len(list(generated.iterdir())) == 6
    for reference in prior.iterdir():
        assert (generated / reference.name).read_bytes() == reference.read_bytes(), reference.name
    audit.validate_outputs(original_network, lengths_, generated)
    hashes = {p.name: sha256(p) for p in generated.iterdir()}
    with pytest.raises(ValueError, match="overwrite"):
        audit.write_reports(*args, paths=paths)
    assert hashes == {p.name: sha256(p) for p in generated.iterdir()}


@pytest.mark.parametrize("failure_at", [3, 5])
def test_interrupted_report_bundle_leaves_no_final_files_and_can_retry(source_paths, report_args, tmp_path, monkeypatch, failure_at):
    paths = replace(source_paths, output_dir=tmp_path / "generated")
    target = paths.output_dir / "parameter_review"
    sources = [paths.earth_checkout / "networks" / area / "base.nc" for area in ("MN", "MN_35k")]
    before = {p: sha256(p) for p in sources}
    seen = []
    original_csv, original_text = pd.DataFrame.to_csv, Path.write_text

    def checkpoint(path):
        path = Path(path)
        assert not target.exists(), "No report may be public during rendering"
        seen.append(path.name)
        if len(seen) == failure_at:
            path.write_bytes(b"interrupted partial file")
            raise OSError(f"Injected report generation failure #{failure_at}")

    def csv(frame, path, *args, **kwargs):
        checkpoint(path)
        return original_csv(frame, path, *args, **kwargs)

    def text(path, data, *args, **kwargs):
        checkpoint(path)
        return original_text(path, data, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pd.DataFrame, "to_csv", csv)
        patch.setattr(Path, "write_text", text)
        with pytest.raises(OSError, match="Injected report generation failure"):
            audit.write_reports(*report_args, paths=paths)
    assert seen == list(audit.REPORT_FILES[:failure_at])
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())
    assert before == {p: sha256(p) for p in sources}
    # Retry the same final destination without manual cleanup or overwrite.
    audit.write_reports(*report_args, paths=paths)
    assert {p.name for p in target.iterdir()} == set(audit.REPORT_FILES)


def test_report_validation_failure_does_not_publish_a_bundle(source_paths, report_args, tmp_path, monkeypatch):
    paths = replace(source_paths, output_dir=tmp_path / "generated")
    target = paths.output_dir / "parameter_review"

    def reject(network, lengths_, staged):
        assert not target.exists()
        assert {p.name for p in staged.iterdir()} == set(audit.REPORT_FILES)
        raise ValueError("Injected completed-report validation failure")

    with monkeypatch.context() as patch:
        patch.setattr(audit, "validate_outputs", reject)
        with pytest.raises(ValueError, match="validation failure"):
            audit.write_reports(*report_args, paths=paths)
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())
