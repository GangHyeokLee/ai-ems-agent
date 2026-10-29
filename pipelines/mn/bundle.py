"""Source-independent, standard-library-only validation of Mongolia bundles.

The manifest digest is an OUTSIDE trust input. A digest read from the same
untrusted bundle is not an approval. This module never resolves Paths, imports
PyPSA, or follows development paths recorded in legacy provenance reports.
"""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import xml.etree.ElementTree as ET

from .inventory import Checks, compare_numerical, sha256

SCHEMA = "mn-deployment-bundle-1"
CONTRACT = Path(__file__).with_name("bundle_runtime_contract.json")
MODEL = "models/mn35k_synthetic_powsybl.xiidm"
ASSETS = {"map": "assets/map.json", "pf_preview": "assets/pf_preview.json",
          "reference": "assets/reference.json"}
REPORTS = (
    "conversion_parameters.csv", "conversion_validation.json", "element_mapping.csv",
    "input_hashes.json", "n1_feasibility_results.csv", "n1_ui_compatible_results.json",
    "pf_bus_comparison.csv", "pf_generator_comparison.csv", "pf_line_comparison.csv",
    "pf_shunt_comparison.csv", "pf_summary.json", "pf_transformer_comparison.csv",
)
REPORT_EXTRAS = {"historical": ("pypowsybl_conversion_report.md",),
                 "stage3a": ("runtime_crosscheck.json", "verification_report.md", "conversion_provenance.json")}
VALIDATION = ("validation/runtime_contract.json", "validation/bundle_validation.json",
              "validation/runtime_compatibility.json", "validation/source_verification.json",
              "validation/license_review.json")


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=nonfinite)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def relative_name(name):
    require(isinstance(name, str) and bool(name) and "\\" not in name and ":" not in name,
            "Unsafe bundle relative path")
    p = PurePosixPath(name)
    require(not p.is_absolute() and all(x not in (".", "..") for x in p.parts)
            and p.as_posix() == name, "Unsafe bundle relative path")
    return name


def no_symlinks(path):
    p = Path(os.path.abspath(path))
    for part in (p, *p.parents):
        require(not part.is_symlink(), f"Symlink path is forbidden: {part}")
    return p


def tree_files(root):
    root = no_symlinks(root)
    require(root.is_dir(), "Bundle directory is missing")
    files, directories = {}, set()
    for folder, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            p = Path(folder) / name
            mode = p.lstat()
            require(not stat.S_ISLNK(mode.st_mode), f"Bundle symlink: {p}")
            relative = p.relative_to(root).as_posix()
            relative_name(relative)
            if stat.S_ISDIR(mode.st_mode):
                directories.add(relative)
            else:
                require(stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1 and mode.st_size > 0,
                        f"Bundle entry must be a nonempty regular file without hardlinks: {p}")
                files[relative] = {"bytes": mode.st_size, "sha256": sha256(p)}
    expected_dirs = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents
                     if parent.as_posix() != "."}
    require(directories == expected_dirs, "Unexpected empty bundle directory")
    return files


