"""Isolated full rebuild and failure paths; original data remain read-only."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

pypsa = pytest.importorskip("pypsa", reason="Stage 2B rebuilds require the separate build environment")
from pipelines.mn import build_mn35k_poc as builder, passive_inputs
from pipelines.mn.inventory import sha256


@pytest.fixture(autouse=True)
def forbid_power_flow(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Passive rebuild must not run PF/LPF or assign a slack")
    for cls in (pypsa.Network, pypsa.SubNetwork):
        for name in ("pf", "lpf"):
            monkeypatch.setattr(cls, name, forbidden)
    monkeypatch.setattr(pypsa.SubNetwork, "find_bus_controls", forbidden)


@pytest.fixture
def copied_inputs(source_paths, tmp_path):
    """Copy only the pinned small inputs, never an environment or a checkout."""
    paths = replace(source_paths, source_project=tmp_path / "source", earth_checkout=tmp_path / "earth",
                    work_dir=tmp_path / "work", output_dir=tmp_path / "generated", bundle_dir=tmp_path / "bundles")
    spec = passive_inputs.contract()
    names = set(spec["inputs"]) | {spec["config"], spec["historical_manifest"], "pypsa-earth/"+spec["reference_model"]}
    for name in names:
        target = passive_inputs.logical_input(paths, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(passive_inputs.logical_input(source_paths, name), target)
    return paths


def test_full_rebuild_is_byte_identical_with_relocated_inputs(copied_inputs, source_paths):
    paths = copied_inputs
    result = builder.rebuild_for_verification(paths, paths.output_dir / "verification")
    target = paths.output_dir / "verification"
    assert {p.name for p in target.iterdir()} == set(builder.BUILD_FILES)
    assert result["input_files"] == 57 and result["assumption_rows"] == 3845
    assert result["artifact"]["sha256"] == passive_inputs.contract()["reference_sha256"]
    assert result["equivalence"]["byte_identical_to_reference"]
    assert result["equivalence"]["length_unresolved"] == 18
    assert [len(c) for c in result["equivalence"]["components"]] == [166, 3, 2, 1, 1]
    assert all(m["max_abs_delta"] == 0 for m in result["equivalence"]["ybus"])
    assert result["ac_pf_executed"] is False
    # All nine numerical CSVs preserve the original writer's bytes/precision.
    for name in builder.BUILD_FILES:
        if name.endswith(".csv"):
            assert (target/name).read_bytes() == (source_paths.source_project/"reports/model_refinement"/name).read_bytes(), name
    assert json.loads((target/"input_hashes.json").read_text()) == passive_inputs.contract()["inputs"]
    provenance = json.loads((target/"build_provenance.json").read_text())
    assert provenance["owned_code_sha256"]["pipelines/mn/build_mn35k_poc.py"] == sha256(Path(builder.__file__))
    before = sha256(target/"base.nc")
    with pytest.raises(ValueError, match="overwrite"):
        builder.rebuild_for_verification(paths, target)
    assert sha256(target/"base.nc") == before


@pytest.mark.parametrize("problem", ["missing-file", "missing-directory", "hash", "extra", "prior", "config"])
def test_missing_or_changed_inputs_fail_before_generation(copied_inputs, monkeypatch, problem):
    paths = copied_inputs
    line = passive_inputs.logical_input(paths, "pypsa-earth/resources/MN_35k/base_network/all_lines_build_network.csv")
    if problem == "missing-file":
        line.unlink()
    elif problem == "missing-directory":
        shutil.rmtree(paths.source_project/"reports/validation")
    elif problem == "hash":
        line.write_bytes(line.read_bytes()+b"\n")
    elif problem == "extra":
        (line.parent/"unexpected.csv").write_text("extra")
    elif problem == "prior":
        (paths.source_project/"reports/model_refinement/input_hashes.json").write_text("{}")
    else:
        (paths.source_project/"configs/mn35k_poc.json").write_text("{}")
    def forbidden(*a, **k):
        pytest.fail("Input validation must fail before model construction")
    monkeypatch.setattr(builder, "build_model", forbidden)
    with pytest.raises((ValueError, FileNotFoundError)):
        builder.rebuild_for_verification(paths, paths.output_dir/"verification")
    assert not paths.output_dir.exists()


@pytest.mark.parametrize("kind", ["source", "earth", "work", "bundle", "existing", "symlink", "hardlink"])
def test_unsafe_outputs_never_overwrite_originals(copied_inputs, monkeypatch, kind):
    paths = copied_inputs
    source = paths.earth_checkout/"networks/MN_35k_poc/base.nc"
    before = sha256(source)
    if kind in ("source", "earth", "work", "bundle"):
        target = {"source":paths.source_project, "earth":paths.earth_checkout, "work":paths.work_dir, "bundle":paths.bundle_dir}[kind]/"forbidden-stage2b"
    else:
        paths.output_dir.mkdir()
        target = paths.output_dir/"verification"
        if kind == "existing":
            target.mkdir()
        elif kind == "symlink":
            target.symlink_to(source)
        else:
            target.hardlink_to(source)
    def forbidden(*a, **k):
        pytest.fail("Unsafe destination must be refused before construction")
    monkeypatch.setattr(builder, "build_model", forbidden)
    with pytest.raises(ValueError):
        builder.rebuild_for_verification(paths, target)
    assert sha256(source) == before
    if kind == "existing":
        assert target.is_dir() and not list(target.iterdir())


@pytest.mark.parametrize("phase", ["model", "third-report"])
def test_partial_build_is_never_published_and_same_path_can_retry(source_paths, tmp_path, monkeypatch, phase):
    paths = replace(source_paths, output_dir=tmp_path/"generated")
    target = paths.output_dir/"verification"
    original_csv = builder._csv
    seen = []
    def fail_model(*a, **k):
        raise RuntimeError("Injected model generation failure")
    def fail_csv(frame, name, *a, **k):
        assert not target.exists()
        original_csv(frame, name, *a, **k)
        seen.append(name)
        if len(seen) == 3:
            raise RuntimeError("Injected third-report failure")
    with monkeypatch.context() as patch:
        patch.setattr(builder, "build_model" if phase == "model" else "_csv", fail_model if phase == "model" else fail_csv)
        with pytest.raises(RuntimeError, match="Injected"):
            builder.rebuild_for_verification(paths, target)
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())
    builder.rebuild_for_verification(paths, target)
    assert sha256(target/"base.nc") == passive_inputs.contract()["reference_sha256"]


def test_input_change_during_reports_discards_whole_bundle(copied_inputs, monkeypatch):
    paths = copied_inputs
    target = paths.output_dir/"verification"
    original = builder._write_reports
    def change_input(*a, **k):
        original(*a, **k)
        file = passive_inputs.logical_input(paths, "reports/expansion/mn_35k_bus_mapping.csv")
        file.write_bytes(file.read_bytes()+b"\n")
    monkeypatch.setattr(builder, "_write_reports", change_input)
    with pytest.raises(ValueError, match="input hashes changed"):
        builder.rebuild_for_verification(paths, target)
    assert not target.exists()
    assert not list(paths.output_dir.iterdir())
