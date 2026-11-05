"""Read-only pinned historical conversion inputs, independent of source location."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import sys

from .inventory import sha256
from .passive_inputs import logical_input
from .synthetic_inputs import source_manifest as synthetic_manifest


def contract():
    value = json.loads(Path(__file__).with_name("conversion_contract.json").read_text())
    if value["schema"] != "mn-conversion-1":
        raise ValueError("Unsupported conversion input contract")
    return value


def ems_input(paths, logical):
    relative = Path(logical)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("EMS evidence must be a relative path")
    result = (paths.ems_root/relative).resolve()
    if not result.is_relative_to(paths.ems_root.resolve()):
        raise ValueError("EMS evidence link escapes selected root")
    return result


def source_manifest(paths):
    paths.validate()
    spec = contract()
    # Preserve all original reports/configs/MN NetCDFs; new files cannot silently
    # fall outside the fixed manifest. Earth may be a separately located checkout.
    names = {"reports/"+p.relative_to(logical_input(paths,"reports")).as_posix()
             for p in logical_input(paths,"reports").rglob("*") if p.is_file()}
    names.update("configs/"+p.name for p in logical_input(paths,"configs").glob("*.json") if p.is_file())
    networks = logical_input(paths,"pypsa-earth/networks")
    names.update("pypsa-earth/networks/"+p.relative_to(networks).as_posix()
                 for p in networks.rglob("*.nc") if "MN" in p.relative_to(networks).as_posix())
    pinned = {p for p in spec["source_files"] if p.startswith(("reports/","configs/")) or p.endswith(".nc")}
    if names != pinned:
        raise ValueError(f"Conversion input set changed: missing={sorted(pinned-names)}, extra={sorted(names-pinned)}")
    hashes = {}
    for logical, expected in spec["source_files"].items():
        path = logical_input(paths,logical)
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes[logical] = sha256(path)
        if hashes[logical] != expected:
            raise ValueError(f"Conversion input SHA256 mismatch: {logical}")
    for logical, expected in spec["ems_files"].items():
        path = ems_input(paths,logical)
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes["ems:"+logical] = sha256(path)
        if hashes["ems:"+logical] != expected:
            raise ValueError(f"EMS reference SHA256 mismatch: {logical}")
    return hashes


def read_settings(paths, config_path=None):
    source_manifest(paths)
    synthetic_manifest(paths)
    spec = contract()
    path = logical_input(paths,spec["config"]) if config_path is None else Path(config_path).expanduser()
    if not path.is_absolute():
        path = paths.ems_root/path
    if sha256(path) != spec["source_files"][spec["config"]]:
        raise ValueError("Conversion configuration differs from pinned bytes")
    value = json.loads(path.read_text())
    if value["input_model_sha256"] != spec["source_files"][value["input_model"]]:
        raise ValueError("Historical conversion model identity/hash mismatch")
    for name in ("pypsa","pypowsybl"):
        if importlib.metadata.version(name) != spec["versions"][name]:
            raise ValueError(f"Unexpected conversion environment version: {name}")
    return value


def owned_code_hashes(paths):
    names = ("convert_mn35k_to_powsybl.py","mn35k_powsybl.py","conversion_inputs.py",
             "conversion_checks.py","conversion_contract.json","paths.py","artifacts.py",
             "passive_inputs.py","synthetic_inputs.py","synthetic_contract.json",
             "inventory.py","preflight.py","probe.py")
    return {"pipelines/mn/"+n: sha256(paths.ems_root/"pipelines/mn"/n) for n in names}


def environment():
    return {"python": sys.version, "executable": sys.executable,
            **{name: importlib.metadata.version(name) for name in ("pypsa","pypowsybl","numpy","pandas","scipy","xarray")}}
