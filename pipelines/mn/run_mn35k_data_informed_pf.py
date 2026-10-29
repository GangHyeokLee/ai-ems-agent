"""Run explicitly assumed plant-candidate and load-sensitivity AC PF cases."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .audit_mn35k_plants import audit_tables
from .run_mn35k_acpf import execute_pf, select_component
from .run_mn35k_reactive_support import (markdown, _result_tables, build_reactive_scenario,
    execute_with_q_limits, validate_reactive_results)
from .mn35k_refinement import export_deterministic
from .inventory import sha256
from .diagnostic_inputs import (analysis_bundle, source_manifest, read_config, base_settings)
from .passive_inputs import logical_input


def _power_factor(p_mw: float, q_mvar: float) -> float:
    apparent = math.hypot(p_mw, q_mvar)
    return abs(p_mw) / apparent if apparent else 1.0



def _bus_control_counts(network: pypsa.Network) -> dict[str, int]:
    pv_buses = set(network.generators.loc[
        network.generators.control.eq("PV"), "bus"
    ])
    slack_buses = set(network.generators.loc[
        network.generators.control.eq("Slack"), "bus"
    ])
    if pv_buses & slack_buses:
        raise ValueError("A bus cannot be counted as both PV and Slack")
    return {
        "PV": len(pv_buses),
        "Slack": len(slack_buses),
        "PQ": len(network.buses) - len(pv_buses) - len(slack_buses),
    }



def _candidate_rows(
    settings: dict, identity: pd.DataFrame, bus_candidates: pd.DataFrame
) -> tuple[pd.DataFrame, list[dict]]:
    scenario = settings["plant_candidate_scenario"]
    selected = identity.loc[
        identity.record_id.isin(scenario["candidate_record_ids"])
    ].copy()
    if len(selected) != len(scenario["candidate_record_ids"]):
        return selected, []
    selected_buses = bus_candidates.loc[
        bus_candidates.record_id.isin(scenario["candidate_record_ids"])
        & bus_candidates.candidate_rank_all_voltages.eq(1)
    ].drop(columns=["identity_status", "selection_note"])
    rows = selected.merge(selected_buses, on="record_id", how="left")
    eligible = []
    for item in rows.itertuples(index=False):
        snapshot_year = pd.Timestamp(settings["snapshot"]).year
        date_active = (
            pd.notna(item.date_in)
            and pd.notna(item.date_out)
            and float(item.date_in) <= snapshot_year
            and float(item.date_out) >= snapshot_year
        )
        if (
            item.identity_status != "DISTINCT_CONFIRMED"
            or not date_active
            or item.connection_status != "SPATIAL_CANDIDATE"
            or float(item.distance_km) > scenario["maximum_bus_distance_km"]
            or not bool(item.in_166_bus_main_component)
            or float(item.candidate_voltage_kv) not in (110.0, 220.0)
        ):
            continue
        eligible.append(
            {
                "record_id": int(item.record_id),
                "name": str(item.plant_name),
                "capacity_mw": float(item.capacity_mw),
                "fuel_type": str(item.fuel_type),
                "technology": "" if pd.isna(item.technology) else str(item.technology),
                "date_in": float(item.date_in),
                "date_out": float(item.date_out),
                "bus": str(item.candidate_bus),
                "voltage_kv": float(item.candidate_voltage_kv),
                "distance_km": float(item.distance_km),
                "identity_status": str(item.identity_status),
                "connection_status": scenario["connection_status"],
                "capacity_classification": "PUBLIC_DATA_ESTIMATE",
                "date_operational_status": "UNRESOLVED",
                "p_dispatch_classification": "SYNTHETIC_ASSUMPTION",
                "selection_reason": scenario["selection_reason"],
            }
        )
    if len({row["bus"] for row in eligible}) != len(eligible):
        raise ValueError("Selected plant candidates map to the same PV bus")
    return rows, eligible



def _candidate_network(
    source: pypsa.Network,
    base_config: dict,
    support_settings: dict,
    settings: dict,
    candidates: list[dict],
) -> tuple[pypsa.Network, dict[str, float], dict[str, str]]:
    if not candidates:
        raise ValueError("No plant records passed identity/date/spatial screening")
    reference = next(
        item
        for item in support_settings["scenarios"]
        if item["id"] == "reduced_27_q_limited_with_reactor"
    )
    trial = copy.deepcopy(reference)
    trial["id"] = settings["plant_candidate_scenario"]["scenario_id"]
    trial["label"] = settings["plant_candidate_scenario"]["label"]
    trial["pv_buses"] = [item["bus"] for item in candidates]
    trial["selection_reason"] = settings["plant_candidate_scenario"][
        "selection_reason"
    ]
    network, reasons = build_reactive_scenario(
        source, base_config, support_settings, trial
    )

    total_capacity = sum(item["capacity_mw"] for item in candidates)
    load_mw = float(network.loads.p_set.sum())
    candidate_share = settings["plant_candidate_scenario"][
        "candidate_generation_share_of_synthetic_load"
    ]
    slack_share = settings["plant_candidate_scenario"][
        "slack_generation_share_of_synthetic_load"
    ]
    if not math.isclose(candidate_share + slack_share, 1.0, abs_tol=1e-12):
        raise ValueError("Candidate and Slack dispatch shares must sum to one")

    slack_name = "SYNTHETIC_SLACK_" + str(
        network.meta["synthetic_acpf"]["slack_bus"]
    )
    q_limits = {
        slack_name: (
            float(base_config["generator"]["q_min_mvar"]),
            float(base_config["generator"]["q_max_mvar"]),
        )
    }
    dispatch_records = {}
    for candidate in candidates:
        generator = "SYNTHETIC_PV_" + candidate["bus"]
        network.generators.at[generator, "p_nom"] = candidate["capacity_mw"]
        network.generators.at[generator, "p_set"] = (
            load_mw * candidate_share * candidate["capacity_mw"] / total_capacity
        )
        q_limit = candidate["capacity_mw"] * math.tan(
            math.acos(settings["plant_candidate_scenario"]["minimum_power_factor"])
        )
        q_limits[generator] = (-q_limit, q_limit)
        reasons[candidate["bus"]] = candidate["selection_reason"]
        dispatch_records[generator] = candidate
    network.generators.at[slack_name, "p_set"] = load_mw * slack_share
    network.meta["synthetic_reactive_support"]["scenario"] = {
        "id": trial["id"],
        "label": trial["label"],
        "connection_status": "ASSUMED_CONNECTION",
        "plant_record_ids": [item["record_id"] for item in candidates],
        "plant_generator_capacity_source": "PUBLIC_DATA_ESTIMATE",
        "plant_dispatch_source": "SYNTHETIC_ASSUMPTION",
    }
    network.meta["synthetic_reactive_support"]["plant_candidates"] = dispatch_records
    network.meta["synthetic_reactive_support"]["generator_q_limits_mvar"] = q_limits
    network.meta["synthetic_reactive_support"]["initial_bus_control_counts"] = {
        key: int(value) for key, value in _bus_control_counts(network).items()
    }
    network.meta["synthetic_reactive_support"]["generator_reasons"].update(
        {
            "SYNTHETIC_PV_" + item["bus"]: (
                f"Plant row {item['record_id']} capacity used as a public-data estimate; "
                "bus tie and dispatch are synthetic assumptions."
            )
            for item in candidates
        }
    )
    network.meta["synthetic_reactive_support"]["q_limit_method"] = (
        "Candidate generators use symmetric Q limits derived from the assumed "
        "minimum power factor at reported nameplate capacity; Slack uses the "
        "reference synthetic limit. Violating PV generators are manually changed "
        "to PQ at the exceeded bound and the AC PF is rerun."
    )
    return network, q_limits, reasons



def _execute_candidate(
    network: pypsa.Network,
    base_config: dict,
    q_limits: dict[str, tuple[float, float]],
) -> tuple[dict, list[dict]]:
    attempts = []
    switched = set()
    for round_index in range(len(network.generators) + 1):
        outcome = execute_pf(network, base_config)
        violations = []
        if outcome["status"] == "AC_PF_CONVERGED":
            q_values = network.generators_t.q.loc[network.snapshots[0]]
            for generator, q in q_values.items():
                lower, upper = q_limits[str(generator)]
                if q < lower - 1e-9:
                    violations.append(
                        {
                            "generator": str(generator),
                            "bus": str(network.generators.at[generator, "bus"]),
                            "control": str(network.generators.at[generator, "control"]),
                            "q_mvar": float(q),
                            "q_limit_mvar": lower,
                        }
                    )
                elif q > upper + 1e-9:
                    violations.append(
                        {
                            "generator": str(generator),
                            "bus": str(network.generators.at[generator, "bus"]),
                            "control": str(network.generators.at[generator, "control"]),
                            "q_mvar": float(q),
                            "q_limit_mvar": upper,
                        }
                    )
            screen_tables, screen_validation = validate_reactive_results(
                network, base_config
            )
            p_balance = screen_tables["power_balance_validation"].loc["P"]
            q_balance = screen_tables["power_balance_validation"].loc["Q"]
            diagnostics = {
                "v_min_pu": float(
                    screen_tables["bus_voltage_results"].v_mag_pu.min()
                ),
                "v_max_pu": float(
                    screen_tables["bus_voltage_results"].v_mag_pu.max()
                ),
                "voltage_violation_count": int(
                    screen_validation["violations"]["bus_voltage"]
                ),
                "line_max_loading_percent": float(
                    screen_tables["line_flow_results"].loading_percent.max()
                ),
                "line_thermal_violation_count": int(
                    screen_validation["violations"]["line_thermal"]
                ),
                "transformer_max_loading_percent": float(
                    screen_tables["transformer_flow_results"].loading_percent.max()
                ),
                "transformer_thermal_violation_count": int(
                    screen_validation["violations"]["transformer_thermal"]
                ),
                "generator_p_violation_count": int(
                    screen_validation["violations"]["generator_p"]
                ),
                "generator_s_violation_count": int(
                    screen_validation["violations"]["generator_s"]
                ),
                "q_limit_violation_count": len(violations),
                "p_balance_residual_mw": float(p_balance.residual),
                "q_balance_residual_mvar": float(q_balance.residual),
                "nodal_max_residual_mva": float(
                    screen_validation["max_nodal_residual_mva"]
                ),
                "shunt_actual_q_mvar": float(
                    screen_tables["shunt_reactor_results"].q_mvar.sum()
                ),
                "is_final_operating_point": False,
                "interpretation": (
                    "Intermediate converged PF before manual PV-to-PQ conversion; "
                    "not an accepted operating solution."
                ),
            }
        else:
            diagnostics = None
        attempts.append(
            {
                "round": round_index,
                "phase": "initial_pf" if round_index == 0 else "pv_to_pq_recalculation",
                "result": outcome,
                "q_limit_violations": violations,
                "generator_results": (
                    [
                        {
                            "generator": str(generator),
                            "bus": str(network.generators.at[generator, "bus"]),
                            "p_mw": float(
                                network.generators_t.p.at[
                                    network.snapshots[0], generator
                                ]
                            ),
                            "q_mvar": float(
                                network.generators_t.q.at[
                                    network.snapshots[0], generator
                                ]
                            ),
                            "q_min_mvar": q_limits[str(generator)][0],
                            "q_max_mvar": q_limits[str(generator)][1],
                        }
                        for generator in network.generators.index
                    ]
                    if outcome["status"] == "AC_PF_CONVERGED"
                    else []
                ),
                "shunt_results": (
                    [
                        {
                            "shunt": str(shunt),
                            "bus": str(network.shunt_impedances.at[shunt, "bus"]),
                            "q_mvar": float(
                                network.shunt_impedances_t.q.at[
                                    network.snapshots[0], shunt
                                ]
                            ),
                        }
                        for shunt in network.shunt_impedances.index
                    ]
                    if outcome["status"] == "AC_PF_CONVERGED"
                    else []
                ),
                "screening_diagnostics": diagnostics,
            }
        )
        if outcome["status"] != "AC_PF_CONVERGED":
            break
        actionable = [
            item
            for item in violations
            if item["control"] == "PV" and item["generator"] not in switched
        ]
        if not actionable:
            break
        for violation in actionable:
            generator = violation["generator"]
            network.generators.at[generator, "control"] = "PQ"
            network.generators.at[generator, "q_set"] = violation["q_limit_mvar"]
            switched.add(generator)
        network.meta["synthetic_reactive_support"]["q_limit_switches"] = [
            {
                **item,
                "new_control": "PQ",
                "new_q_set_mvar": item["q_limit_mvar"],
                "method": "manual PV-to-PQ conversion; rerun AC PF",
            }
            for item in violations
            if item["generator"] in switched
        ]
    else:
        raise RuntimeError("Candidate Q-limit loop exceeded its generator-count guard")
    return outcome, attempts



def _candidate_result_tables(
    scenario_id: str,
    network: pypsa.Network,
    base_config: dict,
    reasons: dict[str, str],
    q_limits: dict[str, tuple[float, float]],
) -> tuple[dict[str, pd.DataFrame], dict]:
    tables, validation = _result_tables(
        scenario_id, network, base_config, reasons
    )
    generators = tables["generator_reactive_results"]
    generators["q_min_mvar"] = generators.generator.map(
        lambda name: q_limits[str(name)][0]
    )
    generators["q_max_mvar"] = generators.generator.map(
        lambda name: q_limits[str(name)][1]
    )
    generators["q_limit_violation"] = (
        (generators.q_mvar < generators.q_min_mvar - 1e-9)
        | (generators.q_mvar > generators.q_max_mvar + 1e-9)
    )
    q_violations = int(generators.q_limit_violation.sum())
    validation["violations"]["generator_q"] = q_violations
    violations = validation["violations"]
    passed = bool(
        validation["finite_results"]
        and validation["controls_verified"]
        and validation["loads_verified"]
        and validation["power_balance_verified"]
        and validation["nodal_balance_verified"]
    )
    validation["screening_status"] = (
        "SYNTHETIC_SCENARIO_HAS_VIOLATIONS"
        if passed and any(violations.values())
        else "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS"
        if passed
        else "MODEL_DATA_REQUIRES_REVIEW"
    )
    validation["operating_constraints_satisfied"] = bool(
        passed and not any(violations.values())
    )
    return tables, validation



def _status(outcome: dict, validation: dict | None) -> str:
    if outcome["status"] != "AC_PF_CONVERGED":
        return "DATA_INFORMED_SCENARIO_NOT_CONVERGED"
    if validation is None or not validation["operating_constraints_satisfied"]:
        return "DATA_INFORMED_SCENARIO_CONVERGED_WITH_VIOLATIONS"
    return "DATA_INFORMED_SCENARIO_CONVERGED_WITHIN_ASSUMED_LIMITS"



def _load_table(network: pypsa.Network, scenario_id: str) -> pd.DataFrame:
    frame = network.loads[["bus", "p_set", "q_set"]].copy()
    frame.index.name = "load"
    frame = frame.reset_index()
    frame["scenario_id"] = scenario_id
    frame["voltage_kv"] = frame.bus.map(network.buses.v_nom)
    frame["power_factor"] = frame.apply(
        lambda row: _power_factor(float(row.p_set), float(row.q_set)), axis=1
    )
    frame["classification"] = "SYNTHETIC_ASSUMPTION"
    frame["allocation_policy"] = (
        "Uniform multiplier of the inherited deterministic per-bus load; "
        "not calibrated to a Mongolian measurement."
    )
    return frame



def _dispatch_table(
    network: pypsa.Network,
    scenario_id: str,
    candidates: list[dict] | None = None,
    q_limits: dict[str, tuple[float, float]] | None = None,
    pf_results_valid: bool = True,
) -> pd.DataFrame:
    candidate_map = {
        "SYNTHETIC_PV_" + row["bus"]: row
        for row in candidates or []
    }
    q_limits = q_limits or {
        str(name): (
            float(network.meta["synthetic_reactive_support"].get("q_min_mvar", -100)),
            float(network.meta["synthetic_reactive_support"].get("q_max_mvar", 100)),
        )
        for name in network.generators.index
    }
    rows = []
    for name, generator in network.generators.iterrows():
        candidate = candidate_map.get(str(name))
        q_min, q_max = q_limits[str(name)]
        p = (
            network.generators_t.p.loc[network.snapshots[0]].get(name, np.nan)
            if pf_results_valid
            else np.nan
        )
        q = (
            network.generators_t.q.loc[network.snapshots[0]].get(name, np.nan)
            if pf_results_valid
            else np.nan
        )
        rows.append(
            {
                "scenario_id": scenario_id,
                "generator": str(name),
                "bus": str(generator.bus),
                "voltage_kv": float(network.buses.at[generator.bus, "v_nom"]),
                "control": str(generator.control),
                "capacity_mw": float(generator.p_nom),
                "p_set_mw": float(generator.p_set),
                "p_mw": float(p),
                "q_mvar": float(q),
                "q_min_mvar": q_min,
                "q_max_mvar": q_max,
                "power_factor": _power_factor(float(p), float(q)),
                "plant_record_id": candidate["record_id"] if candidate else None,
                "plant_name_reference": candidate["name"] if candidate else None,
                "fuel_reference": candidate["fuel_type"] if candidate else None,
                "capacity_classification": (
                    "PUBLIC_DATA_ESTIMATE" if candidate else "SYNTHETIC_ASSUMPTION"
                ),
                "dispatch_classification": "SYNTHETIC_ASSUMPTION",
                "connection_status": (
                    "ASSUMED_CONNECTION" if candidate else "SYNTHETIC_CONTROL"
                ),
                "pf_status": network.meta["synthetic_reactive_support"].get(
                    "pf_final_status", "NOT_RUN"
                ),
                "dispatch_result_valid": pf_results_valid,
                "q_limit_violation": (
                    not (q_min - 1e-9 <= float(q) <= q_max + 1e-9)
                    if pf_results_valid
                    else None
                ),
            }
        )
    return pd.DataFrame(rows)



def _scenario_row(
    scenario_id: str,
    label: str,
    network: pypsa.Network,
    outcome: dict,
    attempts: list[dict],
    validation: dict | None,
    tables: dict[str, pd.DataFrame] | None,
    candidates: list[dict] | None = None,
) -> dict:
    last = outcome["subnetworks"][0] if outcome.get("subnetworks") else {}
    converged_attempts = [
        item
        for item in attempts
        if item["result"]["status"] == "AC_PF_CONVERGED"
    ]
    last_converged_attempt = converged_attempts[-1] if converged_attempts else None
    row = {
        "scenario_id": scenario_id,
        "label": label,
        "classification": _status(outcome, validation),
        "pf_status": outcome["status"],
        "bus_count": len(network.buses),
        "line_count": len(network.lines),
        "transformer_count": len(network.transformers),
        "generator_count": len(network.generators),
        "pv_bus_count": int(network.buses.control.eq("PV").sum()),
        "pq_bus_count": int(network.buses.control.eq("PQ").sum()),
        "slack_bus_count": int(network.buses.control.eq("Slack").sum()),
        "initial_pv_bus_count": network.meta["synthetic_reactive_support"].get(
            "initial_bus_control_counts", {}
        ).get("PV", int(network.buses.control.eq("PV").sum())),
        "initial_pq_bus_count": network.meta["synthetic_reactive_support"].get(
            "initial_bus_control_counts", {}
        ).get("PQ", int(network.buses.control.eq("PQ").sum())),
        "plant_candidate_generator_count": len(candidates or []),
        "public_plant_capacity_mw_estimate": sum(
            item["capacity_mw"] for item in candidates or []
        ),
        "synthetic_controller_count": len(network.generators) - len(candidates or []),
        "shunt_count": len(network.shunt_impedances),
        "shunt_nominal_mvar": sum(
            item["nominal_q_absorption_mvar"]
            for item in network.meta["synthetic_reactive_support"].get(
                "shunt_reactors", []
            )
        ),
        "load_mw": float(network.loads.p_set.sum()),
        "load_mvar": float(network.loads.q_set.sum()),
        "load_power_factor": _power_factor(
            float(network.loads.p_set.sum()), float(network.loads.q_set.sum())
        ),
        "total_generation_mw": None,
        "total_generation_mvar": None,
        "slack_p_mw": None,
        "slack_q_mvar": None,
        "shunt_actual_q_mvar": None,
        "net_demand_mw": float(network.loads.p_set.sum()),
        "net_demand_mvar": float(network.loads.q_set.sum()),
        "q_violation_count": None,
        "last_converged_q_violation_count": (
            len(last_converged_attempt.get("q_limit_violations", []))
            if last_converged_attempt
            else None
        ),
        "last_converged_generation_p_mw": None,
        "last_converged_generation_q_mvar": None,
        "last_converged_shunt_q_mvar": None,
        "last_converged_v_min_pu": None,
        "last_converged_v_max_pu": None,
        "last_converged_line_max_loading_percent": None,
        "last_converged_transformer_max_loading_percent": None,
        "last_converged_p_balance_residual_mw": None,
        "last_converged_q_balance_residual_mvar": None,
        "last_converged_nodal_max_residual_mva": None,
        "last_converged_voltage_violation_count": None,
        "last_converged_line_thermal_violation_count": None,
        "last_converged_transformer_thermal_violation_count": None,
        "last_converged_generator_p_violation_count": None,
        "last_converged_generator_s_violation_count": None,
        "q_limit_switch_count": sum(
            item.get("new_control") == "PQ"
            for item in network.meta["synthetic_reactive_support"].get(
                "q_limit_switches", []
            )
        ),
        "n_iter": last.get("n_iter"),
        "error": last.get("error"),
        "pf_rounds": len(attempts),
        "v_min_pu": None,
        "v_max_pu": None,
        "line_max_loading_percent": None,
        "transformer_max_loading_percent": None,
        "p_balance_residual_mw": None,
        "q_balance_residual_mvar": None,
        "nodal_max_residual_mva": None,
        "voltage_violation_count": None,
        "line_thermal_violation_count": None,
        "transformer_thermal_violation_count": None,
        "generator_p_violation_count": None,
        "generator_s_violation_count": None,
        "connection_status": (
            "ASSUMED_CONNECTION" if candidates else "SYNTHETIC_CONTROL"
        ),
        "actual_generator_count": 0,
    }
    if validation is not None and tables is not None:
        gen_dispatch = network.generators_t.p.loc[network.snapshots[0]]
        gen_reactive = network.generators_t.q.loc[network.snapshots[0]]
        slack_name = network.generators.index[
            network.generators.control.eq("Slack")
        ][0]
        row.update(
            total_generation_mw=float(gen_dispatch.sum()),
            total_generation_mvar=float(gen_reactive.sum()),
            slack_p_mw=float(gen_dispatch.at[slack_name]),
            slack_q_mvar=float(gen_reactive.at[slack_name]),
            shunt_actual_q_mvar=float(
                tables["shunt_reactor_results"].q_mvar.sum()
            ),
            q_violation_count=validation["violations"]["generator_q"],
            v_min_pu=float(tables["bus_voltage_results"].v_mag_pu.min()),
            v_max_pu=float(tables["bus_voltage_results"].v_mag_pu.max()),
            line_max_loading_percent=float(
                tables["line_flow_results"].loading_percent.max()
            ),
            transformer_max_loading_percent=float(
                tables["transformer_flow_results"].loading_percent.max()
            ),
            p_balance_residual_mw=float(
                tables["power_balance_validation"]
                .set_index("quantity")
                .at["P", "residual"]
            ),
            q_balance_residual_mvar=float(
                tables["power_balance_validation"]
                .set_index("quantity")
                .at["Q", "residual"]
            ),
            nodal_max_residual_mva=validation["max_nodal_residual_mva"],
            voltage_violation_count=validation["violations"]["bus_voltage"],
            line_thermal_violation_count=validation["violations"]["line_thermal"],
            transformer_thermal_violation_count=validation["violations"][
                "transformer_thermal"
            ],
            generator_p_violation_count=validation["violations"]["generator_p"],
            generator_s_violation_count=validation["violations"]["generator_s"],
            last_converged_v_min_pu=float(
                tables["bus_voltage_results"].v_mag_pu.min()
            ),
            last_converged_v_max_pu=float(
                tables["bus_voltage_results"].v_mag_pu.max()
            ),
            last_converged_line_max_loading_percent=float(
                tables["line_flow_results"].loading_percent.max()
            ),
            last_converged_transformer_max_loading_percent=float(
                tables["transformer_flow_results"].loading_percent.max()
            ),
            last_converged_p_balance_residual_mw=float(
                tables["power_balance_validation"]
                .set_index("quantity")
                .at["P", "residual"]
            ),
            last_converged_q_balance_residual_mvar=float(
                tables["power_balance_validation"]
                .set_index("quantity")
                .at["Q", "residual"]
            ),
            last_converged_nodal_max_residual_mva=float(
                validation["max_nodal_residual_mva"]
            ),
            last_converged_voltage_violation_count=validation["violations"][
                "bus_voltage"
            ],
            last_converged_line_thermal_violation_count=validation["violations"][
                "line_thermal"
            ],
            last_converged_transformer_thermal_violation_count=validation[
                "violations"
            ]["transformer_thermal"],
            last_converged_generator_p_violation_count=validation["violations"][
                "generator_p"
            ],
            last_converged_generator_s_violation_count=validation["violations"][
                "generator_s"
            ],
        )
        row["last_converged_generation_p_mw"] = float(gen_dispatch.sum())
        row["last_converged_generation_q_mvar"] = float(gen_reactive.sum())
        row["last_converged_q_violation_count"] = int(
            validation["violations"]["generator_q"]
        )
        row["last_converged_shunt_q_mvar"] = float(
            tables["shunt_reactor_results"].q_mvar.sum()
        )
    elif last_converged_attempt:
        rows = last_converged_attempt.get("generator_results", [])
        row["last_converged_generation_p_mw"] = sum(item["p_mw"] for item in rows)
        row["last_converged_generation_q_mvar"] = sum(item["q_mvar"] for item in rows)
        row["last_converged_shunt_q_mvar"] = sum(
            item["q_mvar"]
            for item in last_converged_attempt.get("shunt_results", [])
        )
        diagnostics = last_converged_attempt.get("screening_diagnostics") or {}
        row.update(
            last_converged_v_min_pu=diagnostics.get("v_min_pu"),
            last_converged_v_max_pu=diagnostics.get("v_max_pu"),
            last_converged_line_max_loading_percent=diagnostics.get(
                "line_max_loading_percent"
            ),
            last_converged_transformer_max_loading_percent=diagnostics.get(
                "transformer_max_loading_percent"
            ),
            last_converged_p_balance_residual_mw=diagnostics.get(
                "p_balance_residual_mw"
            ),
            last_converged_q_balance_residual_mvar=diagnostics.get(
                "q_balance_residual_mvar"
            ),
            last_converged_nodal_max_residual_mva=diagnostics.get(
                "nodal_max_residual_mva"
            ),
            last_converged_voltage_violation_count=diagnostics.get(
                "voltage_violation_count"
            ),
            last_converged_line_thermal_violation_count=diagnostics.get(
                "line_thermal_violation_count"
            ),
            last_converged_transformer_thermal_violation_count=diagnostics.get(
                "transformer_thermal_violation_count"
            ),
            last_converged_generator_p_violation_count=diagnostics.get(
                "generator_p_violation_count"
            ),
            last_converged_generator_s_violation_count=diagnostics.get(
                "generator_s_violation_count"
            ),
        )
    return row



def _candidate_block_reason(
    settings: dict,
    identity: pd.DataFrame,
    buses: pd.DataFrame,
) -> str | None:
    _, candidates = _candidate_rows(settings, identity, buses)
    if len(candidates) < len(settings["plant_candidate_scenario"]["candidate_record_ids"]):
        return (
            "No selected record set passed identity status, source-year, candidate-bus "
            "distance, main-component and supported-voltage screening. Electrical "
            "connection evidence is absent; actual-plant scenario is blocked."
        )
    return None



def _scenario_table(
    network: pypsa.Network,
    scenario_id: str,
    label: str,
    base_config: dict,
    attempts: list[dict],
    candidates: list[dict] | None = None,
    q_limits: dict[str, tuple[float, float]] | None = None,
    reasons: dict[str, str] | None = None,
) -> tuple[dict, dict[str, pd.DataFrame] | None, dict | None]:
    outcome = attempts[-1]["result"]
    tables = None
    validation = None
    if outcome["status"] == "AC_PF_CONVERGED":
        if candidates:
            tables, validation = _candidate_result_tables(
                scenario_id,
                network,
                base_config,
                reasons or {},
                q_limits or {},
            )
        else:
            tables, validation = _result_tables(
                scenario_id,
                network,
                base_config,
                reasons or {},
            )
    summary = _scenario_row(
        scenario_id, label, network, outcome, attempts, validation, tables, candidates
    )
    return summary, tables, validation



def _generate(paths, reports, settings, base_config, support_settings):
    protected_before = source_manifest(paths)
    identity, bus_candidates = audit_tables(paths)
    identity.to_csv(reports / "plant_identity_audit.csv", index=False)
    bus_candidates.to_csv(reports / "plant_bus_candidates.csv", index=False)
    candidate_rows, candidates = _candidate_rows(
        settings, identity, bus_candidates
    )
    candidate_rows.to_csv(reports / "plant_candidate_selection.csv", index=False)
    source = pypsa.Network(logical_input(paths, base_config["input_model"]))
    main_buses, _, _ = select_component(source, base_config)
    summaries = []
    dispatch_frames = []
    attempt_dispatch_frames = []
    attempt_shunt_frames = []
    attempt_validation_frames = []
    load_frames = []
    result_frames: dict[str, list[pd.DataFrame]] = {
        "bus_voltage_results": [],
        "line_flow_results": [],
        "transformer_flow_results": [],
        "power_balance_validation": [],
        "nodal_balance_validation": [],
        "shunt_reactor_results": [],
    }
    all_attempts = []
    output_model_hash = None

    reference_spec = next(
        item
        for item in support_settings["scenarios"]
        if item["id"] == "reduced_27_q_limited_with_reactor"
    )
    reference, reference_reasons = build_reactive_scenario(
        source, base_config, support_settings, reference_spec
    )
    reference.meta["synthetic_reactive_support"]["initial_bus_control_counts"] = {
        key: int(value) for key, value in _bus_control_counts(reference).items()
    }
    reference_outcome, reference_attempts, _ = execute_with_q_limits(
        reference, base_config, reference_spec["enforce_pv_q_limits"]
    )
    reference.meta["synthetic_reactive_support"]["pf_final_status"] = (
        reference_outcome["status"]
    )
    all_attempts.append(
        {
            "scenario_id": "reference_45_generators_20mvar",
            "attempts": reference_attempts,
        }
    )
    reference_summary, reference_tables, _ = _scenario_table(
        reference,
        "reference_45_generators_20mvar",
        "기존 45대 + 20MVAr 기준, 재실행",
        base_config,
        reference_attempts,
        reasons=reference_reasons,
    )
    summaries.append(reference_summary)
    load_frames.append(_load_table(reference, reference_summary["scenario_id"]))
    if reference_tables is not None:
        for name in result_frames:
            result_frames[name].append(reference_tables[name])
    dispatch_frames.append(
        _dispatch_table(
            reference,
            reference_summary["scenario_id"],
            pf_results_valid=reference_outcome["status"] == "AC_PF_CONVERGED",
        )
    )

    blocked = _candidate_block_reason(settings, identity, bus_candidates)
    candidate_scenario = settings["plant_candidate_scenario"]
    if blocked:
        summaries.append(
            {
                "scenario_id": candidate_scenario["scenario_id"],
                "label": candidate_scenario["label"],
                "classification": "BLOCKED_BY_DATA_GAPS",
                "pf_status": "NOT_RUN",
                "block_reason": blocked,
                "bus_count": len(main_buses),
                "plant_candidate_generator_count": 0,
                "actual_generator_count": 0,
            }
        )
        all_attempts.append(
            {
                "scenario_id": candidate_scenario["scenario_id"],
                "status": "BLOCKED_BY_DATA_GAPS",
                "reason": blocked,
                "attempts": [],
            }
        )
    else:
        candidate_network, candidate_q_limits, candidate_reasons = _candidate_network(
            source,
            base_config,
            support_settings,
            settings,
            candidates,
        )
        candidate_outcome, candidate_attempts = _execute_candidate(
            candidate_network, base_config, candidate_q_limits
        )
        candidate_network.meta["synthetic_reactive_support"]["pf_final_status"] = (
            candidate_outcome["status"]
        )
        all_attempts.append(
            {
                "scenario_id": candidate_scenario["scenario_id"],
                "connection_status": "ASSUMED_CONNECTION",
                "plant_candidate_record_ids": [
                    item["record_id"] for item in candidates
                ],
                "attempts": candidate_attempts,
            }
        )
        for attempt in candidate_attempts:
            for result in attempt.get("generator_results", []):
                attempt_dispatch_frames.append(
                    {
                        "scenario_id": candidate_scenario["scenario_id"],
                        "round": attempt["round"],
                        "phase": attempt["phase"],
                        "pf_status": attempt["result"]["status"],
                        **result,
                        "power_factor": _power_factor(
                            result["p_mw"], result["q_mvar"]
                        ),
                        "q_limit_violation": not (
                            result["q_min_mvar"] - 1e-9
                            <= result["q_mvar"]
                            <= result["q_max_mvar"] + 1e-9
                        ),
                        "is_final_operating_point": False,
                        "connection_status": "ASSUMED_CONNECTION"
                        if result["generator"].startswith("SYNTHETIC_PV_")
                        else "SYNTHETIC_CONTROL",
                    }
                )
            for result in attempt.get("shunt_results", []):
                attempt_shunt_frames.append(
                    {
                        "scenario_id": candidate_scenario["scenario_id"],
                        "round": attempt["round"],
                        "phase": attempt["phase"],
                        "pf_status": attempt["result"]["status"],
                        **result,
                        "is_final_operating_point": False,
                    }
                )
            diagnostics = attempt.get("screening_diagnostics")
            if diagnostics:
                attempt_validation_frames.append(
                    {
                        "scenario_id": candidate_scenario["scenario_id"],
                        "round": attempt["round"],
                        "phase": attempt["phase"],
                        "pf_status": attempt["result"]["status"],
                        **diagnostics,
                    }
                )
        summary, candidate_tables, _ = _scenario_table(
            candidate_network,
            candidate_scenario["scenario_id"],
            candidate_scenario["label"],
            base_config,
            candidate_attempts,
            candidates,
            candidate_q_limits,
            candidate_reasons,
        )
        summaries.append(summary)
        load_frames.append(_load_table(candidate_network, summary["scenario_id"]))
        if candidate_tables is not None:
            for name in result_frames:
                result_frames[name].append(candidate_tables[name])
        dispatch_frames.append(
            _dispatch_table(
                candidate_network,
                summary["scenario_id"],
                candidates,
                candidate_q_limits,
                pf_results_valid=candidate_outcome["status"] == "AC_PF_CONVERGED",
            )
        )
        if candidate_outcome["status"] == "AC_PF_CONVERGED":
            model_path = reports / Path(settings["output_model"]).name
            export_deterministic(candidate_network, model_path, paths=paths,
                                 protected_models=base_config["protected_models"])
            output_model_hash = sha256(model_path)

    for multiplier in settings["demand"]["sensitivity_multipliers"]:
        scenario_id = f"reference_45_generators_load_{multiplier:g}x"
        trial = copy.deepcopy(reference_spec)
        trial["id"] = scenario_id
        trial["label"] = f"기준 합성부하의 {multiplier:g}배 민감도"
        network, reasons = build_reactive_scenario(
            source, base_config, support_settings, trial
        )
        network.meta["synthetic_reactive_support"]["initial_bus_control_counts"] = {
            key: int(value) for key, value in _bus_control_counts(network).items()
        }
        network.loads.loc[:, "p_set"] *= multiplier
        network.loads.loc[:, "q_set"] *= multiplier
        network.generators.loc[:, "p_set"] *= multiplier
        network.meta["synthetic_reactive_support"]["scenario"] = trial
        outcome, attempts, _ = execute_with_q_limits(
            network, base_config, trial["enforce_pv_q_limits"]
        )
        network.meta["synthetic_reactive_support"]["pf_final_status"] = outcome[
            "status"
        ]
        all_attempts.append({"scenario_id": scenario_id, "attempts": attempts})
        summary, tables, _ = _scenario_table(
            network,
            scenario_id,
            trial["label"],
            base_config,
            attempts,
            reasons=reasons,
        )
        summaries.append(summary)
        load_frames.append(_load_table(network, scenario_id))
        if tables is not None:
            for name in result_frames:
                result_frames[name].append(tables[name])
        dispatch_frames.append(
            _dispatch_table(
                network,
                scenario_id,
                pf_results_valid=outcome["status"] == "AC_PF_CONVERGED",
            )
        )

    comparison = pd.DataFrame(summaries)
    comparison.to_csv(reports / "scenario_comparison.csv", index=False)
    if dispatch_frames:
        pd.concat(dispatch_frames, ignore_index=True).to_csv(
            reports / "generator_dispatch.csv", index=False
        )
    if attempt_dispatch_frames:
        pd.DataFrame(attempt_dispatch_frames).to_csv(
            reports / "generator_dispatch_attempts.csv", index=False
        )
    if attempt_shunt_frames:
        pd.DataFrame(attempt_shunt_frames).to_csv(
            reports / "shunt_reactor_attempts.csv", index=False
        )
    if attempt_validation_frames:
        pd.DataFrame(attempt_validation_frames).to_csv(
            reports / "scenario_attempt_validation.csv", index=False
        )
    if load_frames:
        pd.concat(load_frames, ignore_index=True).to_csv(
            reports / "load_allocation.csv", index=False
        )
    result_names = {
        "bus_voltage_results": "bus_voltage_results.csv",
        "line_flow_results": "line_flow_results.csv",
        "transformer_flow_results": "transformer_flow_results.csv",
        "power_balance_validation": "power_balance_validation.csv",
        "nodal_balance_validation": "nodal_balance_validation.csv",
        "shunt_reactor_results": "shunt_reactor_results.csv",
    }
    for name, frames in result_frames.items():
        if frames:
            pd.concat(frames, ignore_index=True).to_csv(
                reports / result_names[name], index=False
            )
    assumptions = {
        "result_classification": (
            next(
                row["classification"]
                for row in summaries
                if row["scenario_id"] == candidate_scenario["scenario_id"]
            )
        ),
        "not_actual_mongolian_operation": True,
        "pypsa_version": pypsa.__version__,
        "snapshot_label": settings["snapshot"],
        "modeled_main_component_bus_count": len(main_buses),
        "base_load": {
            "p_mw": settings["demand"]["base_mw"],
            "q_mvar": settings["demand"]["base_mw"]
            * math.tan(math.acos(settings["demand"]["power_factor_lagging"])),
            "power_factor": settings["demand"]["power_factor_lagging"],
            "classification": "SYNTHETIC_ASSUMPTION",
        },
        "demand_multipliers": settings["demand"]["sensitivity_multipliers"],
        "candidate_plant_records": candidates,
        "plant_data_lineage": {
            "source_file": "pypsa-earth/resources/MN/powerplants.csv",
            "build_method": "PyPSA-Earth powerplantmatching output; local lock records powerplantmatching 0.9.1",
            "osm2pm_intermediate_rows": 0,
            "row_snapshot_and_per_source_operational_validation": "UNRESOLVED",
        },
        "candidate_connection_status": "ASSUMED_CONNECTION",
        "candidate_capacity_classification": "PUBLIC_DATA_ESTIMATE",
        "candidate_dispatch_classification": "SYNTHETIC_ASSUMPTION",
        "candidate_dispatch_rule": (
            "90% of the synthetic base active demand allocated proportional to row "
            "capacity; 10% initial Slack setpoint; Slack adjusts for losses. No source "
            "dispatch or fuel availability is inferred."
        ),
        "candidate_reactive_limits": (
            "Each plant candidate uses symmetric Q limits based on nameplate estimate "
            "and assumed minimum power factor 0.9. Slack retains the earlier synthetic "
            "screening limit. These are not actual capability curves."
        ),
        "load_allocation_policy": settings["demand"]["load_allocation"],
        "sensitivity_represents_real_mongolian_demand": False,
        "reactive_support_reference": (
            "Reference 45-generator + 20 MVAr shunt configuration reused without "
            "changing passive topology or line/transformer parameters."
        ),
        "slack_selection": {
            "bus": str(reference.meta["synthetic_acpf"]["slack_bus"]),
            "policy": reference.meta["synthetic_acpf"]["slack_policy"],
        },
        "scenario_results": summaries,
        "candidate_model_sha256": output_model_hash,
    }
    (reports / "scenario_assumptions.json").write_text(
        json.dumps(assumptions, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    (reports / "pf_attempts.json").write_text(
        json.dumps(all_attempts, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )

    protected_after = source_manifest(paths)
    preservation = {
        "unchanged": protected_before == protected_after,
        "before": protected_before,
        "after": protected_after,
    }
    (reports / "source_preservation.json").write_text(
        json.dumps(preservation, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    if not preservation["unchanged"]:
        raise AssertionError("A protected input model, data source or prior result changed")

    status = assumptions["result_classification"]
    candidate_summary = comparison.loc[
        comparison.scenario_id.eq(candidate_scenario["scenario_id"])
    ].iloc[0]
    candidate_slack_q = next(
        row["q_mvar"]
        for row in attempt_dispatch_frames
        if row["scenario_id"] == candidate_scenario["scenario_id"]
        and row["generator"].startswith("SYNTHETIC_SLACK_")
    ) if attempt_dispatch_frames else None
    candidate_diagnostic = (
        f"발전소 후보 시나리오의 Q 한도 적용 전 PF는 "
        f"{candidate_summary.last_converged_generation_p_mw:.3f}MW/"
        f"{candidate_summary.last_converged_generation_q_mvar:.3f}MVAr에서 수치적으로 수렴했지만, "
        f"그 시점에 Q 한도 위반 {int(candidate_summary.last_converged_q_violation_count)}건 "
        f"(Slack Q={candidate_slack_q:.3f}MVAr 포함)이 확인됐다. 전압 범위는 "
        f"{candidate_summary.last_converged_v_min_pu:.3f}–"
        f"{candidate_summary.last_converged_v_max_pu:.3f}pu로 "
        f"{int(candidate_summary.last_converged_voltage_violation_count)}개 모선에서 기준을 벗어났고, "
        f"발전기 S 한도 위반은 "
        f"{int(candidate_summary.last_converged_generator_s_violation_count)}건이었다. "
        f"선로·변압기 최대 부하율은 각각 "
        f"{candidate_summary.last_converged_line_max_loading_percent:.2f}%/"
        f"{candidate_summary.last_converged_transformer_max_loading_percent:.2f}%였다. "
        f"PV {int(candidate_summary.q_limit_switch_count)}대를 Q 한계의 PQ 모드로 전환해 재계산했지만 "
        f"{candidate_summary.pf_status}로 종료되어 최종 운전해로 채택하지 않았다. "
        "`last_converged_*` P/Q, 전압, 부하율, 수지값은 모두 제한 전 중간 진단값이며 "
        "유효 운전값이 아니다."
        if pd.notna(candidate_summary.last_converged_generation_p_mw)
        else "발전소 후보 PF는 자료 공백으로 실행되지 않았다."
    )
    report_columns = [
        "scenario_id",
        "classification",
        "pf_status",
        "load_mw",
        "load_mvar",
        "generator_count",
        "plant_candidate_generator_count",
        "public_plant_capacity_mw_estimate",
        "pv_bus_count",
        "pq_bus_count",
        "slack_bus_count",
        "initial_pv_bus_count",
        "initial_pq_bus_count",
        "total_generation_mw",
        "total_generation_mvar",
        "last_converged_generation_p_mw",
        "last_converged_generation_q_mvar",
        "last_converged_v_min_pu",
        "last_converged_v_max_pu",
        "last_converged_line_max_loading_percent",
        "last_converged_transformer_max_loading_percent",
        "last_converged_p_balance_residual_mw",
        "last_converged_q_balance_residual_mvar",
        "last_converged_nodal_max_residual_mva",
        "last_converged_voltage_violation_count",
        "last_converged_line_thermal_violation_count",
        "last_converged_transformer_thermal_violation_count",
        "last_converged_generator_p_violation_count",
        "last_converged_generator_s_violation_count",
        "slack_p_mw",
        "slack_q_mvar",
        "shunt_nominal_mvar",
        "shunt_actual_q_mvar",
        "last_converged_shunt_q_mvar",
        "last_converged_q_violation_count",
        "q_violation_count",
        "q_limit_switch_count",
        "n_iter",
        "error",
        "v_min_pu",
        "v_max_pu",
        "line_max_loading_percent",
        "transformer_max_loading_percent",
        "p_balance_residual_mw",
        "q_balance_residual_mvar",
        "nodal_max_residual_mva",
    ]
    report = [
        "# MN_35k 데이터 참고 합성 운전·AC PF 실험",
        "",
        f"## 최종 분류: `{status}`",
        "",
        "이 분류는 합성 운전 가정에 대한 수치 결과이며 몽골 운영계통의 타당성 검증이 아니다.",
        "",
        "## 발전소 레코드와 접속",
        "",
        f"27개 행의 identity audit 결과 `{(identity.identity_status == 'DISTINCT_CONFIRMED').sum()}`개는 "
        "고유 source record로 분류했고, 다중유닛 후보·중복후보·미해결 행은 별도로 보존했다. "
        "전기적 연결 증거가 있는 모선은 없으며, plant candidate PF는 `ASSUMED_CONNECTION`이다.",
        "",
        "Ulaanbaatar TES 6행은 동일 GEO-42393 ID/좌표에 92.6335MW 3회, 74.1068MW 3회가 "
        "각각 반복돼 중복후보로 남겼다. Ulaanbaatar lignite/steam 839MW 행은 GEM 8개 ID와 "
        "GPD 2개 ID를 포함해 다중유닛 후보로 분류했지만, 0.57km 떨어진 Oil/CHP-labelled "
        "TES 행과의 실제 설비 경계 및 연료·용량 중복 여부는 미해결이다. "
        "Khushight Kundi / Khushig Khundii는 15MW씩, 약 6.42km 간격, 서로 다른 source ID와 "
        "연도가 있어 중복후보로 구분했다. Serven Solar / Taishir Solar는 약 14m 좌표 차이, "
        "10/14.4MW 및 서로 다른 GEM ID·연도로 identity 미해결이다. 중복후보 용량은 합산하지 않았다.",
        "",
        "원본 powerplants.csv의 행/프로젝트 ID 존재는 `SOURCE_VERIFIED`, 용량·연료·좌표·연도 필드는 "
        "`PUBLIC_DATA_ESTIMATE`, 실제 운전/폐지 상태는 `UNRESOLVED`로 분리했다. "
        "PyPSA-Earth lock의 PowerplantMatching 버전은 0.9.1이며, 해당 OSM→PowerplantMatching "
        "중간 CSV는 0행이다. 행별 소스 스냅샷·운영자 검증자료는 제공되지 않는다.",
        "",
        "Nearest-bus 후보, 전압, 측지거리, 연결요소, OSM 발전기/선로/변전소 근접정보는 "
        "`plant_bus_candidates.csv` 및 `plant_identity_audit.csv`에 있다. 기존 숫자 bus 매핑은 사용하지 않았다.",
        "",
        "## 수요자료 한계와 합성 수요",
        "",
        "ERC 2025 통계는 연간 발전 9,911.9GWh, 수입 2,700.5GWh, 표의 `Total` "
        "12,612.5GWh, 2025-12-02 통합전력망 겨울 피크 1,743.2MW를 보고한다. "
        "연간 에너지와 순간 피크는 서로 다른 물리량이며, 이 국가/지역 집계를 166모선에 "
        "배분할 근거가 없다. 상세 출처와 지역 통계의 지표 정의는 "
        "`demand_source_review.md`를 참조한다. "
        "지역별/변전소별 동시점 수요 측정값이 없어 25.6MW 기준부하를 유지했다. "
        "5x/10x는 수치 민감도용 `SYNTHETIC_ASSUMPTION`이며 몽골 수요 추정치가 아니다. "
        "국가 수요/연간 에너지와 이 166모선 연결요소를 동일시하지 않는다.",
        "",
        "## 시나리오 결과",
        "",
        markdown(comparison.reindex(columns=report_columns)),
        "",
        candidate_diagnostic,
        "",
        "운전제약은 각도/전압, 발전기 P/Q/S, 선로·변압기 열부하, 전력수지, 모선 잔차를 분리해 판정했다. "
        "Q 한도 위반 시 PV→PQ 수동 전환 후 재계산했으며, PyPSA PF 자체의 Q 자동 제한은 가정하지 않았다.",
        "",
        "## 재현성·보호",
        "",
        f"보호 입력·기존 결과 해시 동일: **{preservation['unchanged']}**. "
        "발전소 후보 PF는 Q 제한 후 비수렴했으므로 신규 운전 NetCDF를 저장하지 않았으며, "
        "후속 수렴 산출물도 기존 시나리오와 분리된 경로만 허용한다.",
        "",
        "연료별 시점 가용률·실제 발전량, 개별 발전기 능력곡선, 발전소 bus 연결도, 배전/지역부하 "
        "분포, 운영자 계통경계와 실제 스위칭 상태가 확보되어야 실제 운전조건 검증으로 진전할 수 있다.",
    ]
    (reports / "acpf_validation_report.md").write_text("\n".join(report) + "\n")
    print(comparison.to_string(index=False))
    print(
        json.dumps(
            {
                "candidate_status": status,
                "protected_inputs_unchanged": preservation["unchanged"],
                "candidate_model_sha256": output_model_hash,
                "reports": settings["reports_dir"],
            },
            ensure_ascii=False,
        )
    )
    return {"classification": status, "candidate_model_sha256": output_model_hash,
            "scenario_results": summaries, "candidate_record_ids": [r["record_id"] for r in candidates]}


EXPECTED_FILES = ('acpf_validation_report.md', 'bus_voltage_results.csv', 'generator_dispatch.csv', 'generator_dispatch_attempts.csv', 'line_flow_results.csv', 'load_allocation.csv', 'nodal_balance_validation.csv', 'pf_attempts.json', 'plant_bus_candidates.csv', 'plant_candidate_selection.csv', 'plant_identity_audit.csv', 'power_balance_validation.csv', 'scenario_assumptions.json', 'scenario_attempt_validation.csv', 'scenario_comparison.csv', 'shunt_reactor_attempts.csv', 'shunt_reactor_results.csv', 'source_preservation.json', 'transformer_flow_results.csv')


def read_settings(paths, config_path=None):
    settings = read_config(paths, "configs/mn35k_data_informed_scenario.json", config_path)
    base_config, support_settings = base_settings(paths)
    if settings["demand"]["base_mw"] != 25.6 or settings["snapshot"] != base_config["snapshot"]:
        raise ValueError("Data-informed demand or snapshot differs from the reference")
    if settings["base_config"] != "configs/mn35k_acpf.json" or settings["reactive_support_config"] != "configs/mn35k_reactive_support.json":
        raise ValueError("Data-informed configuration identity changed")
    if settings["output_model"] in {*base_config["protected_models"], base_config["output_model"], support_settings["output_model"]}:
        raise ValueError("Data-informed output identity collides with a protected model")
    return settings, base_config, support_settings


def analyze_for_verification(paths, destination, *, config_path=None):
    settings, base_config, support_settings = read_settings(paths, config_path)
    return analysis_bundle(paths, destination, EXPECTED_FILES,
        lambda output: _generate(paths, output, settings, base_config, support_settings),
        optional_files=(Path(settings["output_model"]).name,))
