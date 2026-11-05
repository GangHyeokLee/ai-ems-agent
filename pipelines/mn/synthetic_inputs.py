"""Pinned synthetic inputs and all-or-nothing verification bundle publication.

Logical source names remain provenance identifiers. All physical paths are
injected; this module does not import a source-project script or activate EMS.
"""
from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path

from .artifacts import artifact_destination, new_artifact_directory
from .inventory import sha256
from .passive_inputs import logical_input


class ScenarioFailure(RuntimeError):
    """Numerical failure, with diagnostics that must never become a model bundle."""

    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(diagnostics["status"])


def contract():
    return json.loads(Path(__file__).with_name("synthetic_contract.json").read_text())


def source_manifest(paths):
    """Verify exact original protection sets and pins, including old PF artifacts."""
    paths.validate()
    expected = contract()
    # Missing directories and newly added files cannot silently change a manifest.
    for directory in expected["directories"]:
        root = logical_input(paths, directory)
        if not root.is_dir():
            raise FileNotFoundError(root)
        actual = {str(p.relative_to(paths.source_project)) for p in root.rglob("*") if p.is_file()}
        pinned = {p for p in expected["files"] if p.startswith(directory + "/")}
        if actual != pinned:
            raise ValueError(f"Synthetic input set changed: {directory}")
    actual = {}
    for name, digest in expected["files"].items():
        path = logical_input(paths, name)
        if not path.is_file():
            raise FileNotFoundError(path)
        actual[name] = sha256(path)
        if actual[name] != digest:
            raise ValueError(f"Synthetic input SHA256 mismatch: {name}")
    return actual


def read_settings(paths, *, config_path=None, reactive_path=None):
    source_manifest(paths)
    specs = contract()
    loaded = {}
    for kind, selected in (("initial", config_path), ("reactive", reactive_path)):
        logical = specs["configs"][kind]
        original = logical_input(paths, logical)
        path = Path(selected) if selected is not None else original
        if not path.is_absolute():
            path = paths.ems_root / path
        # CLI-selected copies are allowed only with the exact pinned bytes.
        if sha256(path) != specs["files"][logical]:
            raise ValueError(f"Synthetic config SHA256 mismatch: {kind}")
        loaded[kind] = json.loads(path.read_text())
    config, settings = loaded["initial"], loaded["reactive"]
    if importlib.metadata.version("pypsa") != config["required_pypsa_version"]:
        raise ValueError("Unexpected PyPSA version")
    if settings["base_config"] != specs["configs"]["initial"]:
        raise ValueError("Reactive base config identity changed")
    if settings["output_model"] == config["output_model"]:
        raise ValueError("Reactive output identity collides with initial scenario")
    ids = [item["id"] for item in settings["scenarios"]]
    if not ids or len(ids) != len(set(ids)) or not config["trials"]:
        raise ValueError("Invalid scenario/trial identities")
    if ids[-1] != "reduced_27_q_limited_with_reactor":
        raise ValueError("Pinned final reactive scenario is missing")
    # Fixed protected hashes cannot be replaced by selected settings.
    for name, digest in config["protected_models"].items():
        if specs["files"].get(name) != digest:
            raise ValueError(f"Protected model identity/hash conflict: {name}")
    if config["input_model"] not in config["protected_models"]:
        raise ValueError("Unpinned passive input")
    return config, settings


def owned_code_hashes(paths):
    names = ("run_mn35k_acpf.py", "run_mn35k_reactive_support.py", "synthetic_inputs.py",
             "synthetic_contract.json", "paths.py", "artifacts.py", "passive_inputs.py",
             "audit_mn35k_parameters.py", "mn35k_refinement.py", "validate_mn35k_poc.py")
    return {"pipelines/mn/" + name: sha256(paths.ems_root / "pipelines/mn" / name) for name in names}


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def scenario_bundle(paths, destination, expected_files, generate, config):
    """Publish only complete, validated success; PF failures get diagnostics only.

    Unexpected I/O/report failures clean the private directory without publishing
    anything. A numerical failure can publish a distinct *.failed diagnostics
    directory, marked accepted=false and containing no NetCDF or success CSV.
    """
    target = artifact_destination(paths, Path(destination))
    before = source_manifest(paths)
    provenance = {"source_inputs": before, "owned_code_sha256": owned_code_hashes(paths),
                  "purpose": "VERIFICATION_ONLY_NOT_EMS_ACTIVATION",
                  "environment": {"python": sys.version, **{name: importlib.metadata.version(name)
                      for name in ("pypsa", "numpy", "pandas", "scipy", "xarray")}}}
    try:
        with new_artifact_directory(paths, target, expected_files=(*expected_files, "build_provenance.json"),
                                    protected_models=config["protected_models"]) as staged:
            result = generate(staged, logical_input(paths, config["input_model"]))
            if result.get("status") != "AC_PF_CONVERGED" or not result.get("validation", {}).get("balance_verified"):
                raise ScenarioFailure({"status": "RESULT_NOT_ACCEPTED", "result": result})
            # All JSON must parse before publication. The original generators check
            # result tables, balance, preservation and every exported result field.
            for entry in staged.glob("*.json"):
                json.loads(entry.read_text())
            provenance.update(accepted=True, result=result,
                              artifact_sha256={p.name: sha256(p) for p in sorted(staged.iterdir())})
            _write_json(staged / "build_provenance.json", provenance)
            if source_manifest(paths) != before:
                raise ValueError("Synthetic inputs changed during generation")
    except ScenarioFailure as failure:
        if source_manifest(paths) != before:
            raise ValueError("Synthetic inputs changed during failed PF") from failure
        failed_target = target.with_name(target.name + ".failed")
        with new_artifact_directory(paths, failed_target, expected_files=("failure.json",),
                                    protected_models=config["protected_models"]) as staged:
            _write_json(staged / "failure.json", {**provenance, "accepted": False,
                        "status": "FAILED_NOT_A_MODEL", "diagnostics": failure.diagnostics})
            if source_manifest(paths) != before:
                raise ValueError("Synthetic inputs changed while recording failure")
        raise
    return {"artifact": str(target), **result,
            "sha256": {p.name: sha256(p) for p in sorted(target.iterdir())}}
