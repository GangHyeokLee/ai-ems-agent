#!/usr/bin/env python3
"""Validate a passive MN_35k PoC model; never run PF or assign a slack."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .audit_mn35k_parameters import component_map

LINE_FIELDS = ["bus0", "bus1", "v_nom", "type", "length", "num_parallel", "s_nom", "r", "x", "b", "g", "r_pu", "x_pu", "b_pu", "g_pu"]
TRAFO_FIELDS = ["bus0", "bus1", "type", "model", "s_nom", "r", "x", "g", "b", "tap_ratio", "tap_side", "phase_shift", "r_pu", "x_pu", "g_pu", "b_pu"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_electrical_equal(left, right):
    """Check every static input/derived field used by the electrical model."""
    for component, fields in [("buses", ["v_nom", "x", "y", "control"]), ("lines", LINE_FIELDS), ("transformers", TRAFO_FIELDS)]:
        pd.testing.assert_frame_equal(getattr(left, component)[fields], getattr(right, component)[fields], check_dtype=False, rtol=1e-12, atol=1e-14)
    pd.testing.assert_frame_equal(left.line_types, right.line_types, check_dtype=False, rtol=1e-12, atol=1e-14)
    assert left.snapshots.equals(right.snapshots), "Snapshots changed"
    assert left.meta == right.meta, "Model provenance metadata changed"


def validate_model(model, original, config, *, matrices=None):
    """Return explicit pass/fail checks, screening warnings, and Ybus structure."""
    checks, warnings = [], []

    def check(name, condition, detail=""):
        checks.append({"check": name, "passed": bool(condition), "detail": detail})

    expected = config["expected_structure"]
    check("pypsa_version", pypsa.__version__ == config["required_pypsa_version"])
    metadata = model.meta.get("poc_refinement", {})
    check("source_fingerprint", metadata.get("source_model_sha256") == config["protected_models"][config["input_model"]])
    check("config_fingerprint", metadata.get("config_sha256") == hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
    for component in ["buses", "lines", "transformers", "generators", "loads"]:
        frame, old = getattr(model, component), getattr(original, component)
        check(component + "_count", len(frame) == expected[component], str(len(frame)))
        check(component + "_ids_preserved", frame.index.equals(old.index))
    check("snapshots_preserved", model.snapshots.equals(original.snapshots))
    check("bus_coordinates_and_voltage_preserved", model.buses[["x", "y", "v_nom", "control"]].equals(original.buses[["x", "y", "v_nom", "control"]]))
    endpoints_ok = True
    for name in ["lines", "transformers"]:
        branch = getattr(model, name)
        valid = branch.bus0.isin(model.buses.index).all() and branch.bus1.isin(model.buses.index).all()
        endpoints_ok &= valid
        check(name + "_endpoints_exist", valid)
        check(name + "_endpoints_unchanged", branch[["bus0", "bus1"]].equals(getattr(original, name)[["bus0", "bus1"]]))
        check(name + "_active_preserved", branch.active.equals(getattr(original, name).active))
        for field in ["r", "x", "s_nom"]:
            check(name + "_positive_finite_" + field, np.isfinite(branch[field]).all() and (branch[field] > 0).all())
    graph, _, components = component_map(model)
    isolated = sorted(str(i) for i, degree in graph.degree if degree == 0)
    check("connected_components_preserved", [len(c) for c in components] == expected["component_sizes"], str([len(c) for c in components]))
    check("isolated_buses_preserved", isolated == expected["isolated_buses"], str(isolated))
    voltage_counts = {str(int(v)): int(k) for v, k in model.lines.groupby("v_nom").size().items()}
    check("line_voltage_counts", voltage_counts == expected["line_voltage_counts"], str(voltage_counts))
    check("parallel_circuits_preserved", model.lines.num_parallel.equals(original.lines.num_parallel))
    for field in ["length", "num_parallel", "v_nom"]:
        check("line_positive_finite_" + field, np.isfinite(model.lines[field]).all() and (model.lines[field] > 0).all())
    for field in ["b", "g"]:
        check("line_nonnegative_finite_" + field, np.isfinite(model.lines[field]).all() and (model.lines[field] >= 0).all())
    for field in ["g", "b", "phase_shift", "tap_ratio"]:
        check("transformer_finite_" + field, np.isfinite(model.transformers[field]).all())
    check("transformer_positive_tap", (model.transformers.tap_ratio > 0).all())
    check("transformer_tap_side_valid", model.transformers.tap_side.isin([0, 1]).all())
    check("transformer_neutral_tap_assumption", model.transformers.tap_ratio.eq(config["transformer_common"]["tap_ratio"]).all())
    check("transformer_manual_type", model.transformers.type.eq("").all())
    check("transformer_phase_reference_explicit", model.transformers.phase_shift.eq(config["transformer_common"]["phase_shift_deg"]).all())
    check("transformer_shunt_omission_explicit", model.transformers.g.eq(0).all() and model.transformers.b.eq(0).all())
    if endpoints_ok:
        check("line_voltage_matches_both_buses", np.allclose(model.lines.v_nom, model.lines.bus0.map(model.buses.v_nom)) and np.allclose(model.lines.v_nom, model.lines.bus1.map(model.buses.v_nom)))
    type_voltage_ok = True
    for _, row in model.lines.iterrows():
        type_voltage_ok &= config["type_voltage_kv"].get(row.type, 35 if str(row.type).startswith("POC_35kV_") else None) == row.v_nom
    check("line_type_voltage_match", type_voltage_ok)
    check("35kv_no_fallback", model.lines.loc[model.lines.v_nom.eq(35), "type"].str.startswith("POC_35kV_").all())
    types_ok = model.lines.type.isin(model.line_types.index).all()
    check("line_types_registered", types_ok)
    scenario = model.meta.get("poc_refinement", {}).get("scenario")
    check("scenario_known", scenario in config["line_35kv"])
    if scenario in config["line_35kv"]:
        from .mn35k_refinement import line_type_parameters
        expected_type = "POC_35kV_SYNTHETIC_ACSR_" + scenario
        check("35kv_scenario_type", model.lines.loc[model.lines.v_nom.eq(35), "type"].eq(expected_type).all())
        if expected_type in model.line_types.index:
            expected_params = line_type_parameters(config["line_35kv"][scenario], config["line_common"])
            check("35kv_type_matches_config", all(np.isclose(model.line_types.at[expected_type, key], expected_params[key]) for key in ["r_per_length", "x_per_length", "c_per_length", "i_nom", "f_nom"]))
    for standard_type in config["type_voltage_kv"]:
        check("retained_standard_type_" + standard_type, standard_type in model.line_types.index and model.line_types.loc[standard_type].equals(original.line_types.loc[standard_type]))
    if types_ok:
        types = model.line_types.reindex(model.lines.type).set_axis(model.lines.index)
        length, parallel = model.lines.length, model.lines.num_parallel
        for field, expected_values in {
            "r": types.r_per_length * length / parallel,
            "x": types.x_per_length * length / parallel,
            "b": 2 * np.pi * types.f_nom * types.c_per_length * 1e-9 * length * parallel,
            "s_nom": np.sqrt(3) * model.lines.v_nom * types.i_nom * parallel,
            "r_pu": model.lines.r / model.lines.v_nom**2,
            "x_pu": model.lines.x / model.lines.v_nom**2,
            "b_pu": model.lines.b * model.lines.v_nom**2,
        }.items():
            check("line_units_formula_" + field, np.allclose(model.lines[field], expected_values, rtol=1e-10, atol=1e-14))
    trafo = model.transformers
    for field in ["r", "x"]:
        check("transformer_1mva_base_" + field, np.allclose(trafo[field + "_pu"], trafo[field] / trafo.s_nom, rtol=1e-10, atol=1e-14))
    for name, row in trafo.iterrows():
        if row.bus0 not in model.buses.index or row.bus1 not in model.buses.index:
            check("transformer_test_values_" + name, False, "Invalid endpoint; parameter lookup skipped")
            continue
        pair = "_".join(str(int(v)) for v in sorted(model.buses.loc[[row.bus0, row.bus1], "v_nom"]))
        parameters = config["transformers"].get(pair, {}).get(scenario)
        if parameters is None:
            check("transformer_test_values_" + name, False, "Unconfigured voltage pair or scenario")
            continue
        check("transformer_test_values_" + name,
              np.isclose(row.s_nom, parameters["s_nom_mva"]) and
              np.isclose(np.hypot(row.r, row.x), parameters["uk_percent"] / 100) and
              np.isclose(row.r * row.s_nom * 1000, parameters["load_loss_kw"]))
    for field, limits in config["screening_limits"].items():
        component, column = field.split(".")
        values = getattr(model, component)[column]
        bad = values[(values < limits[0]) | (values > limits[1])]
        for name, value in bad.items():
            warnings.append({"equipment": str(name), "parameter": field, "value": float(value), "reason": "Engineering screening threshold; requires review, not automatic invalidity"})
    ybus = []
    if all(item["passed"] for item in checks):
        temporary = model.copy()
        # determine_network_topology() in PyPSA1.2.4 calls find_bus_controls()
        # and invents slack labels even without generators. Assemble only the
        # component membership needed by calculate_Y, avoiding that routine.
        for index, buses in enumerate(components):
            name = "poc_ybus_" + str(index)
            temporary.add("SubNetwork", name, carrier="AC")
            temporary.buses.loc[buses, "sub_network"] = name
        for branch in [temporary.lines, temporary.transformers]:
            branch["sub_network"] = branch.bus0.map(temporary.buses.sub_network)
        for index, buses in enumerate(components):
            name = "poc_ybus_" + str(index)
            sub = pypsa.SubNetwork(temporary, name)
            # Matrix assembly needs an ordering, not generators, bus controls or a slack.
            sub.buses_o = sub.c.buses.static.index
            sub.calculate_Y(skip_pre=True)
            matrix = sub.Y.tocsr()
            if matrices is not None:
                matrices[name] = (list(map(str, sub.buses_o)), matrix.copy())
            delta = matrix - matrix.T
            symmetric_error = float(np.max(np.abs(delta.data))) if delta.nnz else 0.0
            finite = bool(np.isfinite(matrix.data).all())
            ybus.append({"component": str(name), "buses": list(map(str, sub.buses_o)), "shape": list(matrix.shape), "nnz": int(matrix.nnz), "finite": finite, "symmetry_error": symmetric_error, "isolated_zero_matrix": matrix.shape == (1, 1) and matrix.nnz == 0})
            check("Ybus_finite_" + str(name), finite)
            check("Ybus_symmetric_" + str(name), symmetric_error < 1e-9)
        check("no_slack_or_controls_assigned", temporary.buses.control.equals(model.buses.control))
    return {"valid": all(item["passed"] for item in checks), "checks": checks, "screening_warnings": warnings, "components": components, "isolated_buses": isolated, "ybus": ybus, "ac_power_flow_executed": False, "convergence_verified": False}
