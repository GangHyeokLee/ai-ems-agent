"""Real bytes and isolated EMS interpreter; requires explicit external inputs.

No PyPSA import is needed for packaging. The separate build suite exercises this
alongside earlier migrations; default EMS runs report these opt-in skips.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from pipelines.mn import artifacts, bundle
from pipelines.mn import bundle_build as build
from pipelines.mn.inventory import sha256


@pytest.fixture(scope="module")
def real_bundle(source_paths, tmp_path_factory):
    area = tmp_path_factory.mktemp("stage3b-bundles")
    paths = replace(source_paths, bundle_dir=area)
    result = build.create_bundle(paths, version="test-1")
    return paths, result


def copied(real_bundle, tmp_path):
    paths, receipt = real_bundle
    root = tmp_path/"copied"
    shutil.copytree(receipt["bundle"], root)
    return root, receipt["trusted_manifest_sha256"]


def rehash_manifest(root):
    """Test attacker repairs file hashes; caller must still supply outside trust."""
    manifest = bundle.read_json(root/"manifest.json")
    manifest["files"] = {k: v for k, v in bundle.tree_files(root).items() if k != "manifest.json"}
    (root/"manifest.json").write_text(json.dumps(manifest))
    return sha256(root/"manifest.json")


def test_actual_bundle_identity_and_all_original_bytes(real_bundle):
    paths, receipt = real_bundle
    root = Path(receipt["bundle"])
    checked = bundle.validate_bundle(root, expected_manifest_sha256=receipt["trusted_manifest_sha256"])
    assert checked["file_count"] == 23 and checked["mapping_count"] == 561
    assert not receipt["activated"]
    sources = build.selected_inputs(paths, "historical", None)
    for name, source in sources.items():
        assert (root/name).read_bytes() == source.read_bytes()
        assert (root/name).stat().st_ino != source.stat().st_ino
    assert not list(root.rglob("*.nc"))
    runtime = bundle.read_json(root/"validation/runtime_compatibility.json")
    assert len(runtime["numerical_checks"]) == 186
    assert [r["isolated_bus_count"] for r in runtime["numerical"]["n1"]] == [8, 0, 11]
    assert sum(r["candidate_count"] for r in runtime["numerical"]["sensitivity"]) == 132
    assert runtime["pypsa_installed"] is False


def test_runtime_copied_bundle_without_source_earth_build_env_or_dev_assets(real_bundle):
    paths, receipt = real_bundle
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        pytest.fail("This independence test requires read-only mount isolation (bwrap)")
    before = bundle.tree_files(Path(receipt["bundle"]))
    with tempfile.TemporaryDirectory(prefix="mn-bundle-independent-") as temporary:
        copy = Path(temporary)/"relocated"
        shutil.copytree(receipt["bundle"], copy)
        evidence = Path(temporary)/"isolation-proof.py"
        inaccessible = [paths.source_project/"scripts/convert_mn35k_to_powsybl.py",
                        paths.earth_checkout/"networks/MN_35k_poc/base.nc",
                        paths.conversion_python,
                        paths.source_project/"reports/pypowsybl_conversion/element_mapping.csv",
                        paths.ems_root/"data/grids/mn-osm-35k-map.json",
                        Path(receipt["bundle"])/"manifest.json"]
        evidence.write_text("from pathlib import Path\nimport runpy,sys\n"
                            + f"blocked={list(map(str,inaccessible))!r}\n"
                            + "for name in blocked:\n try: Path(name).read_bytes()\n except (FileNotFoundError, PermissionError): pass\n else: raise AssertionError('external path accessible: '+name)\n"
                            + "sys.argv=sys.argv[1:]\nrunpy.run_path(sys.argv[0],run_name='__main__')\n")
        command = [bwrap, "--ro-bind", "/", "/", "--bind", "/tmp", "/tmp", "--dev-bind", "/dev", "/dev",
                   "--proc", "/proc", "--ro-bind", str(copy), str(copy),
                   "--tmpfs", str(paths.source_project),
                   "--tmpfs", str(paths.ems_root/"data"), "--tmpfs", str(paths.ems_root/"outputs")]
        if not paths.earth_checkout.is_relative_to(paths.source_project):
            command += ["--tmpfs", str(paths.earth_checkout)]
        command += ["--", str(paths.runtime_python), "-B", "-I", str(evidence),
                    str(paths.ems_root/"pipelines/mn/bundle_probe.py"), "--bundle", str(copy),
                    "--manifest-sha256", receipt["trusted_manifest_sha256"], "--ems-root", str(paths.ems_root)]
        done = subprocess.run(command, capture_output=True, text=True, timeout=180)
        assert done.returncode == 0, done.stdout + done.stderr
        runtime = json.loads(done.stdout.splitlines()[-1])
        assert runtime["runtime_compatible"] and not runtime["activated"]
        assert runtime["pypsa_installed"] is False
        assert all(c["passed"] for c in runtime["numerical_checks"])
        assert bundle.tree_files(copy) == before
        # Persist proof outside the immutable copied/original bundle.
        output = Path(receipt["bundle"]).parent/"independence-proof.json"
        build.write_json(output, {"command": command, "blocked_read_probes": list(map(str,inaccessible)),
                                  "mount_isolation": True, "runtime": runtime})
    assert bundle.tree_files(Path(receipt["bundle"])) == before


@pytest.mark.parametrize("change", ["tamper", "missing", "extra", "symlink", "hardlink", "manifest", "pending-runtime"])
def test_bundle_corruption_fails(real_bundle, tmp_path, change):
    root, trusted = copied(real_bundle, tmp_path)
    victim = root/"reports/element_mapping.csv"
    if change == "tamper":
        with victim.open("ab") as stream:
            stream.write(b"\n")
    elif change == "missing":
        victim.unlink()
    elif change == "extra":
        (root/"extra").write_text("unlisted")
    elif change == "manifest":
        manifest = bundle.read_json(root/"manifest.json")
        manifest["scenario_id"] = "data_informed"
        (root/"manifest.json").write_text(json.dumps(manifest))
    elif change == "pending-runtime":
        (root/"validation/runtime_compatibility.json").write_text('{"status":"PENDING"}')
        trusted = rehash_manifest(root)
    else:
        source = tmp_path/"outside"
        shutil.copyfile(victim, source)
        victim.unlink()
        victim.symlink_to(source) if change == "symlink" else os.link(source, victim)
    with pytest.raises(ValueError):
        bundle.validate_bundle(root, expected_manifest_sha256=trusted)


@pytest.mark.parametrize("change", ["source-hash", "mapping-id", "electrical", "scenario", "controls", "license", "escape", "contract", "report-pair"])
def test_even_rehashed_bundle_must_satisfy_fixed_runtime_contract(real_bundle, tmp_path, change):
    root, trusted = copied(real_bundle, tmp_path)
    if change == "mapping-id":
        p = root/"reports/element_mapping.csv"
        p.write_text(p.read_text().replace("SYNTHETIC_SLACK_25", "UNVERIFIED", 1))
    elif change == "electrical":
        p = root/bundle.MODEL
        p.write_text(p.read_text().replace('r="', 'r="1', 1))
    elif change in ("source-hash", "scenario", "report-pair"):
        p = root/("reports/input_hashes.json" if change == "source-hash" else "reports/conversion_validation.json")
        value = bundle.read_json(p)
        if change == "source-hash":
            value["input_model"] = {"wrong.nc": "0"*64}
        elif change == "scenario":
            value["input_scenario"]["meta_scenario_id"] = "data_informed"
        else:
            value["xiidm_reload"]["sha256"] = "0"*64
        p.write_text(json.dumps(value))
    elif change == "contract":
        p = root/"validation/runtime_contract.json"
        value = bundle.read_json(p)
        value["numerical_reference"]["sensitivity"][0]["candidates"][0]["sensitivity"] += 1
        p.write_text(json.dumps(value))
    elif change == "license":
        (root/"validation/license_review.json").write_text('{"external_distribution_approved":true}')
    else:
        p = root/"manifest.json"
        value = bundle.read_json(p)
        if change == "controls":
            value["loadflow_parameters"]["distributed_slack"] = True
        else:
            value["runtime_paths"]["xiidm"] = "../original.xiidm"
        p.write_text(json.dumps(value))
    repaired = rehash_manifest(root)
    with pytest.raises(ValueError):
        bundle.validate_bundle(root, expected_manifest_sha256=trusted)
    with pytest.raises(ValueError):
        bundle.validate_bundle(root, expected_manifest_sha256=repaired)


@pytest.mark.parametrize("failure", ["third-report", "fifth-report", "runtime", "source-change", "publication"])
def test_actual_creation_failures_never_publish(real_bundle, monkeypatch, failure):
    paths, _ = real_bundle
    version = "failure-"+failure
    target = paths.bundle_dir/f"mn-osm-35k-historical-{version}"
    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")
    if failure in ("third-report", "fifth-report"):
        original = build.write_json
        count = 0
        def write(*args, **kwargs):
            nonlocal count
            count += 1
            if count == (3 if failure == "third-report" else 5):
                fail()
            return original(*args, **kwargs)
        monkeypatch.setattr(build, "write_json", write)
    elif failure == "runtime":
        monkeypatch.setattr(build, "observe_candidate", fail)
    elif failure == "source-change":
        original = build.source_manifest
        count = 0
        def changing(p):
            nonlocal count
            count += 1
            result = original(p)
            return result if count == 1 else dict(result, changed="0"*64)
        monkeypatch.setattr(build, "source_manifest", changing)
    else:
        monkeypatch.setattr(artifacts, "_publish_directory", fail)
    with pytest.raises((RuntimeError, ValueError), match="injected|changed"):
        build.create_bundle(paths, version=version)
    assert not target.exists()
    assert not list(paths.bundle_dir.glob(".mn-bundle-*"))


def test_published_bundle_is_immutable_on_repeat(real_bundle):
    paths, receipt = real_bundle
    before = bundle.tree_files(Path(receipt["bundle"]))
    with pytest.raises(ValueError, match="existing"):
        build.create_bundle(paths, version="test-1")
    assert bundle.tree_files(Path(receipt["bundle"])) == before


def test_stage3a_source_is_explicit_paired_and_distinct(real_bundle):
    paths, historical = real_bundle
    source = paths.ems_root/"outputs/mn_stage3a/generated/historical-02"
    assert source.is_dir(), "Stage 3A verified evidence is required for this comparison"
    result = build.create_bundle(paths, version="test-1", model_source="stage3a", conversion_directory=source)
    assert result["bundle_id"] != historical["bundle_id"]
    new = bundle.read_json(Path(result["bundle"])/"manifest.json")
    old = bundle.read_json(Path(historical["bundle"])/"manifest.json")
    assert new["xiidm"]["byte_sha256"] != old["xiidm"]["byte_sha256"]
    assert new["xiidm"]["canonical_xml_without_caseDate_sha256"] == old["xiidm"]["canonical_xml_without_caseDate_sha256"]
    assert result["static"]["file_count"] == 25
    with pytest.raises(ValueError, match="overridden"):
        build.selected_inputs(paths, "historical", source)
    with pytest.raises(ValueError, match="explicit"):
        build.selected_inputs(paths, "stage3a", None)


@pytest.mark.parametrize("failure", ["missing", "hash", "linked", "file-set"])
def test_actual_selected_input_failures(real_bundle, tmp_path, monkeypatch, failure):
    paths, _ = real_bundle
    original = build.selected_inputs
    def select(*args):
        result = original(*args)
        replacement = tmp_path/"input.json"
        if failure == "missing":
            result[bundle.ASSETS["map"]] = replacement
        elif failure == "hash":
            replacement.write_text('{"invalid":"asset"}')
            result[bundle.ASSETS["map"]] = replacement
        elif failure == "linked":
            replacement.symlink_to(result[bundle.ASSETS["map"]])
            result[bundle.ASSETS["map"]] = replacement
        else:
            del result["reports/pf_generator_comparison.csv"]
        return result
    monkeypatch.setattr(build, "selected_inputs", select)
    # selected_inputs' own link guard is additionally tested below; the final
    # guard catches aliases even if an injected reader supplies one late.
    with pytest.raises((OSError, ValueError)):
        build.create_bundle(paths, version="bad-input-"+failure)
    assert not (paths.bundle_dir/f"mn-osm-35k-historical-bad-input-{failure}").exists()
    assert not list(paths.bundle_dir.glob(".mn-bundle-*"))


@pytest.mark.parametrize("failure", ["nonconverged", "timeout", "changed-during-validation"])
def test_runtime_validator_failure_and_immutability(real_bundle, tmp_path, monkeypatch, failure):
    root, trusted = copied(real_bundle, tmp_path)
    paths, _ = real_bundle
    def probe(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("probe", 1)
        if failure == "nonconverged":
            return subprocess.CompletedProcess([], 2, json.dumps({"runtime_compatible": False, "error": "PF_NOT_CONVERGED"}), "")
        with (root/"reports/element_mapping.csv").open("ab") as stream:
            stream.write(b"\n")
        return subprocess.CompletedProcess([], 0, json.dumps({"runtime_compatible": True}), "")
    monkeypatch.setattr(bundle.subprocess, "run", probe)
    with pytest.raises(ValueError, match="failed|complete|mismatch"):
        bundle.validate_runtime(root, expected_manifest_sha256=trusted,
                                runtime_python=paths.runtime_python, ems_root=paths.ems_root)


def test_pf_nonconvergence_is_not_available(real_bundle):
    # Actual EMS service failure handling, using a fresh network object. The
    # solver failure is injected without changing a model or solver settings.
    paths, _ = real_bundle
    root = Path(real_bundle[1]["bundle"])
    script = """import sys
from pathlib import Path
sys.path[:0] = [str(Path(sys.argv[1])/'src'), sys.argv[1]]
from ai_ems import mongolia_security as ms
from pipelines.mn.bundle_probe import observe
def nonconverged(*args, **kwargs):
 raise ms.MongoliaModelUnavailable('PF_NOT_CONVERGED injected')
ms._run_base_lf = nonconverged
try:
 observe(Path(sys.argv[2]))
except ValueError as exc:
 assert 'PF_NOT_CONVERGED' in str(exc), str(exc)
else:
 raise AssertionError('Nonconverged PF became available')
"""
    done = subprocess.run([str(paths.runtime_python), "-B", "-I", "-c", script, str(paths.ems_root), str(root)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
