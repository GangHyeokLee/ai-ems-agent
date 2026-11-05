"""Extra validation gates; no changes to the original electrical calculations."""
from __future__ import annotations

from dataclasses import replace
import json
import hashlib
import xml.etree.ElementTree as ET

import pandas as pd

from . import mn35k_powsybl as conv
from .inventory import Checks, compare_numerical, load_contract, sha256
from .preflight import run_probe


def assert_mapping(source, model):
    mapping = model.mapping
    if mapping.duplicated(["component","original_id"]).any() or mapping.iidm_id.duplicated().any():
        raise ValueError("Conversion mapping contains duplicate IDs")
    pd.testing.assert_frame_equal(mapping,conv.build_mapping(model),check_exact=True)
    for component, attr in (("Bus","buses"),("Line","lines"),("Transformer","transformers"),
                            ("Generator","generators"),("Load","loads"),("Shunt","shunt_impedances")):
        if set(mapping.loc[mapping.component.eq(component),"original_id"]) != set(getattr(source,attr).index.astype(str)):
            raise ValueError(f"Incomplete conversion mapping: {component}")


def assert_structure(source, model, net):
    assert_mapping(source, model)
    mapping = model.mapping
    specs = (("Bus","buses",net.get_bus_breaker_view_buses),
             ("Line","lines",net.get_lines),
             ("Transformer","transformers",net.get_2_windings_transformers),
             ("Generator","generators",net.get_generators),
             ("Load","loads",net.get_loads),
             ("Shunt","shunt_impedances",net.get_shunt_compensators))
    for component, attr, getter in specs:
        rows = mapping.loc[mapping.component.eq(component)]
        frame = getter()
        if set(rows.original_id) != set(getattr(source,attr).index.astype(str)) or set(frame.index) != set(rows.iidm_id):
            raise ValueError(f"Incomplete conversion mapping/inventory: {component}")
        for row in rows.itertuples():
            item = frame.loc[row.iidm_id]
            if item["name"] != row.original_id:
                raise ValueError(f"Original ID name changed: {row.iidm_id}")
            if component in ("Line","Transformer"):
                if (item.voltage_level1_id,item.voltage_level2_id) != ("VL_"+row.original_bus0,"VL_"+row.original_bus1):
                    raise ValueError(f"Conversion branch connectivity mismatch: {row.iidm_id}")
            elif item.voltage_level_id != "VL_"+row.original_bus0:
                raise ValueError(f"Conversion injection/bus connectivity mismatch: {row.iidm_id}")
    buses = mapping.loc[mapping.component.eq("Bus")]
    levels = net.get_voltage_levels()
    if set(levels.index) != set(buses.iidm_voltage_level_id):
        raise ValueError("Conversion voltage levels mismatch")
    for row in buses.itertuples():
        if levels.at[row.iidm_voltage_level_id,"nominal_v"] != source.buses.at[row.original_id,"v_nom"]:
            raise ValueError("Conversion nominal voltage mismatch")
    if not net.get_ratio_tap_changers().empty or not net.get_phase_tap_changers().empty:
        raise ValueError("Unexpected tap changer in supported unity-tap model")
    limits = net.get_loading_limits().reset_index()
    limits = limits.loc[limits.acceptable_duration.eq(-1)]
    branches = mapping.loc[mapping.component.isin(["Line","Transformer"])]
    if set(limits.element_id) != set(branches.iidm_id) or len(limits) != 2*len(branches):
        raise ValueError("Conversion thermal-limit coverage mismatch")
    for row in branches.itertuples():
        source_frame = source.lines if row.component == "Line" else source.transformers
        rows = limits.loc[limits.element_id.eq(row.iidm_id)]
        if not rows["type"].eq("APPARENT_POWER").all() or not rows.value.eq(source_frame.at[row.original_id,"s_nom"]).all():
            raise ValueError("Conversion thermal-limit value mismatch")


