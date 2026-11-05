"""Deterministic byte identity and semantic relationships; all inputs are read-only."""
from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CONTRACT = Path(__file__).with_name("baseline_contract.json")
SCHEMA = "mn-stage1-baseline-1"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path=CONTRACT):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if result.get("schema") != "mn-stage1-contract-1":
        raise ValueError("지원하지 않는 기준선 계약")
    seen = set()
    for item in result["files"]:
        if item["role"] in seen or not re.fullmatch('[a-f0-9]{64}', item["sha256"]):
            raise ValueError("중복 역할 또는 잘못된 기준 SHA256")
        if item["root"] not in {"ems", "source", "earth"}:
            raise ValueError("잘못된 입력 루트")
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("기준선 입력은 루트 아래 상대 경로여야 합니다.")
        seen.add(item["role"])
    return result


class Checks:
    def __init__(self):
        self.items = []

    def equal(self, name, actual, expected, scopes=("runtime",)):
        self.items.append(dict(name=name, passed=actual == expected, actual=actual,
                               expected=expected, scopes=list(scopes)))

    def error(self, name, message, scopes=("runtime",)):
        self.items.append(dict(name=name, passed=False, error=str(message), scopes=list(scopes)))

    def warning(self, name, message):
        self.items.append(dict(name=name, passed=None, warning=message, scopes=[]))

    def ready(self, profile="all"):
        return not any(c["passed"] is False and (profile == "all" or profile in c["scopes"])
                       for c in self.items)


def git_state(path):
    def git(*args):
        done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=15)
        if done.returncode:
            raise ValueError(f"Git 체크아웃을 확인할 수 없습니다: {path}")
        return done.stdout.strip()
    return {"head": git("rev-parse", "HEAD"), "status": git("status", "--porcelain"),
            "tracked_changes": git("diff", "--name-only", "HEAD")}


def collect(paths, contract, checks):
    roots = {"ems": paths.ems_root, "source": paths.source_project, "earth": paths.earth_checkout}
    files = {}
    for spec in contract["files"]:
        role = spec["role"]
        path = roots[spec["root"]] / spec["path"]
        if role == "xiidm":
            path = paths.runtime_xiidm
        elif role.startswith("conversion."):
            path = paths.runtime_reports / role.removeprefix("conversion.")
        record = dict(origin={"root": spec["root"], "path": spec["path"]}, path=str(path),
                      expected_sha256=spec["sha256"], exists=path.is_file())
        try:
            record.update(bytes=path.stat().st_size, sha256=sha256(path))
            checks.equal("file:" + role, record["sha256"], spec["sha256"], spec["scopes"])
        except OSError as exc:
            checks.error("file:" + role, exc, spec["scopes"])
        files[role] = record
    return files


def read_json(files, role):
    value = json.loads(Path(files[role]["path"]).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"객체 JSON이 필요합니다: {role}")
    return value