def xml_identity(path):
    root = ET.parse(path).getroot()
    date = root.attrib.pop("caseDate", None)
    canonical = ET.canonicalize(ET.tostring(root, encoding="unicode"))
    return {"byte_sha256": sha256(path), "caseDate": date,
            "canonical_xml_without_caseDate_sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def expected_files(source):
    require(source in REPORT_EXTRAS, "Unknown conversion source selection")
    return set(ASSETS.values()) | {MODEL} | {"reports/"+n for n in (*REPORTS, *REPORT_EXTRAS[source])} | set(VALIDATION)


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def check_relationships(root, manifest):
    """Recheck fixed source identities, XML parameters, mapping and asset lineage."""
    contract = read_json(CONTRACT)
    require(read_json(root/"validation/runtime_contract.json") == contract, "Runtime contract differs from reviewed EMS contract")
    require(manifest["grid_id"] == contract["grid_id"] and manifest["country"] == contract["country"]
            and manifest["scenario_id"] == contract["scenario_id"], "Bundle grid/scenario identity mismatch")
    require(manifest["model_status"] == {"synthetic": True, "actual_operating_data": False,
            "electrical_data_verified": False, "reliability": "MODEL_DATA_REQUIRES_REVIEW"}, "Synthetic model caveats missing")
    require(manifest["runtime_paths"] == dict(ASSETS, xiidm=MODEL, reports="reports"), "Runtime paths differ from relative bundle contract")
    require(manifest["counts"] == {k: contract[k] for k in ("map_counts", "analysis_counts", "reference_counts")}, "Bundle inventory metadata mismatch")
    for role, name in ASSETS.items():
        require(sha256(root/name) == contract["identities"]["pf" if role == "pf_preview" else role], f"Fixed asset identity mismatch: {role}")
    map_data, preview, reference = (read_json(root/ASSETS[k]) for k in ("map", "pf_preview", "reference"))
    meta = preview["metadata"]
    require(meta["grid_id"] == reference["grid_id"] == contract["grid_id"], "Asset grid mismatch")
    require(meta["map_json_sha256"] == reference["base_map_sha256"] == sha256(root/ASSETS["map"]), "Map/PF/reference binding mismatch")
    source_hash = contract["identities"]["pf_model"]
    require(manifest["netcdf_source_sha256"] == meta["source_sha256"] == source_hash, "NetCDF lineage mismatch")
    require(meta["scenario_id"] == contract["scenario_id"] and meta["synthetic_scenario"] is True
            and meta["actual_operating_data"] is False, "PF scenario/caveats mismatch")
    require(Counter(r["control"] for r in preview["buses"]) == contract["bus_controls"], "PV/PQ/Slack classification mismatch")
    for key in ("map_model_sha256", "poc_model_sha256"):
        role = "map_model" if key == "map_model_sha256" else "passive_model"
        require(meta[key] == contract["identities"][role], f"Fixed source lineage mismatch: {key}")
    validation = read_json(root/"reports/conversion_validation.json")
    hashes = read_json(root/"reports/input_hashes.json")
    require(hashes["input_model"] == {meta["source_model"]: source_hash}, "Conversion input hash mismatch")
    scenario = validation["input_scenario"]
    require(scenario["passed"] is True and scenario["meta_scenario_id"] == contract["scenario_id"]
            and scenario["slack_bus"] == "25" and scenario["actual_operation_validated"] is False,
            "Conversion scenario validation mismatch")
    identity = xml_identity(root/MODEL)
    require(identity == manifest["xiidm"], "XIIDM manifest identity mismatch")
    require(identity["canonical_xml_without_caseDate_sha256"] == contract["canonical_xml_without_caseDate_sha256"],
            "XIIDM structure/electrical parameters differ from reviewed model")
    require(validation["xiidm_reload"]["sha256"] == identity["byte_sha256"]
            and list(hashes["xiidm_output"].values()) == [identity["byte_sha256"]], "XIIDM and reports are not a paired set")
    # These comparison outputs are independent of caseDate and physical paths.
    for name in REPORTS:
        if name.endswith(".csv") or name in ("pf_summary.json", "n1_ui_compatible_results.json"):
            require(sha256(root/"reports"/name) == contract["identities"]["conversion."+name], f"Conversion reference differs: {name}")
    mapping = csv_rows(root/"reports/element_mapping.csv")
    require(len(mapping) == 561 and Counter(r["component"] for r in mapping) == contract["analysis_counts"], "Mapping inventory mismatch")
    require(len({(r["component"], r["original_id"]) for r in mapping}) == 561
            and len({r["iidm_id"] for r in mapping}) == 561, "Duplicate/missing equipment ID")
    tags = {"bus": "Bus", "line": "Line", "twoWindingsTransformer": "Transformer",
            "generator": "Generator", "load": "Load", "shuntCompensator": "Shunt"}
    nodes = [n for n in ET.parse(root/MODEL).getroot().iter() if n.tag.rsplit("}", 1)[-1] in tags]
    elements = {n.attrib["id"]: n for n in nodes}
    require(len(nodes) == len(elements) == 561 and set(elements) == {r["iidm_id"] for r in mapping}, "XIIDM/mapping IDs mismatch")
    for kind, field, name in (("Bus", "bus_id", "buses"), ("Line", "line_id", "lines"), ("Transformer", "transformer_id", "transformers")):
        ids = {str(r[field]) for r in preview[name]}
        require(ids <= {str(r[field]) for r in map_data[name]}, "PF equipment outside map")
        require(ids == {r["original_id"] for r in mapping if r["component"] == kind}, "PF/mapping ID mismatch")
    for row in mapping:
        if row["component"] in ("Line", "Transformer"):
            node = elements[row["iidm_id"]]
            require((node.get("voltageLevelId1"), node.get("voltageLevelId2")) ==
                    ("VL_"+row["original_bus0"], "VL_"+row["original_bus1"]), "Mapping endpoint mismatch")
    generators = csv_rows(root/"reports/pf_generator_comparison.csv")
    require({r["generator_id"] for r in generators} == {r["original_id"] for r in mapping if r["component"] == "Generator"}, "Generator reference IDs mismatch")
    return {"mapping_count": 561, "asset_lineage": True, "fixed_xml_parameters": True,
            "map_has_grid_id_field": False, "map_grid_identity": "fixed asset SHA256 plus PF/reference grid binding"}


def _inspect_bundle(directory, *, expected_manifest_sha256):
    root = no_symlinks(directory)
    files = tree_files(root)
    require(re.fullmatch(r"[a-f0-9]{64}", expected_manifest_sha256 or "") is not None, "Trusted manifest SHA256 must be supplied externally")
    require(files.get("manifest.json", {}).get("sha256") == expected_manifest_sha256, "Trusted manifest SHA256 mismatch")
    manifest = read_json(root/"manifest.json")
    require(manifest["schema"] == SCHEMA, "Unsupported bundle schema")
    source = manifest["model_source"]
    required = expected_files(source)
    require(manifest["required_files"] == sorted(required), "Required file contract mismatch")
    for name in manifest["files"]:
        relative_name(name)
    require(set(files) == required | {"manifest.json"} and set(manifest["files"]) == required, "Manifest file set mismatch")
    require({k: v for k, v in files.items() if k != "manifest.json"} == manifest["files"], "Bundle file byte hash/size mismatch")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", manifest["version"]) is not None, "Invalid bundle version")
    require(manifest["bundle_id"] == f"mn-osm-35k-{source}-{manifest['version']}", "Bundle ID/source mismatch")
    require(manifest["validation"] == {"static_integrity": True, "runtime_compatible": True, "activated": False}, "Bundle is incomplete or activation state invalid")
    require(manifest["external_distribution_approved"] is False, "External redistribution is not approved")
    facts = check_relationships(root, manifest)
    return {"passed": True, "activated": False, "manifest_sha256": expected_manifest_sha256,
            "file_count": len(files), "bytes": sum(x["bytes"] for x in files.values()), **facts}


def validate_bundle(directory, *, expected_manifest_sha256):
    result = _inspect_bundle(directory, expected_manifest_sha256=expected_manifest_sha256)
    root = Path(directory)
    manifest = read_json(root/"manifest.json")
    runtime = read_json(root/"validation/runtime_compatibility.json")
    require(runtime.get("runtime_compatible") is True and runtime.get("activated") is False,
            "Completed runtime validation evidence is required")
    checks = runtime["numerical_checks"]
    require(len(checks) == 186 and all(c["passed"] is True for c in checks), "Runtime numeric evidence is incomplete")
    require(runtime["numerical"]["loadflow_parameters"] == manifest["loadflow_parameters"] ==
            read_json(CONTRACT)["numerical_reference"]["loadflow_parameters"], "PF control contract mismatch")
    require(runtime["environment"] == manifest["runtime_environment"], "Runtime environment evidence mismatch")
    require(runtime["numerical"]["status"]["available"] is True
            and runtime["numerical"]["status"]["sensitivity_available"] is True, "Recorded model was unavailable")
    comparison = Checks()
    compare_numerical(runtime["numerical"], read_json(CONTRACT)["numerical_reference"], comparison)
    require(comparison.ready() and comparison.items == checks, "Recorded numerical evidence does not match fixed reference")
    source = read_json(root/"validation/source_verification.json")
    require(source["stage1_file_count"] == 87 and source["stage1_sha256"] == read_json(CONTRACT)["identities"],
            "Creation-time baseline identity record mismatch")
    require(read_json(root/"validation/bundle_validation.json")["activated"] is False,
            "Creation validation activation state mismatch")
    require(read_json(root/"validation/license_review.json") == read_json(Path(__file__).with_name("bundle_license_review.json")),
            "License review/caveats differ from reviewed contract")
    return result


def validate_runtime(directory, *, expected_manifest_sha256, runtime_python, ems_root, timeout=180):
    """Use the selected EMS interpreter; never set runtime selector variables."""
    before = validate_bundle(directory, expected_manifest_sha256=expected_manifest_sha256)
    command = [str(runtime_python), "-B", "-I", str(Path(__file__).with_name("bundle_probe.py")),
               "--bundle", str(Path(directory).absolute()), "--manifest-sha256", expected_manifest_sha256,
               "--ems-root", str(Path(ems_root).absolute())]
    try:
        done = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Runtime probe could not complete: {exc}") from exc
    try:
        result = json.loads(done.stdout.splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Runtime probe produced no valid result (exit {done.returncode})") from exc
    require(done.returncode == 0 and result.get("runtime_compatible") is True, f"Runtime compatibility failed: {result}")
    require(validate_bundle(directory, expected_manifest_sha256=expected_manifest_sha256) == before, "Bundle changed during runtime validation")
    return result