def runtime_observation(paths, xiidm, reports):
    selected = replace(paths,runtime_xiidm=xiidm,runtime_reports=reports)
    result = run_probe(paths.runtime_python,"numerical",selected)
    if not result["ok"]:
        raise ValueError(f"Isolated EMS compatibility verification failed: {result.get('error')}")
    return result["result"]


def runtime_comparison(candidate, reference, config):
    checks = Checks()
    compare_numerical(candidate,reference,checks)
    contract = load_contract()
    expected = contract["loadflow_parameters"]
    params = conv.loadflow_parameters("VL_"+config["expected_slack_bus"],**{
        "use_reactive_limits": config["openloadflow"]["use_reactive_limits"],
        "conv_eps_per_eq": config["openloadflow"]["conv_eps_per_eq"],
        "max_iterations": config["openloadflow"]["max_iterations"]})
    actual = {k: getattr(params,k).name if hasattr(getattr(params,k),"name") else getattr(params,k) for k in expected}
    checks.equal("converter_vs_runtime_parameters",actual,candidate["loadflow_parameters"])
    checks.equal("runtime_vs_stage1_parameters",candidate["loadflow_parameters"],expected)
    if not checks.ready():
        failed = [c["name"] for c in checks.items if c["passed"] is False]
        raise ValueError(f"Converted runtime baseline mismatch: {failed}")
    return {"passed": True,"checks": checks.items}


def assert_valid_result(directory, summary, config):
    validation = json.loads((directory/"conversion_validation.json").read_text())
    if not summary["validation_passed"] or not summary["comparison"]["within_all_tolerances"]:
        raise ValueError("Conversion PF validation did not pass")
    if not all(validation["mapping_complete"].values()) or not validation["input_scenario"]["passed"]:
        raise ValueError("Conversion mapping/input verification did not pass")
    if not summary["pypowsybl_lf"]["converged"] or not summary["pypsa_rerun"]["converged"]:
        raise ValueError("Conversion PF did not converge")
    reload = validation["xiidm_reload"]
    if not reload["lf_after_reload"]["converged"] or not reload["inventory_equal"] or not all(v is True for v in reload["parameters_equal"].values()):
        raise ValueError("Conversion XIIDM reload validation failed")
    xiidm = directory/"mn35k_synthetic_powsybl.xiidm"
    hashes = json.loads((directory/"input_hashes.json").read_text())
    if reload["sha256"] != sha256(xiidm) or hashes["xiidm_output"] != {xiidm.name: sha256(xiidm)}:
        raise ValueError("Serialized XIIDM byte-integrity record mismatch")
    if hashes["input_model"] != {config["input_model"]: config["input_model_sha256"]} or not hashes["protected_unchanged"]:
        raise ValueError("Conversion input/protection record mismatch")
    restoration = summary["n1_restoration"]
    if restoration.get("skipped") or restoration.get("sa_pre_contingency_status") != "CONVERGED" or not restoration.get("variants_cleaned_and_lines_reconnected"):
        raise ValueError("Conversion N-1/restoration did not pass")
    rows = pd.read_csv(directory/"n1_feasibility_results.csv")
    if len(rows) != config["n1"]["max_contingencies"] or not rows.security_analysis_status.eq("CONVERGED").all() or not rows.manual_variant_status.eq("CONVERGED").all() or not rows.isolation_matches_graph.all():
        raise ValueError("Conversion N-1 cases did not pass")


def xiidm_identity(path):
    """Keep byte integrity separate from XML identity excluding only caseDate."""
    root = ET.parse(path).getroot()
    date = root.attrib.pop("caseDate", None)
    canonical = ET.canonicalize(ET.tostring(root, encoding="unicode"))
    return {"byte_sha256": sha256(path), "caseDate": date,
            "canonical_xml_without_caseDate_sha256": hashlib.sha256(canonical.encode()).hexdigest()}