def csv_rows(files, role):
    with Path(files[role]["path"]).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def inspect_relationships(files, contract, checks):
    facts = {}
    data = {}
    for role in ("map", "pf", "reference", "conversion.conversion_validation.json",
                 "conversion.input_hashes.json", "conversion.pf_summary.json"):
        try:
            data[role] = read_json(files, role)
        except (OSError, ValueError, KeyError) as exc:
            checks.error("parse:" + role, exc)
    if not {"map", "pf", "reference"} <= data.keys():
        return facts
    map_data, pf, reference = (data[k] for k in ("map", "pf", "reference"))
    metadata = pf["metadata"]
    facts["map_counts"] = {key: len(map_data[field]) for key, field in
                           (("Bus", "buses"), ("Line", "lines"), ("Transformer", "transformers"), ("PlantRecord", "powerplants"))}
    facts["analysis_counts"] = {"Bus": len(pf["buses"]), "Line": len(pf["lines"]),
                                "Transformer": len(pf["transformers"]),
                                "Generator": metadata["generator_count"], "Load": len(pf["buses"]),
                                "Shunt": metadata["reactor_count"]}
    facts["reference_counts"] = {"Line": len(reference["lines"]), "Substation": len(reference["substations"])}
    for key in ("map_counts", "analysis_counts", "reference_counts"):
        checks.equal(key, facts[key], contract[key])
    facts["scenario"] = {key: metadata.get(key) for key in
                         ("scenario_id", "snapshot", "load_mw", "load_mvar", "reactor_mvar",
                          "synthetic_scenario", "actual_operating_data", "source_model", "source_sha256")}
    checks.equal("scenario_id", metadata["scenario_id"], contract["scenario_id"])
    checks.equal("synthetic_scenario", metadata["synthetic_scenario"], True)
    checks.equal("actual_operating_data", metadata["actual_operating_data"], False)
    for key, expected in contract["scenario_values"].items():
        checks.equal("scenario_value:" + key, metadata.get(key), expected)
    facts["bus_controls"] = dict(Counter(bus["control"] for bus in pf["buses"]))
    checks.equal("bus_controls", facts["bus_controls"], contract["bus_controls"])
    facts["slack_bus_ids"] = sorted(str(bus["bus_id"]) for bus in pf["buses"] if bus["control"] == "Slack")
    checks.equal("slack_bus", facts["slack_bus_ids"], [contract["slack_bus"]])
    for name, actual, role in [("pf_map_hash", metadata["map_json_sha256"], "map"),
                                ("reference_map_hash", reference["base_map_sha256"], "map"),
                                ("pf_source_hash", metadata["source_sha256"], "pf_model"),
                                ("pf_map_model_hash", metadata["map_model_sha256"], "map_model"),
                                ("pf_passive_model_hash", metadata["poc_model_sha256"], "passive_model")]:
        scopes = ("runtime",) if role == "map" else ("build", "conversion")
        checks.equal(name, actual, files[role].get("sha256"), scopes)
    # Every recorded exporter input is resolved through known role mappings, never arbitrary paths in JSON.
    provenance_roles = {"map_json": "map", "map_model": "map_model", "poc_model": "passive_model", "pf_model": "pf_model",
                        "acpf_config": "config.mn35k_acpf.json", "reactive_support_config": "config.mn35k_reactive_support.json"}
    provenance_roles.update({r.removeprefix("pf_input."): r for r in files if r.startswith("pf_input.")})
    for key, expected in metadata["source_provenance"].items():
        checks.equal("pf_provenance:" + key, files.get(provenance_roles.get(key), {}).get("sha256"), expected, ("build", "conversion"))
    ref_roles = {r.removeprefix("reference_input."): r for r in files if r.startswith("reference_input.")}
    ref_roles["base_map"] = "map"
    for key, expected in reference["source"]["inputs_sha256"].items():
        checks.equal("reference_provenance:" + key, files.get(ref_roles.get(key), {}).get("sha256"), expected, ("build",))
    validation = data.get("conversion.conversion_validation.json", {})
    source_hashes = data.get("conversion.input_hashes.json", {})
    checks.equal("conversion_scenario", validation.get("input_scenario", {}).get("meta_scenario_id"), contract["scenario_id"])
    checks.equal("conversion_input_verified", validation.get("input_scenario", {}).get("passed"), True)
    checks.equal("conversion_source_hash", source_hashes.get("input_model", {}).get(metadata["source_model"]), metadata["source_sha256"])
    checks.equal("xiidm_recorded_byte_hash", files["xiidm"].get("sha256"), validation.get("xiidm_reload", {}).get("sha256"))
    try:
        config = read_json(files, "config.mn35k_powsybl_conversion.json")
        facts["conversion_settings"] = {key: config[key] for key in ("expected_scenario_id", "expected_bus_controls", "expected_slack_bus", "openloadflow", "tolerances", "synthetic_generator_q_limits_mvar")}
        checks.equal("config_scenario", config["expected_scenario_id"], contract["scenario_id"], ("conversion",))
        checks.equal("config_slack", config["expected_slack_bus"], contract["slack_bus"], ("conversion",))
        checks.equal("config_bus_controls", config["expected_bus_controls"], contract["bus_controls"], ("conversion",))
        checks.equal("xiidm_export_hash", source_hashes.get("xiidm_output", {}).get(config["powsybl_output"]), files["xiidm"].get("sha256"))
        checks.equal("source_hash_from_config", config["input_model_sha256"], metadata["source_sha256"], ("conversion",))
    except (OSError, ValueError, KeyError) as exc:
        checks.error("conversion_config", exc, ("conversion",))
    pf_sets = {kind: {str(row[field]) for row in pf[name]} for kind, name, field in
               (("Bus", "buses", "bus_id"), ("Line", "lines", "line_id"), ("Transformer", "transformers", "transformer_id"))}
    for kind, name, field in (("Bus", "buses", "bus_id"), ("Line", "lines", "line_id"), ("Transformer", "transformers", "transformer_id")):
        map_ids = {str(row[field]) for row in map_data[name]}
        checks.equal("pf_subset:" + kind, pf_sets[kind] <= map_ids, True)
        facts["excluded_" + name] = sorted(map_ids - pf_sets[kind])
    try:
        mapping = csv_rows(files, "conversion.element_mapping.csv")
        counts = dict(sorted(Counter(r["component"] for r in mapping).items()))
        checks.equal("mapping_inventory", counts, contract["analysis_counts"])
        checks.equal("mapping_unique", len({(r["component"], r["original_id"]) for r in mapping}), len(mapping))
        checks.equal("mapping_iidm_unique", len({r["iidm_id"] for r in mapping}), len(mapping))
        for kind, ids in pf_sets.items():
            checks.equal("mapping_pf_ids:" + kind, sorted(r["original_id"] for r in mapping if r["component"] == kind), sorted(ids))
        root = ET.parse(files["xiidm"]["path"]).getroot()
        tags = {"bus": "Bus", "line": "Line", "twoWindingsTransformer": "Transformer", "generator": "Generator", "load": "Load", "shuntCompensator": "Shunt"}
        elements = {node.attrib["id"]: node for node in root.iter() if node.tag.rsplit('}', 1)[-1] in tags}
        facts["xiidm_counts"] = dict(sorted(Counter(tags[node.tag.rsplit('}', 1)[-1]] for node in elements.values()).items()))
        checks.equal("xiidm_inventory", facts["xiidm_counts"], contract["analysis_counts"])
        checks.equal("xiidm_mapping_ids", sorted(elements), sorted(r["iidm_id"] for r in mapping))
        for kind in ("Line", "Transformer"):
            matched = all((elements[r["iidm_id"]].get("voltageLevelId1"), elements[r["iidm_id"]].get("voltageLevelId2")) ==
                          ("VL_" + r["original_bus0"], "VL_" + r["original_bus1"]) for r in mapping if r["component"] == kind)
            checks.equal("xiidm_endpoints:" + kind, matched, True)
        generators = csv_rows(files, "conversion.pf_generator_comparison.csv")
        checks.equal("generator_reference_ids", sorted(r["generator_id"] for r in generators), sorted(r["original_id"] for r in mapping if r["component"] == "Generator"))
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        checks.error("mapping_xiidm", exc)
    return facts


