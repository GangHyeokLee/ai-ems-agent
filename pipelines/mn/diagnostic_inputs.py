"""Pinned evidence and atomic publication of diagnostic-only Mongolia analyses."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import tempfile

from .artifacts import artifact_destination, new_artifact_directory
from .inventory import sha256
from .passive_inputs import logical_input
from .synthetic_inputs import read_settings as synthetic_settings


def contract():
    value = json.loads(Path(__file__).with_name("diagnostic_contract.json").read_text())
    if value["schema"] != "mn-diagnostics-1":
        raise ValueError("Unsupported diagnostic evidence contract")
    return value


def source_manifest(paths):
    """Require every fixed input and protected historical output, without Git reads."""
    paths.validate()
    spec = contract()
    for directory in spec["directories"]:
        root = logical_input(paths, directory)
        if not root.is_dir():
            raise FileNotFoundError(root)
        actual = {(Path(directory)/p.relative_to(root)).as_posix()
                  for p in root.rglob("*") if p.is_file()}
        expected = {p for p in spec["files"] if p.startswith(directory+"/")}
        if actual != expected:
            raise ValueError(f"Diagnostic evidence file set changed: {directory}")
    hashes = {}
    for name, expected in spec["files"].items():
        path = logical_input(paths, name)
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes[name] = sha256(path)
        if hashes[name] != expected:
            raise ValueError(f"Diagnostic evidence SHA256 mismatch: {name}")
    return hashes


def base_settings(paths):
    source_manifest(paths)
    return synthetic_settings(paths)


def read_config(paths, logical, selected=None):
    source_manifest(paths)
    original = logical_input(paths, logical)
    path = original if selected is None else Path(selected).expanduser()
    if not path.is_absolute():
        path = paths.ems_root/path
    if sha256(path) != contract()["files"][logical]:
        raise ValueError(f"Selected diagnostic config SHA256 mismatch: {logical}")
    value = json.loads(path.read_text())
    if value["required_pypsa_version"] != importlib.metadata.version("pypsa"):
        raise ValueError("Unexpected diagnostic PyPSA version")
    return value


def owned_code_hashes(paths):
    names = ("audit_mn35k_plants.py", "run_mn35k_data_informed_pf.py", "diagnose_mn35k_reactive.py",
             "audit_mn35k_220kv_lines.py", "diagnostic_inputs.py", "diagnostic_contract.json",
             "paths.py", "artifacts.py", "passive_inputs.py", "synthetic_inputs.py", "synthetic_contract.json",
             "run_mn35k_acpf.py", "run_mn35k_reactive_support.py", "mn35k_refinement.py",
             "mn35k_lengths.py", "audit_mn35k_parameters.py", "validate_mn35k_poc.py")
    return {"pipelines/mn/"+name: sha256(paths.ems_root/"pipelines/mn"/name) for name in names}


def analysis_bundle(paths, destination, expected_files, generate, *, optional_files=()):
    """Publish a complete analysis, never an accepted or activated operating model.

    An expected PF nonconvergence is reportable evidence, not an I/O failure or
    usable model. Unexpected exceptions publish nothing. An optional experimental
    NetCDF, if the original calculation converges, stays in this isolated bundle.
    The first private workspace accommodates this conditional original artifact;
    the existing no-replace helper exclusively publishes the validated file set.
    """
    target = artifact_destination(paths, Path(destination))
    before = source_manifest(paths)
    required, optional = set(expected_files), set(optional_files)
    if (not required or required & optional or "analysis_provenance.json" in required | optional
            or any(Path(n).name != n or n in (".", "..") for n in required | optional)):
        raise ValueError("Expected analysis artifacts must be unique flat file names")
    base_config, _ = base_settings(paths)
    provenance = {"source_inputs": before, "source_commits": contract()["repositories"],
                  "owned_code_sha256": owned_code_hashes(paths),
                  "purpose": "DIAGNOSTIC_ONLY_NOT_EMS_ACTIVATION", "model_usable": False,
                  "model_activated": False,
                  "environment": {"python": sys.version, **{p: importlib.metadata.version(p)
                      for p in ("pypsa", "numpy", "pandas", "scipy", "matplotlib", "shapely", "pyproj")}}}
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mn-analysis-", dir=target.parent) as temporary:
        output = Path(temporary)
        result = generate(output)
        if not isinstance(result, dict):
            raise ValueError("Analysis did not return an explicit result")
        actual = {p.name for p in output.iterdir()}
        if not required <= actual or not actual <= required | optional:
            raise ValueError(f"Incomplete analysis bundle: missing={sorted(required-actual)}, extra={sorted(actual-required-optional)}")
        # Validate before copying: never dereference a generated link into the
        # second private directory or disguise an existing hardlink as a new file.
        for p in output.iterdir():
            if p.is_symlink() or not p.is_file() or p.stat().st_nlink != 1 or not p.stat().st_size:
                raise ValueError(f"Analysis output must be a new nonempty regular file: {p.name}")
            if p.suffix == ".json":
                json.loads(p.read_text(), parse_constant=lambda v: _reject_nonfinite(v))
        if source_manifest(paths) != before:
            raise ValueError("Diagnostic evidence changed during analysis")
        provenance.update(analysis_completed=True, result=result,
                          artifact_sha256={p.name: sha256(p) for p in sorted(output.iterdir())})
        (output/"analysis_provenance.json").write_text(
            json.dumps(provenance, indent=2, ensure_ascii=False, allow_nan=False)+"\n")
        names = (*sorted(actual), "analysis_provenance.json")
        with new_artifact_directory(paths, target, expected_files=names,
                                    protected_models=base_config["protected_models"]) as staged:
            for name in names:
                shutil.copyfile(output/name, staged/name)
            if source_manifest(paths) != before:
                raise ValueError("Diagnostic evidence changed before publication")
    return {"artifact": str(target), **result, "model_usable": False, "model_activated": False,
            "sha256": {p.name: sha256(p) for p in sorted(target.iterdir())}}


def _reject_nonfinite(value):
    raise ValueError(f"Nonfinite JSON constant in diagnostic output: {value}")
