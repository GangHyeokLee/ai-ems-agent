"""Diagnose MN_35k PoC line charging, voltage violations and Q-limit behavior."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import pypsa

from .run_mn35k_acpf import branch_results, execute_pf, select_component
from .run_mn35k_data_informed_pf import _candidate_network, _candidate_rows, read_settings
from .run_mn35k_reactive_support import (_result_tables, build_reactive_scenario,
    execute_with_q_limits, validate_reactive_results)
from .inventory import sha256
from .diagnostic_inputs import (analysis_bundle, source_manifest, read_config, base_settings)
from .passive_inputs import logical_input


def charging_values(
    length_km: float,
    c_nf_per_km: float,
    frequency_hz: float,
    num_parallel: float,
    voltage_kv: float,
    voltage_pu: float = 1.0,
) -> tuple[float, float]:
    """Return total line susceptance (S) and positive charging magnitude (MVAr)."""
    values = (length_km, c_nf_per_km, frequency_hz, num_parallel, voltage_kv, voltage_pu)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Line charging inputs must be finite")
    if min(length_km, c_nf_per_km, frequency_hz, num_parallel, voltage_kv) <= 0:
        raise ValueError("Line charging inputs must be positive")
    if voltage_pu < 0:
        raise ValueError("Voltage magnitude cannot be negative")
    b_siemens = (
        2
        * math.pi
        * frequency_hz
        * c_nf_per_km
        * 1e-9
        * length_km
        * num_parallel
    )
    return b_siemens, b_siemens * voltage_kv**2 * voltage_pu**2



def voltage_class_summary(lines: pd.DataFrame) -> pd.DataFrame:
    """Aggregate no-load charging magnitudes and retain unresolved-length impact."""
    grouped = (
        lines.groupby("voltage_kv", as_index=False)
        .agg(
            line_count=("line_id", "size"),
            length_km=("length_km", "sum"),
            b_siemens=("b_siemens", "sum"),
            charging_q_1pu_mvar=("charging_q_1pu_mvar", "sum"),
            unresolved_line_count=("length_status", lambda x: int(x.eq("UNRESOLVED").sum())),
            unresolved_charging_q_1pu_mvar=(
                "charging_q_1pu_mvar",
                lambda x: float(x[lines.loc[x.index, "length_status"].eq("UNRESOLVED")].sum()),
            ),
        )
        .sort_values("voltage_kv")
    )
    total = grouped.charging_q_1pu_mvar.sum()
    grouped["charging_share_percent"] = (
        100 * grouped.charging_q_1pu_mvar / total if total else 0.0
    )
    grouped["unresolved_charging_share_percent"] = (
        100 * grouped.unresolved_charging_q_1pu_mvar / total if total else 0.0
    )
    return grouped



def voltage_classification(voltage_pu: float, lower: float, upper: float) -> str:
    if not math.isfinite(voltage_pu) or voltage_pu <= 0:
        return "NUMERICAL_DIAGNOSTIC_ONLY"
    if voltage_pu < lower:
        return "UNDER_VOLTAGE"
    if voltage_pu > upper:
        return "OVER_VOLTAGE"
    return "WITHIN_LIMITS"



def _line_type_parameters(network: pypsa.Network, line: pd.Series) -> tuple[float, float, float]:
    line_type = str(line.type)
    if not line_type or line_type not in network.line_types.index:
        raise ValueError(f"Line {line.name} has no resolvable LineType")
    type_data = network.line_types.loc[line_type]
    parallel = float(line.num_parallel)
    length = float(line.length)
    r_expected = float(type_data.r_per_length) * length / parallel
    x_expected = float(type_data.x_per_length) * length / parallel
    b_expected, _ = charging_values(
        length,
        float(type_data.c_per_length),
        float(type_data.f_nom),
        parallel,
        float(line.v_nom),
    )
    return r_expected, x_expected, b_expected



def _line_type_nominal_voltage(line_type: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)$", line_type)
    return float(match.group(1)) if match else np.nan



def line_charging_analysis(
    network: pypsa.Network,
    main_buses: list[str],
    flow_tables: dict[str, pd.DataFrame],
    normal_range: tuple[float, float],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    network.calculate_dependent_values()
    lines = network.lines.loc[
        network.lines.bus0.isin(main_buses) & network.lines.bus1.isin(main_buses)
    ].copy()
    if len(main_buses) != 166 or len(lines) != 156:
        raise ValueError("Unexpected main-component topology")
    rows = []
    for line_id, line in lines.iterrows():
        r_expected, x_expected, b_expected = _line_type_parameters(network, line)
        b, q_1pu = charging_values(
            float(line.length),
            float(network.line_types.at[line.type, "c_per_length"]),
            float(network.line_types.at[line.type, "f_nom"]),
            float(line.num_parallel),
            float(line.v_nom),
        )
        type_voltage = _line_type_nominal_voltage(str(line.type))
        status = str(line.get("poc_length_class", "UNRESOLVED"))
        length_status = (
            "UNRESOLVED"
            if status.upper() in {"UNRESOLVED", "UNVERIFIED", "NAN", ""}
            or status.lower() == "nan"
            else status
        )
        row = {
            "line_id": str(line_id),
            "bus0": str(line.bus0),
            "bus1": str(line.bus1),
            "voltage_kv": float(line.v_nom),
            "length_km": float(line.length),
            "length_status": length_status,
            "num_parallel": float(line.num_parallel),
            "line_type": str(line.type),
            "type_voltage_kv_from_type_name": type_voltage,
            "line_type_voltage_match_from_name": (
                math.isclose(float(line.v_nom), type_voltage, rel_tol=0.0, abs_tol=1e-9)
                if math.isfinite(type_voltage)
                else None
            ),
            "r_ohm": float(line.r),
            "x_ohm": float(line.x),
            "b_siemens": float(line.b),
            "r_ohm_per_km": float(network.line_types.at[line.type, "r_per_length"]),
            "x_ohm_per_km": float(network.line_types.at[line.type, "x_per_length"]),
            "c_nf_per_km": float(network.line_types.at[line.type, "c_per_length"]),
            "frequency_hz": float(network.line_types.at[line.type, "f_nom"]),
            "r_ohm_independent": r_expected,
            "x_ohm_independent": x_expected,
            "b_siemens_independent": b_expected,
            "r_parameter_error_ohm": float(line.r) - r_expected,
            "x_parameter_error_ohm": float(line.x) - x_expected,
            "b_parameter_error_siemens": float(line.b) - b_expected,
            "r_pu_pypsa": float(line.r_pu),
            "x_pu_pypsa": float(line.x_pu),
            "b_pu_pypsa": float(line.b_pu),
            "s_nom_mva": float(line.s_nom),
            "charging_q_1pu_mvar": q_1pu,
            "charging_q_normal_min_mvar": q_1pu * normal_range[0] ** 2,
            "charging_q_normal_max_mvar": q_1pu * normal_range[1] ** 2,
            "q0_mvar_reference": np.nan,
            "q1_mvar_reference": np.nan,
            "q_terminal_net_reference_mvar": np.nan,
            "q0_mvar_candidate": np.nan,
            "q1_mvar_candidate": np.nan,
            "q_terminal_net_candidate_mvar": np.nan,
            "p_loss_mw_candidate": np.nan,
            "bus0_x": float(network.buses.at[line.bus0, "x"]),
            "bus0_y": float(network.buses.at[line.bus0, "y"]),
            "bus1_x": float(network.buses.at[line.bus1, "x"]),
            "bus1_y": float(network.buses.at[line.bus1, "y"]),
        }
        for scenario_id, flows in flow_tables.items():
            if str(line_id) not in flows.index:
                continue
            pfx = "reference" if "reference" in scenario_id else "candidate"
            row[f"q0_mvar_{pfx}"] = float(flows.at[str(line_id), "q0_mvar"])
            row[f"q1_mvar_{pfx}"] = float(flows.at[str(line_id), "q1_mvar"])
            row[f"q_terminal_net_{pfx}_mvar"] = float(
                flows.at[str(line_id), "q_net_mvar"]
            )
            if pfx == "candidate":
                row["p_loss_mw_candidate"] = float(flows.at[str(line_id), "p_loss_mw"])
        if not np.allclose(
            [line.r, line.x, line.b], [r_expected, x_expected, b_expected],
            rtol=1e-10, atol=1e-12,
        ):
            raise AssertionError(f"PyPSA line type values do not reproduce for {line_id}")
        rows.append(row)
    details = pd.DataFrame(rows)
    return details, voltage_class_summary(details)



def _electric_distance_graph(network: pypsa.Network) -> nx.Graph:
    network.calculate_dependent_values()
    graph = nx.Graph()
    graph.add_nodes_from(network.buses.index.astype(str))
    for component in ("lines", "transformers"):
        frame = getattr(network, component)
        for name, branch in frame.iterrows():
            x_pu = float(branch.x_pu)
            if not math.isfinite(x_pu) or x_pu == 0.0:
                raise ValueError(f"Invalid series reactance for {component} {name}")
            weight = abs(x_pu)
            if graph.has_edge(str(branch.bus0), str(branch.bus1)):
                weight = min(weight, graph[str(branch.bus0)][str(branch.bus1)]["weight"])
            graph.add_edge(str(branch.bus0), str(branch.bus1), weight=weight)
    return graph



def _nearest_control_distances(network: pypsa.Network) -> pd.DataFrame:
    graph = _electric_distance_graph(network)
    controls = (
        network.generators.reset_index()
        .groupby("bus")
        .control.apply(lambda values: "+".join(sorted(set(values))))
        .to_dict()
    )
    sources = {str(bus): controls[bus] for bus in controls}
    distance_sets = {
        bus: nx.single_source_dijkstra_path_length(graph, bus, weight="weight")
        for bus in sources
    }
    rows = []
    for bus in network.buses.index.astype(str):
        candidates = [
            (distances[bus], control_bus)
            for control_bus, distances in distance_sets.items()
            if bus in distances
        ]
        if not candidates:
            rows.append(
                {
                    "bus": bus,
                    "nearest_control_bus": None,
                    "nearest_control_type": None,
                    "electrical_distance_x_pu": np.nan,
                }
            )
            continue
        distance, nearest = min(candidates)
        rows.append(
            {
                "bus": bus,
                "nearest_control_bus": nearest,
                "nearest_control_type": sources[nearest],
                "electrical_distance_x_pu": float(distance),
            }
        )
    return pd.DataFrame(rows).set_index("bus")



def voltage_analysis(
    network: pypsa.Network,
    scenario_id: str,
    lower: float,
    upper: float,
    top_n: int,
) -> pd.DataFrame:
    snapshot = network.snapshots[0]
    voltage = network.buses_t.v_mag_pu.loc[snapshot]
    distance = _nearest_control_distances(network)
    bus_table = network.buses[["v_nom", "control"]].copy()
    bus_table.index = bus_table.index.astype(str)
    values = pd.DataFrame(
        {
            "bus": bus_table.index,
            "voltage_kv": bus_table.v_nom.to_numpy(dtype=float),
            "bus_control": bus_table.control.astype(str).to_numpy(),
            "v_mag_pu": voltage.reindex(bus_table.index).to_numpy(dtype=float),
        }
    ).set_index("bus")
    values = values.join(distance)
    values["scenario_id"] = scenario_id
    values["voltage_classification"] = values.v_mag_pu.map(
        lambda value: voltage_classification(float(value), lower, upper)
    )
    values["result_classification"] = (
        "NUMERICAL_DIAGNOSTIC_ONLY"
        if scenario_id.startswith("plant_candidates")
        else "REFERENCE_SCREENING_RESULT"
    )
    values["voltage_violation"] = values.voltage_classification.isin(
        ["UNDER_VOLTAGE", "OVER_VOLTAGE"]
    )
    for rank_column, ascending in [("minimum_voltage_rank", True), ("maximum_voltage_rank", False)]:
        ranks = values.v_mag_pu.rank(method="first", ascending=ascending)
        values[rank_column] = ranks.where(ranks <= top_n)
    return values.reset_index()



def _q_violations(
    network: pypsa.Network, q_limits: dict[str, tuple[float, float]]
) -> list[dict]:
    snapshot = network.snapshots[0]
    rows = []
    for name in network.generators.index:
        q = float(network.generators_t.q.at[snapshot, name])
        lower, upper = q_limits[str(name)]
        if q < lower - 1e-9:
            bound = lower
        elif q > upper + 1e-9:
            bound = upper
        else:
            continue
        rows.append(
            {
                "generator": str(name),
                "bus": str(network.generators.at[name, "bus"]),
                "control": str(network.generators.at[name, "control"]),
                "q_mvar": q,
                "q_limit_mvar": bound,
                "violation_mvar": q - bound,
            }
        )
    return rows



def _network_q_limits(
    network: pypsa.Network, base_config: dict, candidate_limits: dict | None = None
) -> dict[str, tuple[float, float]]:
    default_limits = (
        float(base_config["generator"]["q_min_mvar"]),
        float(base_config["generator"]["q_max_mvar"]),
    )
    return {
        str(name): (candidate_limits or {}).get(str(name), default_limits)
        for name in network.generators.index
    }



def _flow_tables(network: pypsa.Network) -> tuple[pd.DataFrame, pd.DataFrame]:
    line = branch_results(network, "lines")
    line.index = line.index.astype(str)
    transformer = branch_results(network, "transformers")
    transformer.index = transformer.index.astype(str)
    return line, transformer



def _scenario_summary(
    scenario_id: str,
    description: str,
    network: pypsa.Network,
    outcome: dict,
    q_limits: dict[str, tuple[float, float]],
    validation: dict | None,
    modified_voltage_class: float | None = None,
    reactor_mvar: float | None = None,
    *, base_config: dict,
) -> dict:
    converged = outcome["status"] == "AC_PF_CONVERGED"
    sub = outcome["subnetworks"][0] if outcome.get("subnetworks") else {}
    values_finite = bool(
        np.isfinite(network.buses_t.v_mag_pu.loc[network.snapshots[0]].to_numpy()).all()
    )
    if converged and values_finite:
        q_violations = _q_violations(network, q_limits)
        validation_tables, validation_result = validate_reactive_results(
            network, base_config
        )
        v = network.buses_t.v_mag_pu.loc[network.snapshots[0]]
        line, transformer = _flow_tables(network)
        gen_p = float(network.generators_t.p.loc[network.snapshots[0]].sum())
        gen_q = float(network.generators_t.q.loc[network.snapshots[0]].sum())
        loads_p = float(network.loads_t.p.loc[network.snapshots[0]].sum())
        loads_q = float(network.loads_t.q.loc[network.snapshots[0]].sum())
        slack_names = network.generators.index[network.generators.control.eq("Slack")]
        return {
            "scenario_id": scenario_id,
            "description": description,
            "result_source": "REPRODUCED_DIAGNOSTIC_RUN",
            "pf_status": outcome["status"],
            "n_iter": sub.get("n_iter"),
            "error": sub.get("error"),
            "bus_count": len(network.buses),
            "line_count": len(network.lines),
            "transformer_count": len(network.transformers),
            "generator_count": len(network.generators),
            "pv_bus_count": int(network.buses.control.eq("PV").sum()),
            "pq_bus_count": int(network.buses.control.eq("PQ").sum()),
            "slack_bus_count": int(network.buses.control.eq("Slack").sum()),
            "q_limit_violation_count": len(q_violations),
            "voltage_violation_count": int(
                ((v < 0.9) | (v > 1.1)).sum()
            ),
            "v_min_pu": float(v.min()),
            "v_max_pu": float(v.max()),
            "generation_p_mw": gen_p,
            "generation_q_mvar": gen_q,
            "load_p_mw": loads_p,
            "load_q_mvar": loads_q,
            "line_p_loss_mw": float(line.p_loss_mw.sum()),
            "transformer_p_loss_mw": float(transformer.p_loss_mw.sum()),
            "line_max_loading_percent": float(line.loading_percent.max()),
            "transformer_max_loading_percent": float(transformer.loading_percent.max()),
            "line_terminal_q_net_mvar": float(line.q_net_mvar.sum()),
            "transformer_terminal_q_net_mvar": float(transformer.q_net_mvar.sum()),
            "shunt_q_mvar": float(
                validation_tables["shunt_reactor_results"].q_mvar.sum()
            ),
            "slack_p_mw": float(
                network.generators_t.p.loc[network.snapshots[0], slack_names].sum()
            ),
            "slack_q_mvar": float(
                network.generators_t.q.loc[network.snapshots[0], slack_names].sum()
            ),
            "p_balance_residual_mw": float(
                validation_tables["power_balance_validation"].at["P", "residual"]
            ),
            "q_balance_residual_mvar": float(
                validation_tables["power_balance_validation"].at["Q", "residual"]
            ),
            "nodal_max_residual_mva": validation_result["max_nodal_residual_mva"],
            "modified_line_voltage_class_kv": modified_voltage_class,
            "reactor_nominal_mvar": reactor_mvar,
            "finite_results": True,
            "assessment": (
                "NUMERICAL_DIAGNOSTIC_ONLY"
                if scenario_id.startswith("plant_candidates")
                else "SYNTHETIC_REFERENCE_OR_SENSITIVITY"
            ),
        }
    return {
        "scenario_id": scenario_id,
        "description": description,
        "result_source": "REPRODUCED_DIAGNOSTIC_RUN",
        "pf_status": outcome["status"],
        "n_iter": sub.get("n_iter"),
        "error": sub.get("error"),
        "bus_count": len(network.buses),
        "line_count": len(network.lines),
        "transformer_count": len(network.transformers),
        "generator_count": len(network.generators),
        "pv_bus_count": int(network.buses.control.eq("PV").sum()),
        "pq_bus_count": int(network.buses.control.eq("PQ").sum()),
        "slack_bus_count": int(network.buses.control.eq("Slack").sum()),
        "q_limit_violation_count": None,
        "voltage_violation_count": None,
        "v_min_pu": None,
        "v_max_pu": None,
        "last_iterate_voltage_min_pu": (
            float(network.buses_t.v_mag_pu.loc[network.snapshots[0]].min())
            if values_finite
            else None
        ),
        "last_iterate_voltage_max_pu": (
            float(network.buses_t.v_mag_pu.loc[network.snapshots[0]].max())
            if values_finite
            else None
        ),
        "generation_p_mw": None,
        "generation_q_mvar": None,
        "load_p_mw": float(network.loads.p_set.sum()),
        "load_q_mvar": float(network.loads.q_set.sum()),
        "line_p_loss_mw": None,
        "transformer_p_loss_mw": None,
        "line_max_loading_percent": None,
        "transformer_max_loading_percent": None,
        "line_terminal_q_net_mvar": None,
        "transformer_terminal_q_net_mvar": None,
        "shunt_q_mvar": None,
        "slack_p_mw": None,
        "slack_q_mvar": None,
        "p_balance_residual_mw": None,
        "q_balance_residual_mvar": None,
        "nodal_max_residual_mva": None,
        "modified_line_voltage_class_kv": modified_voltage_class,
        "reactor_nominal_mvar": reactor_mvar,
        "finite_results": False,
        "assessment": "NONCONVERGED_OR_NONFINITE",
    }



def _change_reactor_rating(
    network: pypsa.Network, nominal_mvar: float
) -> None:
    if not math.isfinite(nominal_mvar) or nominal_mvar < 0:
        raise ValueError("Reactor sensitivity ratings must be finite and nonnegative")
    for shunt in network.shunt_impedances.index:
        bus = str(network.shunt_impedances.at[shunt, "bus"])
        voltage_kv = float(network.buses.at[bus, "v_nom"])
        network.shunt_impedances.at[shunt, "b"] = -nominal_mvar / voltage_kv**2
    network.meta["synthetic_reactive_support"]["shunt_reactors"] = [
        {**item, "nominal_q_absorption_mvar": nominal_mvar}
        for item in network.meta["synthetic_reactive_support"]["shunt_reactors"]
    ]



def _line_charging_sensitivity(
    network: pypsa.Network, voltage_kv: float, scale: float
) -> None:
    mask = network.lines.v_nom.eq(voltage_kv)
    if not mask.any():
        raise ValueError(f"No lines at {voltage_kv:g} kV")
    original_b = network.lines.loc[mask, "b"].copy()
    network.lines.loc[mask, "type"] = ""
    network.lines.loc[mask, "b"] = original_b * scale
    expected = original_b * scale
    network.calculate_dependent_values()
    pd.testing.assert_series_equal(
        network.lines.loc[mask, "b"],
        expected,
        check_names=False,
        check_dtype=False,
        rtol=1e-12,
        atol=1e-15,
    )



def _jacobian_condition(
    network: pypsa.Network, slack_bus: str | None = None
) -> dict:
    """Finite-difference Newton mismatch Jacobian at the current voltage state."""
    network.calculate_dependent_values()
    network.determine_network_topology()
    sub = network.sub_networks.obj.iloc[0]
    sub.calculate_Y(skip_pre=True)
    buses = sub.buses_o.astype(str).tolist()
    index = {bus: idx for idx, bus in enumerate(buses)}
    if slack_bus is None:
        slack_bus = str(sub.slack_bus)
    non_slack = [bus for bus in buses if bus != slack_bus]
    pq = network.buses.index[network.buses.control.eq("PQ")].astype(str).tolist()
    theta0 = network.buses_t.v_ang.loc[network.snapshots[0]].reindex(buses).to_numpy()
    vm0 = network.buses_t.v_mag_pu.loc[network.snapshots[0]].reindex(buses).to_numpy()
    x0 = np.r_[theta0[[index[bus] for bus in non_slack]], vm0[[index[bus] for bus in pq]]]
    y = sub.Y.toarray()
    p_generation = network.generators.groupby("bus").p_set.sum().reindex(
        buses, fill_value=0.0
    )
    p_load = network.loads.groupby("bus").p_set.sum().reindex(buses, fill_value=0.0)
    q_generation = (
        network.generators.loc[network.generators.control.eq("PQ")]
        .groupby("bus")
        .q_set.sum()
        .reindex(buses, fill_value=0.0)
    )
    q_load = network.loads.groupby("bus").q_set.sum().reindex(buses, fill_value=0.0)
    p_spec = (p_generation - p_load).to_numpy()
    q_spec = (q_generation - q_load).to_numpy()
    if not np.isfinite(p_spec).all() or not np.isfinite(q_spec).all():
        raise ValueError("Specified nodal powers contain non-finite values")

    def mismatch(state: np.ndarray) -> np.ndarray:
        theta = theta0.copy()
        vm = vm0.copy()
        theta[[index[bus] for bus in non_slack]] = state[: len(non_slack)]
        vm[[index[bus] for bus in pq]] = state[len(non_slack) :]
        v = vm * np.exp(1j * theta)
        calculated = v * np.conj(y @ v)
        return np.r_[
            p_spec[[index[bus] for bus in non_slack]]
            - calculated.real[[index[bus] for bus in non_slack]],
            q_spec[[index[bus] for bus in pq]]
            - calculated.imag[[index[bus] for bus in pq]],
        ]

    base = mismatch(x0)
    jacobian = np.empty((len(base), len(x0)), dtype=float)
    step = 1e-6
    for column in range(len(x0)):
        plus, minus = x0.copy(), x0.copy()
        plus[column] += step
        minus[column] -= step
        jacobian[:, column] = (mismatch(plus) - mismatch(minus)) / (2 * step)
    singular_values = np.linalg.svd(jacobian, compute_uv=False)
    rank = int(np.linalg.matrix_rank(jacobian))
    return {
        "bus_count": len(buses),
        "pv_bus_count": int(network.buses.control.eq("PV").sum()),
        "pq_bus_count": int(network.buses.control.eq("PQ").sum()),
        "slack_bus_count": int(network.buses.control.eq("Slack").sum()),
        "unknown_count": int(len(x0)),
        "mismatch_equation_count": int(len(base)),
        "jacobian_rank": rank,
        "jacobian_condition_number_2": float(np.linalg.cond(jacobian)),
        "jacobian_min_singular_value": float(singular_values.min()),
        "jacobian_max_singular_value": float(singular_values.max()),
        "mismatch_norm_mva": float(np.linalg.norm(base)),
        "jacobian_is_finite": bool(np.isfinite(jacobian).all()),
        "evaluation_point": "Current voltage state; post-limit case uses the pre-switch converged voltage as its initial state.",
    }



def reactive_balance_by_voltage(
    network: pypsa.Network,
    scenario_id: str,
    lines: pd.DataFrame,
    transformers: pd.DataFrame,
) -> pd.DataFrame:
    records = []
    for voltage in sorted(network.buses.v_nom.unique()):
        bus_ids = set(network.buses.index[network.buses.v_nom.eq(voltage)].astype(str))
        line_ids = set(
            network.lines.index[
                network.lines.v_nom.eq(voltage)
                & network.lines.bus0.astype(str).isin(bus_ids)
                & network.lines.bus1.astype(str).isin(bus_ids)
            ].astype(str)
        )
        generators = network.generators.loc[
            network.generators.bus.astype(str).isin(bus_ids)
        ]
        loads = network.loads.loc[network.loads.bus.astype(str).isin(bus_ids)]
        shunts = network.shunt_impedances.loc[
            network.shunt_impedances.bus.astype(str).isin(bus_ids)
        ]
        records.append(
            {
                "scenario_id": scenario_id,
                "voltage_class": f"{voltage:g} kV",
                "line_charging_q_1pu_mvar": np.nan,
                "line_terminal_q_net_mvar": float(
                    lines.loc[lines.index.intersection(line_ids), "q_net_mvar"].sum()
                ),
                "generator_q_mvar": float(
                    network.generators_t.q.loc[network.snapshots[0], generators.index].sum()
                ),
                "load_q_mvar": float(loads.q_set.sum()),
                "shunt_q_mvar": float(
                    network.shunt_impedances_t.q.loc[
                        network.snapshots[0], shunts.index
                    ].sum()
                ),
                "transformer_terminal_q_net_mvar": np.nan,
                "transformer_voltage_pair": "",
                "accounting_note": "Line charging-only and actual terminal Q are separate; do not sum these columns as a single balance.",
            }
        )
    by_voltage = network.lines.groupby("v_nom").b_pu.sum()
    for record in records:
        voltage = float(record["voltage_class"].split()[0])
        record["line_charging_q_1pu_mvar"] = float(by_voltage.get(voltage, 0.0))
    for name, transformer in network.transformers.iterrows():
        pair = tuple(
            sorted(
                float(network.buses.at[bus, "v_nom"])
                for bus in [transformer.bus0, transformer.bus1]
            )
        )
        records.append(
            {
                "scenario_id": scenario_id,
                "voltage_class": f"{pair[0]:g}/{pair[1]:g} kV transformer",
                "line_charging_q_1pu_mvar": np.nan,
                "line_terminal_q_net_mvar": np.nan,
                "generator_q_mvar": np.nan,
                "load_q_mvar": np.nan,
                "shunt_q_mvar": np.nan,
                "transformer_terminal_q_net_mvar": float(
                    transformers.at[str(name), "q_net_mvar"]
                ),
                "transformer_voltage_pair": f"{pair[0]:g}/{pair[1]:g}",
                "accounting_note": "Transformer terminal Q is reported once by voltage pair; not included in line charging.",
            }
        )
    return pd.DataFrame(records)



def _save_plots(charging: pd.DataFrame, voltage: pd.DataFrame, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(charging.voltage_kv.astype(str), charging.charging_q_1pu_mvar, color="#4472C4")
    ax.set(xlabel="Nominal line voltage (kV)", ylabel="1 pu no-load charging (MVAr)")
    ax.set_title("Line charging by voltage class (diagnostic model)")
    fig.tight_layout()
    fig.savefig(output / "line_charging_by_voltage.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    data = [
        voltage.loc[voltage.scenario_id.eq(sid), "v_mag_pu"].dropna().to_numpy()
        for sid in ["reference_45_generators_20mvar", "plant_candidates_initial_20mvar"]
    ]
    ax.boxplot(data)
    ax.set_xticks([1, 2], ["45 synthetic controls", "2 plant candidates + slack"])
    ax.axhline(0.9, color="red", linestyle="--", linewidth=1)
    ax.axhline(1.1, color="red", linestyle="--", linewidth=1)
    ax.set(ylabel="Bus voltage (pu)", title="Bus voltage distribution; candidate is diagnostic only")
    fig.tight_layout()
    fig.savefig(output / "bus_voltage_distribution.png", dpi=150)
    plt.close(fig)



def _report(
    output: Path,
    line_details: pd.DataFrame,
    charging: pd.DataFrame,
    voltage_summary: pd.DataFrame,
    comparison: pd.DataFrame,
    gen: pd.DataFrame,
    attempts: dict,
    preservation_ok: bool,
) -> None:
    by_tier = (
        voltage_summary.loc[
            voltage_summary.scenario_id.eq("plant_candidates_initial_20mvar")
        ]
        .groupby("voltage_kv")
        .agg(
            bus_count=("bus", "size"),
            under_voltage=("voltage_classification", lambda s: int(s.eq("UNDER_VOLTAGE").sum())),
            over_voltage=("voltage_classification", lambda s: int(s.eq("OVER_VOLTAGE").sum())),
            within_limits=("voltage_classification", lambda s: int(s.eq("WITHIN_LIMITS").sum())),
        )
        .reset_index()
    )
    ranked = line_details.sort_values(
        "charging_q_1pu_mvar", ascending=False
    ).head(20)
    by_control = (
        gen.loc[gen.scenario_id.eq("reference_45_generators_20mvar")]
        .groupby(["scenario_id", "voltage_kv", "control"], as_index=False)
        .agg(generator_count=("generator", "size"), generation_q_mvar=("q_mvar", "sum"),
             q_absorbed_mvar=("q_absorbed_mvar", "sum"),
             q_limit_violations=("q_limit_violation", "sum"))
    )
    candidate = comparison.set_index("scenario_id").loc[
        "plant_candidates_initial_20mvar"
    ]
    limited = comparison.set_index("scenario_id").loc[
        "plant_candidates_q_limited_recalculation"
    ]
    switch_lines = [
        f"- {item['generator']} @ bus {item['bus']}: Q={item['q_mvar']:.3f} MVAr, "
        f"limit={item['q_limit_mvar']:.3f} MVAr, violation={item['violation_mvar']:.3f} MVAr."
        for item in attempts["q_limit_violations"]
    ]
    switch_text = "\n".join(switch_lines) or "- 최초 PF에서 Q 한도 위반 없음."
    switch_records = attempts.get("pv_to_pq_switches", [])
    switch_details = "\n".join(
        f"- {item['generator']} @ bus {item['bus']}: PV→PQ, "
        f"Q_set={item['q_limit_mvar']:.3f} MVAr."
        for item in switch_records
    ) or "- PV 제어 모선 전환 없음."
    report = [
        "# MN_35k PoC 무효전력·전압 이상 원인 진단",
        "",
        "> 발전소 후보 PF의 최초 수렴 결과는 진단 목적의 수학적 해일 뿐, 제약을 만족하는 운전해가 아니다.",
        "",
        "## 핵심 판정",
        "",
        f"- 원본 모델 주 연결요소: 166모선 / 156선로 / 27변압기. 보호 대상 SHA-256 보존: **{preservation_ok}**.",
        f"- 1 pu 선로 충전 무효전력은 재계산·독립 검산 결과 **{charging.charging_q_1pu_mvar.sum():.3f} MVAr**다. "
        "이 값은 선로 susceptance로만 계산했으며 분로·변압기 Q와 합산하지 않았다.",
        f"- 후보 PF 최초 수렴: {candidate.pf_status}, {int(candidate.n_iter)}회, "
        f"V={candidate.v_min_pu:.3f}–{candidate.v_max_pu:.3f} pu, "
        f"{int(candidate.voltage_violation_count)}개 전압 위반, "
        f"{int(candidate.q_limit_violation_count)}개 발전기 Q 위반.",
        f"- PV→PQ 재계산: {limited.pf_status}, {int(limited.n_iter)}회, "
        f"최종 error={limited.error:.4g}. 이는 현재 토폴로지·전기정수·제어 정책에서 해를 찾지 못했다는 뜻이며, "
        "실제 발전소 조합의 공급 불가능성을 뜻하지 않는다.",
        "",
        "## 시나리오 비교",
        "",
        "기존 108대·단일 Slack·220 kV 중심 PV 결과는 기존 reactive-support 보고서의 PF 및 저장된 발전기·"
        "전압·branch 결과를 재사용했으며 진단 목적으로 재실행하지 않았다. 원본 자료에 없는 값은 비워 두었고, "
        "비수렴 이력은 유효한 발전·전압·손실 결과처럼 채우지 않았다.",
        "",
        comparison[
            [
                "scenario_id", "result_source", "pf_status", "n_iter", "error",
                "generator_count", "pv_bus_count", "pq_bus_count", "slack_bus_count",
                "q_limit_violation_count", "voltage_violation_count", "v_min_pu",
                "v_max_pu", "generation_p_mw", "generation_q_mvar",
                "line_max_loading_percent", "transformer_max_loading_percent",
                "load_p_mw", "load_q_mvar", "p_balance_residual_mw",
                "q_balance_residual_mvar",
            ]
        ].to_markdown(index=False, floatfmt=".5g"),
        "",
        "## 선로 충전 Q와 파라미터 검산",
        "",
        "| 전압 | 선로 | 합산 길이(km) | 합산 b(S) | 1 pu charging(MVAr) | 기여율 | 길이 미해결 선로/Q(MVAr) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in charging.itertuples(index=False):
        report.append(
            f"| {row.voltage_kv:.0f} kV | {row.line_count} | {row.length_km:.3f} | "
            f"{row.b_siemens:.9f} | {row.charging_q_1pu_mvar:.3f} | "
            f"{row.charging_share_percent:.2f}% | {row.unresolved_line_count} / "
            f"{row.unresolved_charging_q_1pu_mvar:.3f} |"
        )
    report.extend(
        [
            "",
            "PyPSA 1.2.4에서 LineType의 `r_per_length`, `x_per_length`는 Ω/km, "
            "`c_per_length`는 nF/km, `f_nom`은 Hz이며, 길이는 km다. 타입이 있는 선로에 대해 "
            "`r=r_per_length·length/num_parallel`, `x=x_per_length·length/num_parallel`, "
            "`b=2π·f·C·length·num_parallel`로 재생성된다. `Network.calculate_dependent_values()`는 "
            "`b_pu=b·V_nom²`; 따라서 1 pu line charging은 `Q=b·V_nom²` MVAr다. "
            "보고서의 각 행은 이 결과를 타입 계수의 SI 단위 변환으로 독립 재계산했으며 허용오차 내 일치한다.",
            "",
            "전체 173모선 원본의 35 kV 선로 48개에는 `POC_35kV_SYNTHETIC_ACSR_reference`"
            "(9.968804 nF/km)가 적용됐다. 이는 합성 타입이며 실제 도체·배치 확인값이 아니다. "
            "166모선 PF 주 연결요소에는 35 kV 46개, 110 kV 91개, 220 kV 19개가 포함된다. "
            "110 kV는 PyPSA `243-AL1/39-ST1A 110.0` 타입(9 nF/km), "
            "220 kV는 `Al/St 240/40 2-bundle 220.0`(12.5 nF/km)이다. "
            "110/220 kV 타입명 전압은 전압 class와 일치하지만 표준 데이터의 도체·기하가 몽골 설비에 맞다는 "
            "검증은 아니다. "
            "220 kV 기여가 큰 핵심 이유는 Q가 V²에 비례하고 라인 길이·C도 크기 때문이다. "
            "타입명 전압 비교는 별도 CSV에 있다. 전체 원본의 unresolved length 18개 중 주 연결요소 16개는 "
            "현재 저장 길이 기준 106.933 MVAr 기여로 계산되며, 그중 220 kV 선로 1개가 89.287 MVAr다. "
            "나머지 2개는 PF 대상에서 제외된 분리 계통요소다. 실제 미확정 길이의 효과는 추정하지 않았다.",
            "",
            "### 변압기 모델의 전압 연결",
            "",
            "PF 대상 변압기는 35/110 kV 17대, 110/220 kV 10대다. 현재 모델에서는 전체 변압기 tap ratio가 "
            "1.0, phase shift가 0°이며, 보고된 terminal Q는 선로 charging과 분리했다. 변압기별 정격·임피던스와 "
            "tap/전압제어 자료는 실설비 검증값이 아니다. 변압기는 전압 등급 사이의 유일한 연결을 구성하는 "
            "구간이므로 이 가정은 전압 전달과 무효전력 흐름에 영향을 주며, 이번 수치만으로 변압기 단독 원인으로 "
            "특정할 수 없다.",
            "",
            "### 충전량 상위 20개 선로",
            "",
            "| 선로 ID | bus0–bus1 | kV | km | LineType | b(S) | 1pu Q(MVAr) | 후보 양단 Q 합(MVAr) |",
            "|---|---|---:|---:|---|---:|---:|---:|",
        ]
    )
    for row in ranked.itertuples(index=False):
        report.append(
            f"| {row.line_id} | {row.bus0}–{row.bus1} | {row.voltage_kv:.0f} | "
            f"{row.length_km:.3f} | {row.line_type} | {row.b_siemens:.8g} | "
            f"{row.charging_q_1pu_mvar:.3f} | {row.q_terminal_net_candidate_mvar:.3f} |"
        )
    report.extend(
        [
            "",
            "양단 실제 `q0+q1`은 도체의 무부하 충전량과 같지 않다. 실제 단자 Q에는 양단 전압·위상, 직렬 리액턴스, "
            "전력 흐름 및 충전 susceptance 영향이 함께 들어간다. 두 양을 별도 CSV 열로 보존했다.",
            "",
            "## 전압 위반 위치",
            "",
            "| kV | 전체 모선 | 저전압 | 과전압 | 기준 내 |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for row in by_tier.itertuples(index=False):
        report.append(
            f"| {row.voltage_kv:.0f} | {row.bus_count} | {row.under_voltage} | "
            f"{row.over_voltage} | {row.within_limits} |"
        )
    low = voltage_summary.loc[
        voltage_summary.scenario_id.eq("plant_candidates_initial_20mvar")
    ].nsmallest(20, "v_mag_pu")
    high = voltage_summary.loc[
        voltage_summary.scenario_id.eq("plant_candidates_initial_20mvar")
    ].nlargest(20, "v_mag_pu")
    report.extend(
        [
            "",
            "후보 초기 PF의 최저 20개 및 최고 20개 모선, 모선 전압 등급, 제어기까지 최단 누적 직렬 X(pu), "
            "가장 가까운 제어 모선은 `voltage_violation_analysis.csv`에 있다. 전기적 거리는 전체 등가망의 "
            "양의 직렬 reactance 합이 최소인 경로로 산정했으며, 지리적 거리와 동일하지 않다.",
            "",
            f"최저 전압 모선은 bus {low.iloc[0].bus} ({low.iloc[0].voltage_kv:.0f} kV, "
            f"{low.iloc[0].v_mag_pu:.3f} pu), 최고 전압은 bus {high.iloc[0].bus} "
            f"({high.iloc[0].voltage_kv:.0f} kV, {high.iloc[0].v_mag_pu:.3f} pu)다. "
            "0.197 pu와 1.561 pu는 일반적인 정상 연속운전 전압으로 받아들일 수 없다. "
            "극단적인 합성 제어·Q 한도 초과를 포함한 진단용 Newton 해로만 취급한다.",
            "",
            "## 발전기 Q·제어 방식",
            "",
            "45 발전기는 실제 발전소가 아니라 합성 전압 제어기다. 45대 기준 PF에는 PV 제어기 44대와 "
            "Slack 1대가 시작에 배치되며, Q 한도 재계산에서 PV 1대가 PQ로 전환된 결과는 43 PV+1 Slack이다. "
            "발전소 후보는 초기 2 PV+1 Slack이다. "
            "제어 버스별 P/Q·Q 한도·흡수 비중은 `generator_q_distribution.csv`에 있다.",
            "",
            f"후보 초기 PF에서 발전기 Q는 {candidate.generation_q_mvar:.3f} MVAr이고 Slack의 Q는 "
            f"{candidate.slack_q_mvar:.3f} MVAr다. 선로의 총 충전량이 모두 발전기 Q로 흡수된다고 직접 "
            "해석하면 안 되지만, 220 kV 선로만 713.980 MVAr이고 제한된 PV 제어원 수 때문에 무효전력 "
            "흡수·전압 제어 부담이 소수 발전기에 집중됐다. 발전소 후보 2대도 실제 전기 접속이 아니라 "
            "`ASSUMED_CONNECTION`이다. 합성 Slack을 향후 외부계통 전압원과 발전기 객체로 분리하고, "
            "무효보상은 별도 Shunt/STATCOM 설비·한도로 표현하는 것이 우선이다. 후보 PF에서 "
            "line 양단 Q 합은 -1,074.205 MVAr, transformer 양단 Q 합은 +9.608 MVAr였고, "
            "20 MVAr shunt는 -20 MVAr를 흡수했다. 비정상 전압에서 계산된 terminal Q이므로 "
            "1 pu no-load charging과 혼동하면 안 된다. 계산상 P loss는 line 57.164 MW와 transformer "
            "0.254 MW였으며 이 역시 해당 진단점의 결과다.",
            "",
            "### 45대 구성의 제어전압별 Q 요약",
            "",
            by_control.to_markdown(index=False, floatfmt=".4g"),
            "",
            "## 제한된 민감도 실험",
            "",
            comparison.to_markdown(index=False, floatfmt=".6g"),
            "",
            "선로 b=0 실험은 해당 전압 등급 선로에서만 charging susceptance를 제거한 원인분리용 민감도이며 "
            "전기적 개선안이 아니다. PyPSA가 다음 PF 전에 LineType에서 값을 다시 계산하지 않도록 진단 복사본의 "
            "`type`을 비우고 b를 직접 변경한 뒤 `calculate_dependent_values()` 후에도 유지되는지 assertion했다. "
            "부하, topology, r/x, 변압기 및 발전기 정책은 그대로 두었다.",
            "",
            "리액터 0/20/40 MVAr는 기존 후보 모선에 한 대만 둔 제한적 합성 민감도다. 위치·용량의 실제 근거는 없고 "
            "후보 PF의 전압/Q 한도 변화 방향만 비교하며 운영 개선으로 채택하지 않는다. 이 위치 가정은 Slack "
            "모선에 두어 이상적인 Slack 전압을 고정하는 동시에 그 Q가 리액터 변화를 받아들인다. 실제 계산에서는 "
            "용량을 0/20/40 MVAr로 바꿔도 전압·branch Q는 같고 Slack Q만 20 MVAr 간격으로 변했다. "
            "따라서 이 실험은 다른 설치 위치의 전압 개선 효과를 검증하지 않는다.",
            "",
            "## PV→PQ 이후 비수렴 분석",
            "",
            switch_text,
            "",
            switch_details,
            "",
            f"최초 후보 Newton 해에서는 Q 위반이 {len(attempts['q_limit_violations'])}건이었다. "
            f"PV→PQ 후 제어점은 PV {attempts['post']['pv_bus_count']}개, "
            f"PQ {attempts['post']['pq_bus_count']}개, Slack {attempts['post']['slack_bus_count']}개가 되었다. "
            f"제약 적용 후 Slack은 Slack 제어를 유지해 Q 한도 enforcement 대상이 아니며, 미제약 Q는 "
            f"{attempts['slack_q_mvar']:.3f} MVAr로 가정한 하한 -100 MVAr를 "
            f"{max(0.0, -100.0-attempts['slack_q_mvar']):.3f} MVAr 넘었다.",
            "",
            f"유한차분 Newton mismatch Jacobian의 2-노름 조건수는 전환 전 "
            f"{attempts['pre']['jacobian_condition_number_2']:.4g}, 전환 직후(기존 수렴전압 초기값에서) "
            f"{attempts['post']['jacobian_condition_number_2']:.4g}였다. 두 행렬 모두 full rank "
            f"({attempts['pre']['jacobian_rank']}/{attempts['pre']['unknown_count']}, "
            f"{attempts['post']['jacobian_rank']}/{attempts['post']['unknown_count']})이며, 전환 후 초기 "
            f"mismatch norm은 {attempts['post']['mismatch_norm_mva']:.3f} MVAr였다. 이는 국소 민감도 진단이지 "
            "전체 Newton 반복의 condition history나 보장된 불안정도 판정이 아니다. PyPSA PF가 공개 산출한 "
            f"최종 error는 {limited.error:.4g}이며, 호출 경로에서 iteration별 residual history는 저장되지 않아 "
            "수렴발산 궤적을 복원하지 않았다. Q 제한은 PV 제어방정식을 Q 지정 방정식으로 전환해 두 PV 전압을 "
            "더 이상 1pu로 유지하지 못하게 하고, 남은 Slack 하나는 큰 Q 위반을 그대로 가진다. 초기점의 "
            "극단 전압과 큰 선로 charging, 35kV LineType 및 미검증 변압기 파라미터가 함께 있어 비수렴은 "
            "제어 자유도 감소·나쁜 초기점·모델 민감도가 결합된 결과일 가능성이 높다. 한 원인으로 단정하지 않는다.",
            "",
            "최초 후보 상태의 유효전력 출력은 Q 한도와 전압을 위반한 진단점이며, load 25.6 MW보다 커 보이는 "
            "P 발전량 차이는 branch P loss로 추적해 비교표에 분리 기록했다. 이것을 정상 운전 손실로 해석하지 않는다.",
            "",
            "## 다음 개선 순서와 한계",
            "",
            "1. 운영자 단선도·bus ID 대응·스위치 상태·실제 35/110/220kV 선로 및 변압기 명판을 확보하고 토폴로지를 확인한다.",
            "2. 18개 길이 미해결 선로와 35kV 합성 도체·배치 가정을 검증한다. 110/220kV 공개 표준 LineType도 실선로와 대조한다.",
            "3. 시각이 일치하는 발전기 P/Q capability, 부하 P/Q, tie-line 교환과 외부계통 경계조건을 확보한다.",
            "4. 실제 발전기, 외부 Slack/그리드 인피드, 발전기 Q capability, shunt reactor/capacitor를 서로 다른 장치 역할·한도로 모델링한다.",
            "5. 그 뒤 Q 제한 전환, 전압 기준, 허용 전압, PF 반복과 민감도를 운영 데이터에 대해 검증한다. 본 실험에서 새 발전기, branch, 리액터나 impedance 변경은 하지 않았다.",
            "",
            "## 재현성",
            "",
            f"기존 모델과 PF·확장·데이터 참고 보고서 SHA-256 보존: **{preservation_ok}**. "
            "모든 민감도는 메모리 복사본에서만 수행했다. 출력 CSV는 재실행 시 같은 입력에 대해 해시 비교할 수 있도록 "
            "`diagnostic_assumptions.json`에 checksum을 기록했다.",
            "",
            "![전압 등급별 선로 충전 Q](./line_charging_by_voltage.png)",
            "",
            "![모선 전압 분포](./bus_voltage_distribution.png)",
            "",
        ]
    )
    (output / "reactive_diagnostics_report.md").write_text("\n".join(report))



def _generate(paths, output, settings, base_config, support_settings, data_settings):
    protected_before = source_manifest(paths)
    source = pypsa.Network(logical_input(paths, base_config["input_model"]))
    main_buses, _, _ = select_component(source, base_config)
    if (len(main_buses), len(source.lines), len(source.transformers)) != (166, 159, 27):
        raise ValueError("Unexpected base network component counts")

    reference_spec = next(
        item for item in support_settings["scenarios"]
        if item["id"] == settings["scenarios"]["reference"]
    )
    reference, _ = build_reactive_scenario(
        source, base_config, support_settings, reference_spec
    )
    reference_outcome, reference_attempts, _ = execute_with_q_limits(
        reference, base_config, True
    )
    if reference_outcome["status"] != "AC_PF_CONVERGED":
        raise RuntimeError("The existing 45-generator reference no longer converges")
    reference_limits = _network_q_limits(reference, base_config)

    identity_path = logical_input(paths, data_settings["reports_dir"] + "/plant_identity_audit.csv")
    candidate_path = logical_input(paths, data_settings["reports_dir"] + "/plant_bus_candidates.csv")
    identity = pd.read_csv(identity_path)
    bus_candidates = pd.read_csv(candidate_path)
    _, candidates = _candidate_rows(data_settings, identity, bus_candidates)
    if len(candidates) != 2:
        raise ValueError(f"Expected the two audited candidates, got {len(candidates)}")
    candidate, candidate_limits, _ = _candidate_network(
        source, base_config, support_settings, data_settings, candidates
    )
    candidate_outcome = execute_pf(candidate, base_config)
    if candidate_outcome["status"] != "AC_PF_CONVERGED":
        raise RuntimeError("Candidate diagnostic starting PF did not reproduce convergence")
    candidate_initial = copy.deepcopy(candidate)
    candidate_limits = _network_q_limits(candidate, base_config, candidate_limits)
    q_violations = _q_violations(candidate, candidate_limits)
    initial_bus_counts = {
        "PV": int(candidate.buses.control.eq("PV").sum()),
        "PQ": int(candidate.buses.control.eq("PQ").sum()),
        "Slack": int(candidate.buses.control.eq("Slack").sum()),
    }
    pre_jacobian = _jacobian_condition(candidate)

    switched = copy.deepcopy(candidate)
    pv_switches = []
    for item in q_violations:
        if item["control"] != "PV":
            continue
        switched.generators.at[item["generator"], "control"] = "PQ"
        switched.generators.at[item["generator"], "q_set"] = item["q_limit_mvar"]
        pv_switches.append(item)
    post_jacobian = _jacobian_condition(switched)
    post_counts = {
        "PV": int(switched.buses.control.eq("PV").sum()),
        "PQ": int(switched.buses.control.eq("PQ").sum()),
        "Slack": int(switched.buses.control.eq("Slack").sum()),
    }
    limited_outcome = execute_pf(switched, base_config)
    nonconvergence = {
        "q_limit_violations": q_violations,
        "pv_to_pq_switches": pv_switches,
        "slack_q_mvar": float(
            candidate.generators_t.q.loc[
                candidate.snapshots[0],
                candidate.generators.index[candidate.generators.control.eq("Slack")],
            ].sum()
        ),
        "pre": pre_jacobian,
        "post": post_jacobian,
        "initial": initial_bus_counts,
        "post_counts": post_counts,
        "post_outcome": limited_outcome,
    }

    comparisons = []
    bus_frames = []
    generator_frames = []
    reactive_frames = []
    flow_tables: dict[str, pd.DataFrame] = {}

    def record_case(
        scenario_id: str,
        description: str,
        network: pypsa.Network,
        outcome: dict,
        limits: dict[str, tuple[float, float]],
        modified_class: float | None = None,
        reactor: float | None = None,
    ) -> None:
        summary = _scenario_summary(
            scenario_id, description, network, outcome, limits,
            None, modified_class, reactor, base_config=base_config,
        )
        comparisons.append(summary)
        if outcome["status"] != "AC_PF_CONVERGED" or not summary["finite_results"]:
            return
        bus_rows = voltage_analysis(
            network, scenario_id, 0.9, 1.1, settings["report_top_n"]
        )
        if scenario_id.startswith("plant_candidates"):
            bus_rows["result_classification"] = "NUMERICAL_DIAGNOSTIC_ONLY"
        bus_frames.append(bus_rows)
        p_values = network.generators_t.p.loc[network.snapshots[0]]
        q_values = network.generators_t.q.loc[network.snapshots[0]]
        gen_rows = []
        for name, generator in network.generators.iterrows():
            q = float(q_values.at[name])
            q_min, q_max = limits[str(name)]
            gen_rows.append(
                {
                    "scenario_id": scenario_id,
                    "generator": str(name),
                    "bus": str(generator.bus),
                    "voltage_kv": float(network.buses.at[generator.bus, "v_nom"]),
                    "control": str(generator.control),
                    "p_mw": float(p_values.at[name]),
                    "q_mvar": q,
                    "q_min_mvar": q_min,
                    "q_max_mvar": q_max,
                    "q_limit_violation": q < q_min - 1e-9 or q > q_max + 1e-9,
                    "q_absorbed_mvar": max(-q, 0.0),
                    "q_supplied_mvar": max(q, 0.0),
                    "is_synthetic_controller": str(name).startswith("SYNTHETIC_"),
                    "result_classification": (
                        "NUMERICAL_DIAGNOSTIC_ONLY"
                        if scenario_id.startswith("plant_candidates")
                        else "REFERENCE_SCREENING_RESULT"
                    ),
                }
            )
        gen = pd.DataFrame(gen_rows)
        total_absorbed = float(gen.q_absorbed_mvar.sum())
        gen["share_of_generator_q_absorption_percent"] = (
            100 * gen.q_absorbed_mvar / total_absorbed if total_absorbed else 0.0
        )
        generator_frames.append(gen)
        line, transformer = _flow_tables(network)
        if scenario_id in {"reference_45_generators_20mvar", "plant_candidates_initial_20mvar"}:
            flow_tables[scenario_id] = line
        reactive_frames.append(
            reactive_balance_by_voltage(network, scenario_id, line, transformer)
        )

    historical = pd.read_csv(
        logical_input(paths, "reports/reactive_support/scenario_comparison.csv")
    ).set_index("scenario_id")
    old_bus_results = pd.read_csv(logical_input(paths, "reports/reactive_support/bus_voltage_results.csv"))
    old_generator_results = pd.read_csv(
        logical_input(paths, "reports/reactive_support/generator_reactive_results.csv")
    )
    old_branch_results = pd.read_csv(
        logical_input(paths, "reports/reactive_support/branch_loading_results.csv")
    )
    for scenario_id, description in {
        "reference_108_generators": "기존 108대 합성 제어기 결과 재사용",
        "single_slack": "Slack 1대 결과 재사용",
        "limited_220kv_pv": "Slack + 220 kV PV 5대 결과 재사용",
    }.items():
        if scenario_id not in historical.index:
            raise ValueError(f"Missing reusable historical scenario {scenario_id}")
        old = historical.loc[scenario_id]
        old_buses = old_bus_results.loc[old_bus_results.scenario_id.eq(scenario_id)]
        old_generators = old_generator_results.loc[
            old_generator_results.scenario_id.eq(scenario_id)
        ]
        old_branches = old_branch_results.loc[
            old_branch_results.scenario_id.eq(scenario_id)
        ]
        old_lines = old_branches.loc[old_branches.component.eq("line")]
        old_transformers = old_branches.loc[old_branches.component.eq("transformer")]
        old_converged = old.pf_status == "AC_PF_CONVERGED"
        comparisons.append(
            {
                "scenario_id": scenario_id,
                "description": description,
                "pf_status": old.pf_status,
                "n_iter": old.n_iter,
                "error": old.error,
                "bus_count": 166,
                "line_count": 156,
                "transformer_count": 27,
                "generator_count": old.generator_count,
                "pv_bus_count": old.pv_bus_count,
                "pq_bus_count": old.pq_bus_count,
                "slack_bus_count": old.slack_bus_count,
                "q_limit_violation_count": old.q_violation_count,
                "voltage_violation_count": (
                    int(old_buses.voltage_violation.sum()) if old_converged else None
                ),
                "v_min_pu": old.v_min_pu,
                "v_max_pu": old.v_max_pu,
                "generation_p_mw": (
                    float(old_generators.p_mw.sum()) if old_converged else None
                ),
                "generation_q_mvar": (
                    float(old_generators.q_mvar.sum()) if old_converged else None
                ),
                "load_p_mw": old.load_mw,
                "load_q_mvar": old.load_mvar,
                "line_p_loss_mw": (
                    float(old_lines.p_loss_mw.sum()) if old_converged else None
                ),
                "transformer_p_loss_mw": (
                    float(old_transformers.p_loss_mw.sum()) if old_converged else None
                ),
                "line_max_loading_percent": (
                    float(old.line_max_loading_percent) if old_converged else None
                ),
                "transformer_max_loading_percent": (
                    float(old.transformer_max_loading_percent) if old_converged else None
                ),
                "line_terminal_q_net_mvar": (
                    float(old_lines.q_net_mvar.sum()) if old_converged else None
                ),
                "transformer_terminal_q_net_mvar": (
                    float(old_transformers.q_net_mvar.sum()) if old_converged else None
                ),
                "shunt_q_mvar": old.shunt_actual_q_mvar,
                "slack_p_mw": old.slack_p_mw,
                "slack_q_mvar": old.slack_q_mvar,
                "p_balance_residual_mw": old.p_balance_residual_mw,
                "q_balance_residual_mvar": old.q_balance_residual_mvar,
                "nodal_max_residual_mva": old.nodal_max_residual_mva,
                "modified_line_voltage_class_kv": np.nan,
                "reactor_nominal_mvar": old.shunt_nominal_mvar,
                "finite_results": bool(old.pf_status == "AC_PF_CONVERGED"),
                "assessment": "REUSED_EXISTING_PF_RESULT",
                "result_source": "reports/reactive_support/scenario_comparison.csv",
            }
        )

    record_case(
        "reference_45_generators_20mvar",
        "45개 합성 발전기 + 20 MVAr 리액터, Q 제한 재계산 포함",
        reference,
        reference_outcome,
        reference_limits,
        reactor=20.0,
    )
    record_case(
        "plant_candidates_initial_20mvar",
        "Dornod/Erdenet 가정 접속 + Slack, Q 제한 전 최초 수렴; 진단 전용",
        candidate,
        candidate_outcome,
        candidate_limits,
        reactor=20.0,
    )
    comparisons.append(
        _scenario_summary(
            "plant_candidates_q_limited_recalculation",
            "PV 두 대를 Q 경계 PQ로 전환한 재계산",
            switched,
            limited_outcome,
            candidate_limits,
            None,
            reactor_mvar=20.0, base_config=base_config,
        )
    )

    for voltage in settings["line_charging_sensitivity"]["voltage_classes_kv"]:
        trial, reasons = build_reactive_scenario(
            source, base_config, support_settings, reference_spec
        )
        _line_charging_sensitivity(
            trial,
            float(voltage),
            float(settings["line_charging_sensitivity"]["b_scale"]),
        )
        outcome, _, _ = execute_with_q_limits(trial, base_config, True)
        record_case(
            f"line_b_zero_{int(voltage)}kv",
            f"{voltage:g} kV line b set to 0 on an in-memory copy",
            trial,
            outcome,
            _network_q_limits(trial, base_config),
            modified_class=float(voltage),
            reactor=20.0,
        )

    for reactor in settings["reactor_sensitivity_mvar"]:
        if float(reactor) == float(settings["candidate_base_reactor_mvar"]):
            continue
        trial, trial_limits, _ = _candidate_network(
            source, base_config, support_settings, data_settings, candidates
        )
        _change_reactor_rating(trial, float(reactor))
        outcome = execute_pf(trial, base_config)
        record_case(
            f"plant_candidates_reactor_{float(reactor):g}mvar",
            f"Candidate PF with a diagnostic-only {float(reactor):g} MVAr reactor sensitivity",
            trial,
            outcome,
            _network_q_limits(trial, base_config, trial_limits),
            reactor=float(reactor),
        )

    line, transformer = _flow_tables(reference)
    flow_tables["reference_45_generators_20mvar"] = line
    line, transformer = _flow_tables(candidate_initial)
    flow_tables["plant_candidates_initial_20mvar"] = line
    line_details, charging = line_charging_analysis(
        source, main_buses, flow_tables,
        tuple(settings["normal_voltage_range_pu"]),
    )
    bus_voltage = pd.concat(bus_frames, ignore_index=True)
    gen_q = pd.concat(generator_frames, ignore_index=True)
    reactive_balance = pd.concat(reactive_frames, ignore_index=True)
    bus_voltage.to_csv(output / "voltage_violation_analysis.csv", index=False, float_format="%.12g")
    line_details.to_csv(output / "line_charging_analysis.csv", index=False, float_format="%.12g")
    gen_q.to_csv(output / "generator_q_distribution.csv", index=False, float_format="%.12g")
    charging.to_csv(output / "line_charging_by_voltage.csv", index=False, float_format="%.12g")
    reactive_balance.to_csv(output / "reactive_balance_by_voltage.csv", index=False, float_format="%.12g")
    comparison = pd.DataFrame(comparisons)
    if not {
        "reference_108_generators",
        "single_slack",
        "limited_220kv_pv",
    }.issubset(set(comparison.scenario_id)):
        raise AssertionError("Historical control scenarios are missing from comparison")
    if not (
        comparison.bus_count.eq(166).all()
        and comparison.line_count.eq(156).all()
        and comparison.transformer_count.eq(27).all()
    ):
        raise AssertionError("A diagnostic scenario does not preserve the PF topology")
    nonconverged = comparison.pf_status.ne("AC_PF_CONVERGED")
    if comparison.loc[nonconverged, "finite_results"].fillna(False).any():
        raise AssertionError("A nonconverged PF must not be labeled as finite results")
    comparison.to_csv(output / "diagnostic_scenario_comparison.csv", index=False, float_format="%.12g")

    _save_plots(charging, bus_voltage, output)

    type_summary = (
        source.lines.loc[source.lines.bus0.isin(main_buses) & source.lines.bus1.isin(main_buses)]
        .groupby(["v_nom", "type"], as_index=False)
        .size()
        .to_dict("records")
    )
    attempts_detail = {
        **nonconvergence,
        "final_pf": limited_outcome,
        "iteration_residual_history_available": False,
        "nonconvergence_interpretation": (
            "PV-to-PQ releases two voltage magnitudes and adds two Q equations; "
            "the remaining Slack stays unbounded by the helper's PV-only limit conversion. "
            "Large initial voltage excursions, line charging and unverified passive parameters "
            "are correlated diagnostics, not individually proven causes."
        ),
    }
    switch_text = "\n".join(
        f"- {item['generator']} @ bus {item['bus']}: Q={item['q_mvar']:.3f} MVAr, "
        f"limit={item['q_limit_mvar']:.3f} MVAr, violation={item['violation_mvar']:.3f} MVAr."
        for item in q_violations
    ) or "- 최초 PF에서 Q 한도 위반 없음."
    switch_details = "\n".join(
        f"- {item['generator']} @ bus {item['bus']}: PV→PQ, "
        f"Q_set={item['q_limit_mvar']:.3f} MVAr."
        for item in pv_switches
    ) or "- PV 제어 모선 전환 없음."
    nonconv_report = [
        "# 후보 발전소 시나리오 PV→PQ 재계산 비수렴 분석",
        "",
        "## 관측 사실",
        "",
        f"- 최초 PF: {candidate_outcome['status']}, "
        f"{candidate_outcome['subnetworks'][0]['n_iter']}회, "
        f"error={candidate_outcome['subnetworks'][0]['error']:.4g}.",
        f"- Q 위반: {len(q_violations)}건. Slack Q={nonconvergence['slack_q_mvar']:.3f} MVAr이며 "
        "helper는 PV만 PQ로 변환하므로 Slack Q 한도는 이 전환 과정에서 enforcement되지 않는다.",
        switch_text,
        "",
        switch_details,
        "",
        f"- 최초 제어 구성 Slack/PV/PQ={initial_bus_counts['Slack']}/"
        f"{initial_bus_counts['PV']}/{initial_bus_counts['PQ']}; 변환 후 "
        f"{post_counts['Slack']}/{post_counts['PV']}/{post_counts['PQ']}. "
        f"방정식/미지수 차원은 {pre_jacobian['mismatch_equation_count']}/"
        f"{pre_jacobian['unknown_count']}에서 "
        f"{post_jacobian['mismatch_equation_count']}/{post_jacobian['unknown_count']}로 증가한다.",
        f"- Newton mismatch Jacobian condition number: pre={pre_jacobian['jacobian_condition_number_2']:.6g}, "
        f"post-at-initial-state={post_jacobian['jacobian_condition_number_2']:.6g}; "
        f"rank={pre_jacobian['jacobian_rank']}/{pre_jacobian['unknown_count']} → "
        f"{post_jacobian['jacobian_rank']}/{post_jacobian['unknown_count']}, "
        f"post mismatch norm={post_jacobian['mismatch_norm_mva']:.6g} MVAr. "
        "유한차분·비정규화 국소 계산이며 전체 반복 경로의 조건수는 아니다.",
        f"- 제한 후 PF: {limited_outcome['status']}, "
        f"{limited_outcome['subnetworks'][0]['n_iter']}회, "
        f"최종 error={limited_outcome['subnetworks'][0]['error']:.6g}. "
        "PyPSA wrapper에서 iteration별 residual history는 제공되지 않는다.",
        "",
        "## 해석 한계",
        "",
        "비수렴의 유력한 직접 메커니즘은 PV 제어가 사라져 PV 버스 두 곳의 전압 자유도가 생기고 Q를 고정하는 "
        "동시에, 남은 Slack에 큰 무효전력 요구가 남아 있는 것이다. 초기점도 0.197–1.561 pu의 진단용 해였고 "
        "선로 충전은 1 pu에서 약 934.5 MVAr다. 초기 PV→PQ Jacobian은 평가점에서 full rank였으므로 "
        "그 점에서의 명백한 특이성은 관측되지 않았다. iteration 중 특이성·불안정 여부까지 배제할 수는 없다. "
        "계통 연결, 35 kV type, 미확정 길이 18개, 합성 변압기 임피던스·정격이 미검증이다.",
        "",
        "100회 비수렴은 현재 모델 및 가정된 dispatch/control에서 수치 해를 찾지 못한 결과다. 실제 설비 조합의 "
        "공급 불가능성에 대한 결론이 아니다.",
        "",
    ]
    (output / "nonconvergence_analysis.md").write_text("\n".join(nonconv_report))
    _report(
        output, line_details, charging, bus_voltage, comparison, gen_q,
        nonconvergence, True
    )

    protected_after = source_manifest(paths)
    preserved = protected_before == protected_after
    if not preserved:
        raise AssertionError("A protected model or prior report changed during diagnostics")
    output_files = sorted(
        path for path in output.iterdir()
        if path.is_file() and path.name != "diagnostic_assumptions.json"
    )
    assumptions = {
        "pypsa_version": pypsa.__version__,
        "input_model": base_config["input_model"],
        "original_model_counts": {
            "buses": len(source.buses),
            "lines": len(source.lines),
            "transformers": len(source.transformers),
            "main_component_buses": len(main_buses),
            "main_component_lines": 156,
        },
        "line_type_assignments": type_summary,
        "calculation": {
            "b_siemens": "2*pi*f_hz*C_nf_per_km*1e-9*length_km*num_parallel",
            "q_charging_mvar": "b_siemens*V_nom_kv^2*V_pu^2",
            "normal_voltage_range_pu": settings["normal_voltage_range_pu"],
            "line_terminal_q_is_separate": True,
            "transformer_and_shunt_not_in_line_charging_sum": True,
            "line_type_parameter_recalculation_verified": True,
        },
        "candidate_scenario": {
            "plant_record_ids": [row["record_id"] for row in candidates],
            "connection_status": "ASSUMED_CONNECTION",
            "initial_pf_is_operable_solution": False,
            "initial_pf_status": candidate_outcome["status"],
            "q_limit_switches": pv_switches,
            "q_limited_pf_status": limited_outcome["status"],
            "q_limited_pf_error": limited_outcome["subnetworks"][0]["error"],
        },
        "nonconvergence_jacobian_diagnostics": attempts_detail,
        "experiments": {
            "line_b_zero_by_voltage_class": settings["line_charging_sensitivity"],
            "candidate_reactor_sensitivity_mvar": sorted(
                {
                    0.0,
                    float(settings["candidate_base_reactor_mvar"]),
                    *[float(v) for v in settings["reactor_sensitivity_mvar"]],
                }
            ),
            "all_network_changes_in_memory_only": True,
            "impedance_or_topology_changes": False,
        },
        "protected_inputs_unchanged": preserved,
        "protected_hashes_before": protected_before,
        "protected_hashes_after": protected_after,
        "output_sha256": {
            path.name: sha256(path) for path in output_files
        },
    }
    (output / "diagnostic_assumptions.json").write_text(
        json.dumps(assumptions, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    _report(
        output, line_details, charging, bus_voltage, comparison, gen_q,
        nonconvergence, preserved
    )
    print(
        json.dumps(
            {
                "reports_dir": str(output.name),
                "line_charging_total_mvar": float(charging.charging_q_1pu_mvar.sum()),
                "candidate_voltage_violation_count": int(
                    comparison.set_index("scenario_id").at[
                        "plant_candidates_initial_20mvar", "voltage_violation_count"
                    ]
                ),
                "candidate_q_limited_status": limited_outcome["status"],
                "protected_inputs_unchanged": preserved,
            },
            ensure_ascii=False,
        )
    )
    return {"classification": "DIAGNOSTIC_ONLY_NOT_OPERATING_MODEL",
            "line_charging_total_mvar": float(charging.charging_q_1pu_mvar.sum()),
            "candidate_q_limited_status": limited_outcome["status"],
            "q_violations": q_violations, "pv_to_pq_switches": pv_switches,
            "scenario_results": comparison.replace({np.nan: None}).to_dict("records")}


EXPECTED_FILES = ('bus_voltage_distribution.png', 'diagnostic_assumptions.json', 'diagnostic_scenario_comparison.csv', 'generator_q_distribution.csv', 'line_charging_analysis.csv', 'line_charging_by_voltage.csv', 'line_charging_by_voltage.png', 'nonconvergence_analysis.md', 'reactive_balance_by_voltage.csv', 'reactive_diagnostics_report.md', 'voltage_violation_analysis.csv')


def analyze_for_verification(paths, destination, *, config_path=None):
    settings = read_config(paths, "configs/mn35k_reactive_diagnostics.json", config_path)
    data_settings, base_config, support_settings = read_settings(paths)
    if settings["base_config"] != data_settings["base_config"] or settings["reactive_support_config"] != data_settings["reactive_support_config"]:
        raise ValueError("Diagnostic and candidate configuration identities differ")
    return analysis_bundle(paths, destination, EXPECTED_FILES,
        lambda output: _generate(paths, output, settings, base_config, support_settings, data_settings))