def compare_files(current, previous, checks):
    checks.equal("comparison_schema", previous.get("schema"), SCHEMA)
    checks.equal("comparison_role_set", sorted(current), sorted(previous.get("files", {})))
    for role, record in current.items():
        actual, expected = record.get("sha256"), previous.get("files", {}).get(role, {}).get("sha256")
        valid = all(isinstance(v, str) and re.fullmatch('[a-f0-9]{64}', v) is not None for v in (actual, expected))
        checks.equal("comparison_hash_present:" + role, valid, True)
        checks.equal("comparison_hash:" + role, actual, expected)


def write_report(paths, destination, report):
    """Identical replays are harmless; a different existing report is never overwritten."""
    target = paths.safe_destination(Path(destination))
    content = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != content:
            raise ValueError(f"기존 보고서 내용이 다릅니다. 새 경로를 지정하세요: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        stream.write(content)


def compare_numerical(actual, reference, checks):
    """Compare observations only; all calculations remain in the existing runtime services."""
    import math
    if reference.get("schema") == SCHEMA:
        reference = reference["numerical"]["result"]
    checks.equal("numeric_reference:controls", actual["loadflow_parameters"], reference["loadflow_parameters"])
    checks.equal("numeric_reference:scenario", actual["status"]["scenario_id"], reference["status"]["scenario_id"])
    checks.equal("numeric_reference:sensitivity_definition", actual["sensitivity_definition"], reference["sensitivity_definition"])
    checks.equal("numeric_reference:source_sha256",
                 actual["status"]["base_check"]["provenance"]["source_sha256"],
                 reference["status"]["base_check"]["provenance"]["source_sha256"])

    def near(name, a, b, tolerance):
        ok = (isinstance(a, (int, float)) and isinstance(b, (int, float))
              and math.isfinite(a) and math.isfinite(b) and abs(a-b) <= tolerance)
        checks.equal(name, ok, True)

    for key in ("slack_p_mw", "slack_q_mvar", "reactor_q_absorbed_mvar"):
        near("numeric_reference:base:" + key, actual["status"]["base_check"]["comparison"][key],
             reference["status"]["base_check"]["comparison"][key], 1e-6)
    for kind, key in (("n1", "outage_line_id"), ("sensitivity", "monitored_line_id")):
        current = {row[key]: row for row in actual[kind]}
        previous = {row[key]: row for row in reference[kind]}
        checks.equal("numeric_reference:cases:" + kind, sorted(current), sorted(previous))
        for identifier in sorted(current.keys() & previous.keys()):
            a, b = current[identifier], previous[identifier]
            prefix = f"numeric_reference:{kind}:{identifier}:"
            for field in (("post_status", "isolated_buses", "isolated_load_mw", "violated_equipment_count")
                          if kind == "n1" else ("analysis_status", "outage_line_id", "candidate_count", "unit")):
                checks.equal(prefix + field, a[field], b[field])
            if kind == "n1":
                for field, tolerance in (("post_voltage_min_pu", 1e-8), ("post_voltage_max_pu", 1e-8),
                                         ("max_line_p_change_mw", 1e-5), ("max_line_q_change_mvar", 1e-5),
                                         ("post_max_line_loading_percent", 1e-5)):
                    near(prefix + field, a[field], b[field], tolerance)
            else:
                ac = {r["generator_id"]: r["sensitivity"] for r in a["candidates"]}
                bc = {r["generator_id"]: r["sensitivity"] for r in b["candidates"]}
                checks.equal(prefix + "generator_ids", sorted(ac), sorted(bc))
                for generator in sorted(ac.keys() & bc.keys()):
                    near(prefix + generator, ac[generator], bc[generator], 1e-8)
                checks.equal(prefix + "finite_difference_verified", a["finite_difference_check"]["passed"], True)
