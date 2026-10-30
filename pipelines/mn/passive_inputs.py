"""Pinned passive-build inputs: logical provenance is independent of storage."""
from __future__ import annotations

import json
from pathlib import Path

from .artifacts import protected_model_hashes
from .inventory import sha256
from .paths import Paths


def contract():
    # Package resource location, never an inferred original-project root.
    value = json.loads(Path(__file__).with_name("passive_build_contract.json").read_text())
    if value["schema"] != "mn-passive-build-1":
        raise ValueError("Unsupported passive-build input contract")
    return value


def logical_input(paths: Paths, logical: str):
    relative = Path(logical)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Input identifier must be a relative logical path: {logical}")
    if relative.parts[0] == "pypsa-earth":
        root, tail = paths.earth_checkout, relative.parts[1:]
    else:
        root, tail = paths.source_project, relative.parts
    result = root.joinpath(*tail).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError(f"Input link escapes the selected source root: {logical}")
    return result


def read_config(paths: Paths, config_path=None):
    spec = contract()
    canonical = logical_input(paths, spec["config"])
    if sha256(canonical) != spec["config_sha256"]:
        raise ValueError("Pinned passive-build configuration hash changed")
    expected = json.loads(canonical.read_text())
    selected = canonical if config_path is None else Path(config_path).expanduser()
    if not selected.is_absolute():
        selected = paths.ems_root / selected
    if sha256(selected) != spec["config_sha256"]:
        raise ValueError("Selected configuration differs from the preserved baseline bytes")
    value = json.loads(selected.read_text())
    if value != expected:
        raise ValueError("Selected passive configuration differs from the baseline")
    return value


def input_manifest(config, paths: Paths):
    """Require the full original file set and hashes; no silent missing directories."""
    spec = contract()
    if config != read_config(paths):
        raise ValueError("Passive configuration must match the pinned baseline")
    names = set(config["protected_models"]) | set(spec["fixed_inputs"])
    for directory in spec["directories"]:
        base = logical_input(paths, directory)
        if not base.is_dir():
            raise FileNotFoundError(f"Required input directory missing: {directory}")
        names.update((Path(directory) / p.relative_to(base)).as_posix() for p in base.rglob("*") if p.is_file())
    if names != set(spec["inputs"]):
        raise ValueError(f"Passive input set changed: missing={sorted(set(spec['inputs'])-names)}, extra={sorted(names-set(spec['inputs']))}")
    actual = {name: sha256(logical_input(paths, name)) for name in sorted(names)}
    changed = [name for name, digest in actual.items() if digest != spec["inputs"][name]]
    if changed:
        raise ValueError("Audited input hashes changed: " + ", ".join(changed))
    prior = logical_input(paths, spec["historical_manifest"])
    if sha256(prior) != spec["historical_manifest_sha256"] or json.loads(prior.read_text()) != actual:
        raise ValueError("Historical input manifest differs from the preserved build evidence")
    protected_model_hashes(paths, config["protected_models"])
    reference = paths.earth_checkout / spec["reference_model"]
    if sha256(reference) != spec["reference_sha256"]:
        raise ValueError("Passive reference model hash changed")
    return actual
