"""Create verification-only deployment candidates; no installation or activation.

Creation checks the development source baseline. Independent bundle validators
do not import this module or inspect those external source paths.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from . import artifacts, bundle
from .conversion_inputs import contract as conversion_contract, source_manifest
from .inventory import git_state, load_contract, sha256
from .passive_inputs import logical_input


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def observe_candidate(paths, staged, digest):
    command = [str(paths.runtime_python), "-B", "-I", str(Path(__file__).with_name("bundle_probe.py")),
               "--bundle", str(staged), "--manifest-sha256", digest, "--ems-root", str(paths.ems_root),
               "--creation-candidate"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Candidate runtime probe could not complete: {exc}") from exc
    try:
        result = json.loads(done.stdout.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Candidate runtime probe failed (exit {done.returncode})") from exc
    bundle.require(done.returncode == 0 and result.get("runtime_compatible") is True,
                   f"Candidate runtime compatibility failed: {result}")
    return result


def bundle_destination(paths, destination):
    paths.validate()
    # Resolve manually constructed Paths too; validate() alone assumes resolved fields.
    work, output, area = (p.resolve() for p in (paths.work_dir, paths.output_dir, paths.bundle_dir))
    for a, b in ((work, output), (area, work), (area, output)):
        bundle.require(not a.is_relative_to(b) and not b.is_relative_to(a), "Overlapping pipeline directories")
    raw = Path(destination).expanduser()
    raw = raw if raw.is_absolute() else paths.ems_root/raw
    bundle.no_symlinks(raw)
    bundle.no_symlinks(paths.bundle_dir)
    target = paths.safe_destination(raw)
    bundle.require(target != area and target.is_relative_to(area), "Bundle must be inside bundle_dir")
    bundle.require(not target.exists(), "Refusing to replace an existing bundle/file/link")
    return target


@contextmanager
def new_bundle_directory(paths, destination):
    target = bundle_destination(paths, destination)
    before = artifacts.protected_model_hashes(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    parent_identity = (target.parent.stat().st_dev, target.parent.stat().st_ino)
    with tempfile.TemporaryDirectory(prefix=".mn-bundle-", dir=target.parent) as temporary:
        staged = Path(temporary)
        yield staged
        bundle.tree_files(staged)
        bundle.require(before == artifacts.protected_model_hashes(paths), "Protected input changed during bundle creation")
        bundle.require(bundle_destination(paths, destination) == target
                       and parent_identity == (target.parent.stat().st_dev, target.parent.stat().st_ino),
                       "Bundle destination changed before publication")
        # Pin the checked parent inode across the final rename. A path-only
        # recheck followed by rename would leave a parent-symlink swap window.
        # Reuse the existing no-replace primitive through the pinned Linux fd;
        # other systems fail closed rather than replacing a prior directory.
        bundle.require(Path("/proc/self/fd").is_dir() and hasattr(os, "O_NOFOLLOW"),
                       "Pinned no-replace bundle publication requires Linux /proc/self/fd")
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            pinned = os.fstat(descriptor)
            bundle.require((pinned.st_dev, pinned.st_ino) == parent_identity, "Publication parent inode changed")
            parent = Path(f"/proc/self/fd/{descriptor}")
            artifacts._publish_directory(parent/staged.name, parent/target.name)
        finally:
            os.close(descriptor)


def selected_inputs(paths, model_source, conversion_directory):
    spec = conversion_contract()
    if model_source == "historical":
        bundle.require(conversion_directory is None, "Historical model/reports cannot be independently overridden")
        xiidm = logical_input(paths, "pypsa-earth/networks/MN_35k_poc/powsybl/mn35k_synthetic_powsybl.xiidm")
        reports = logical_input(paths, "reports/pypowsybl_conversion")
    elif model_source == "stage3a":
        bundle.require(conversion_directory is not None, "Stage 3A requires an explicit complete conversion directory")
        reports = Path(conversion_directory)
        reports = reports if reports.is_absolute() else paths.ems_root/reports
        bundle.no_symlinks(reports)
        xiidm = reports/"mn35k_synthetic_powsybl.xiidm"
        provenance = bundle.read_json(reports/"conversion_provenance.json")
        bundle.require(provenance["purpose"] == "CONVERSION_VERIFICATION_ONLY"
                       and provenance["source_model_kind"] == "HISTORICAL_45_GENERATOR_BASELINE"
                       and provenance["conversion_verified"] is True and provenance["runtime_compatible"] is True
                       and provenance["activated"] is False, "Unverified Stage 3A conversion")
        expected = set(provenance["artifact_sha256"]) | {"conversion_provenance.json"}
        bundle.require(set(bundle.tree_files(reports)) == expected, "Stage 3A conversion file set mismatch")
        for name, digest in provenance["artifact_sha256"].items():
            bundle.relative_name(name)
            bundle.require(sha256(reports/name) == digest, f"Stage 3A artifact hash mismatch: {name}")
        bundle.require(provenance["source_inputs"] == source_manifest(paths), "Stage 3A source lineage mismatch")
    else:
        raise ValueError("Unknown model source; choose historical or stage3a explicitly")
    inputs = {bundle.MODEL: xiidm}
    inputs.update({"reports/"+name: reports/name for name in (*bundle.REPORTS, *bundle.REPORT_EXTRAS[model_source])})
    roles = {"map": "mn-osm-35k-map.json", "pf_preview": "mn35k-synthetic-pf-preview.json",
             "reference": "mn-osm-35k-reference-layers.json"}
    inputs.update({bundle.ASSETS[k]: paths.ems_root/"data/grids"/name for k, name in roles.items()})
    # All selected content must be real file bytes, never a linked dependency.
    for path in inputs.values():
        bundle.no_symlinks(path)
        bundle.require(path.is_file() and path.stat().st_nlink == 1, f"Missing or linked bundle input: {path}")
    if model_source == "historical":
        for name, path in inputs.items():
            if name.startswith("reports/"):
                bundle.require(sha256(path) == spec["source_files"]["reports/pypowsybl_conversion/"+path.name], "Historical report changed")
    return inputs


def create_bundle(paths, *, version, model_source="historical", conversion_directory=None):
    bundle.require(isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version), "Invalid bundle version")
    bundle_id = f"mn-osm-35k-{model_source}-{version}"
    target = paths.bundle_dir/bundle_id
    bundle_destination(paths, target)
    origins = source_manifest(paths)  # Existing 130 source/EMS pins; creation only.
    stage1 = load_contract()
    roots = {"ems": paths.ems_root, "source": paths.source_project, "earth": paths.earth_checkout}
    checked = {}
    for spec in stage1["files"]:
        value = sha256(roots[spec["root"]]/spec["path"])
        bundle.require(value == spec["sha256"], f"Stage 1 baseline hash mismatch: {spec['role']}")
        checked[spec["role"]] = value
    states = {name: git_state(path) for name, path in (("ems", paths.ems_root), ("source", paths.source_project), ("earth", paths.earth_checkout))}
    for name in ("source", "earth"):
        bundle.require(states[name]["head"] == conversion_contract()["repositories"][name], "Unexpected source Git commit")
    inputs = selected_inputs(paths, model_source, conversion_directory)
    before = {name: sha256(p) for name, p in inputs.items()}
    contract = bundle.read_json(bundle.CONTRACT)
    with new_bundle_directory(paths, target) as staged:
        for name, source in inputs.items():
            destination = staged/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
            bundle.require(sha256(destination) == before[name], "Input changed while copying bundle")
        # Contract is fixed, reviewed numeric evidence, not external development paths.
        write_json(staged/"validation/runtime_contract.json", contract)
        write_json(staged/"validation/license_review.json", bundle.read_json(Path(__file__).with_name("bundle_license_review.json")))
        code_hashes = {p.name: sha256(p) for p in Path(__file__).parent.glob("bundle*") if p.suffix in (".py", ".json")}
        write_json(staged/"validation/source_verification.json", {
            "creation_only": True, "stage1_file_count": len(checked), "stage1_sha256": checked,
            "conversion_input_sha256": origins, "repositories": states,
            "physical_sources_provenance_only": {n: str(p) for n, p in inputs.items()},
            "packaging_code_sha256": code_hashes,
            "original_conversion_code_sha256": {k: v for k, v in origins.items() if k.startswith("scripts/")},
        })
        write_json(staged/"validation/bundle_validation.json", {
            "copied_bytes_verified": True, "stage1_87_verified_at_creation": True,
            "independent_checks": "trusted manifest; exact files; SHA256; fixed runtime contract; XML; asset lineage; IDs",
            "not_repeated_without_sources": "external NetCDF/raw/config/source code existence and Git inspection",
            "activated": False,
        })
        # Provisional evidence exists only in the private directory. Publication
        # requires the actual EMS subprocess result, replaced below, and rehashing.
        write_json(staged/"validation/runtime_compatibility.json", {"status": "PENDING", "activated": False})
        manifest = {
            "schema": bundle.SCHEMA, "bundle_id": bundle_id, "version": version,
            "country": "MN", "grid_id": "mn-osm-35k", "scenario_id": contract["scenario_id"],
            "model_source": model_source,
            "model_status": {"synthetic": True, "actual_operating_data": False, "electrical_data_verified": False, "reliability": "MODEL_DATA_REQUIRES_REVIEW"},
            "ems_git_commit": states["ems"]["head"], "source_git_commits": contract["repositories"],
            "counts": {k: contract[k] for k in ("map_counts", "analysis_counts", "reference_counts")},
            "netcdf_source_sha256": contract["identities"]["pf_model"], "xiidm": bundle.xml_identity(staged/bundle.MODEL),
            "netcdf_source_identifier": bundle.read_json(staged/bundle.ASSETS["pf_preview"])["metadata"]["source_model"],
            "conversion_code_sha256": bundle.read_json(staged/"reports/input_hashes.json")["scripts"],
            "packaging_code_sha256": code_hashes,
            "runtime_paths": dict(bundle.ASSETS, xiidm=bundle.MODEL, reports="reports"),
            "asset_model_relationships": {"map_identity": contract["identities"]["map"], "pf_source": contract["identities"]["pf_model"], "mapping_equipment": 561},
            "engines": bundle.read_json(staged/"reports/pf_summary.json")["versions"],
            "loadflow_parameters": contract["numerical_reference"]["loadflow_parameters"],
            "validation": {"static_integrity": True, "runtime_compatible": True, "activated": False},
            "external_distribution_approved": False,
            "required_files": sorted(bundle.expected_files(model_source)),
        }
        def manifest_digest():
            manifest["files"] = {k: v for k, v in bundle.tree_files(staged).items() if k != "manifest.json"}
            # Only our private provisional manifest may be replaced.
            (staged/"manifest.json").unlink(missing_ok=True)
            write_json(staged/"manifest.json", manifest)
            return sha256(staged/"manifest.json")
        digest = manifest_digest()
        runtime = observe_candidate(paths, staged, digest)
        (staged/"validation/runtime_compatibility.json").unlink()
        write_json(staged/"validation/runtime_compatibility.json", runtime)
        manifest["runtime_environment"] = runtime["environment"]
        digest = manifest_digest()
        final = bundle.validate_bundle(staged, expected_manifest_sha256=digest)
        bundle.require(origins == source_manifest(paths), "External input changed during bundle validation")
        for spec in stage1["files"]:
            bundle.require(sha256(roots[spec["root"]]/spec["path"]) == checked[spec["role"]], "Stage 1 input changed during creation")
        for name, path in inputs.items():
            bundle.no_symlinks(path)
            bundle.require(path.stat().st_nlink == 1 and sha256(path) == before[name], "Selected input changed during creation")
        bundle.require(code_hashes == {p.name: sha256(p) for p in Path(__file__).parent.glob("bundle*") if p.suffix in (".py", ".json")},
                       "Packaging code/contract changed during creation")
    return {"bundle": str(target), "bundle_id": bundle_id, "trusted_manifest_sha256": digest,
            "static": final, "runtime_compatible": True, "activated": False}
