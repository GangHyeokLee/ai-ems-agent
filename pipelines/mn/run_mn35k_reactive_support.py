"""Migrated independent reactive-support scenarios; verification only."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import pypsa
from .mn35k_refinement import export_deterministic
from .run_mn35k_acpf import (assert_passive_preserved, build_scenario, diagnose,
    execute_pf, markdown, select_component, validate_results, write_json)
from .validate_mn35k_poc import sha256
from .synthetic_inputs import read_settings, source_manifest, scenario_bundle, ScenarioFailure


def _build_pv_bus_list(
    source: pypsa.Network, base_config: dict, settings: dict, scenario: dict
) -> tuple[list[str], dict[str, str]]:
    policy = scenario["pv_policy"]
    if policy == "all_110_220kv":
        included_buses, _, candidates = select_component(source, base_config)
        buses = [
            str(bus)
            for bus in included_buses
            if bus != candidates.iloc[0].bus
            and source.buses.at[bus, "v_nom"] in (110, 220)
        ]
        reasons = {
            bus: "Reference configuration all_110_220kv policy; not a claim of actual generation."
            for bus in buses
        }
        return buses, reasons
    if policy != "explicit":
        raise ValueError(f"Unsupported PV policy: {policy}")

    buses = [str(bus) for bus in scenario["pv_buses"]]
    if len(buses) != len(set(buses)):
        raise ValueError(f"Duplicate PV bus in scenario {scenario['id']}")
    if any(bus not in source.buses.index for bus in buses):
        raise ValueError(f"Unknown PV bus in scenario {scenario['id']}")
    invalid_voltage = [
        bus for bus in buses if source.buses.at[bus, "v_nom"] not in (110, 220)
    ]
    if invalid_voltage:
        raise ValueError(
            f"PV buses must be 110/220kV in {scenario['id']}: {invalid_voltage}"
        )
    _, _, candidates = select_component(source, base_config)
    slack = str(candidates.iloc[0].bus)
    if slack in buses:
        raise ValueError(f"Slack bus {slack} cannot also be an explicit PV bus")
    reasons = {}
    selection = settings["generator_selection"]
    topology_buses = set(selection["110kv_topology_ranked_buses"])
    for bus in buses:
        voltage = int(source.buses.at[bus, "v_nom"])
        if voltage == 220:
            reasons[bus] = (
                "Non-Slack 220kV bus selected from the charging-dominant transmission "
                "voltage level; synthetic control only."
            )
        elif bus == selection["110kv_voltage_observation_bus"]:
            reasons[bus] = (
                "Added after the preceding reduced-controller trial identified bus 106 "
                "as a high-voltage observation point."
            )
        elif bus in topology_buses:
            reasons[bus] = (
                "Selected by descending 110kV graph betweenness, then degree; "
                "synthetic control only."
            )
        else:
            reasons[bus] = "Explicitly selected candidate bus; synthetic control only."
    return buses, reasons



def build_reactive_scenario(
    source: pypsa.Network, base_config: dict, settings: dict, scenario: dict
) -> tuple[pypsa.Network, dict[str, str]]:
    """Build a disposable scenario using the existing single-snapshot load/slack helper."""
    if scenario["pv_policy"] == "all_110_220kv":
        source_trial = next(
            item for item in base_config["trials"] if item["pv_policy"] == "all_110_220kv"
        )
    else:
        source_trial = next(
            item for item in base_config["trials"] if item["pv_policy"] == "none"
        )
    trial = copy.deepcopy(source_trial)
    trial["id"] = scenario["id"]
    network, _ = build_scenario(source, base_config, trial)
    pv_buses, reasons = _build_pv_bus_list(source, base_config, settings, scenario)
    slack_bus = str(network.meta["synthetic_acpf"]["slack_bus"])
    if slack_bus != str(settings["generator_selection"]["slack_bus"]):
        raise ValueError(
            f"Unexpected selected Slack {slack_bus}; configured "
            f"{settings['generator_selection']['slack_bus']}"
        )

    generator_limits = base_config["generator"]
    p_each = float(network.loads.p_set.sum()) / (len(pv_buses) + 1)
    network.generators.loc[:, "p_set"] = p_each
    existing_pv = set(
        network.generators.loc[network.generators.control.eq("PV"), "bus"].astype(str)
    )
    if scenario["pv_policy"] == "all_110_220kv":
        if existing_pv != set(pv_buses):
            raise ValueError("Reference PV buses differ from the existing AC PF policy")
    else:
        for bus in pv_buses:
            network.add(
                "Generator",
                "SYNTHETIC_PV_" + bus,
                bus=bus,
                control="PV",
                p_nom=generator_limits["p_nom_mw"],
                p_set=p_each,
                q_set=0.0,
            )
            network.buses.at[bus, "v_mag_pu_set"] = generator_limits[
                "voltage_setpoint_pu"
            ]

    reasons[slack_bus] = (
        "220kV single Slack selected by the existing topology ranking "
        "(degree, betweenness, then bus ID)."
    )
    for reactor in scenario["shunt_reactors"]:
        bus = str(reactor["bus"])
        nominal_q = float(reactor["nominal_q_absorption_mvar"])
        if bus not in network.buses.index:
            raise ValueError(f"Unknown shunt bus {bus} in {scenario['id']}")
        if not math.isfinite(nominal_q) or nominal_q <= 0:
            raise ValueError(f"Invalid reactor rating in {scenario['id']}: {nominal_q}")
        if reactor["id"] in network.shunt_impedances.index:
            raise ValueError(f"Duplicate shunt ID: {reactor['id']}")
        voltage_kv = float(network.buses.at[bus, "v_nom"])
        # PyPSA's shunt base is 1 MVA: q_1pu = b_Siemens * v_nom_kV**2.
        susceptance_siemens = -nominal_q / voltage_kv**2
        network.add(
            "ShuntImpedance",
            reactor["id"],
            bus=bus,
            g=0.0,
            b=susceptance_siemens,
            sign=-1.0,
        )

    network.name = (
        "MN_35k_poc synthetic reactive-support AC PF; not actual operation"
    )
    network.meta["synthetic_reactive_support"] = {
        "scenario": scenario,
        "snapshot_is_synthetic": True,
        "actual_operation_validated": False,
        "generator_reasons": {
            "SYNTHETIC_SLACK_" + slack_bus: reasons[slack_bus],
            **{"SYNTHETIC_PV_" + bus: reason for bus, reason in reasons.items() if bus != slack_bus},
        },
        "shunt_reactors": scenario["shunt_reactors"],
        "q_limit_method": (
            "After an unconstrained PF, a violating PV generator is changed to PQ "
            "with q_set at the violated bound, then PF is rerun. PyPSA PF itself "
            "does not enforce generator Q limits."
            if scenario["enforce_pv_q_limits"]
            else "No Q-limit switching; violations are screened and reported."
        ),
        "selection_reason": scenario["selection_reason"],
    }
    assert_passive_preserved(source, network)
    return network, reasons



def _generator_q_violations(
    network: pypsa.Network, base_config: dict
) -> list[dict]:
    limits = base_config["generator"]
    snapshot = network.snapshots[0]
    q_values = network.generators_t.q.loc[snapshot]
    tolerance = 1e-9
    rows = []
    for generator in network.generators.index:
        q = float(q_values.at[generator])
        if q < limits["q_min_mvar"] - tolerance:
            bound = float(limits["q_min_mvar"])
        elif q > limits["q_max_mvar"] + tolerance:
            bound = float(limits["q_max_mvar"])
        else:
            continue
        rows.append(
            {
                "generator": str(generator),
                "bus": str(network.generators.at[generator, "bus"]),
                "control": str(network.generators.at[generator, "control"]),
                "q_mvar": q,
                "q_limit_mvar": bound,
            }
        )
    return rows



def execute_with_q_limits(
    network: pypsa.Network, base_config: dict, enforce_pv_q_limits: bool
) -> tuple[dict, list[dict], list[dict]]:
    """Run PF and, when requested, manually enforce PV Q bounds by PV-to-PQ conversion."""
    attempts = []
    switches = []
    outcome = {}
    max_rounds = len(network.generators) + 1
    for round_number in range(max_rounds):
        outcome = execute_pf(network, base_config)
        violations = (
            _generator_q_violations(network, base_config)
            if outcome["status"] == "AC_PF_CONVERGED"
            else []
        )
        attempts.append(
            {
                "round": round_number,
                "phase": "unlimited_pf" if round_number == 0 else "pv_to_pq_recalculation",
                "result": outcome,
                "q_limit_violations": violations,
            }
        )
        if outcome["status"] != "AC_PF_CONVERGED" or not enforce_pv_q_limits:
            break
        actionable = [item for item in violations if item["control"] == "PV"]
        if not actionable:
            break
        for item in actionable:
            generator = item["generator"]
            if network.generators.control.eq("PV").groupby(network.generators.bus).sum().at[
                item["bus"]
            ] != 1:
                raise ValueError(
                    f"Cannot safely release Q limit at {item['bus']}: multiple PV "
                    "generators share the bus"
                )
            network.generators.at[generator, "control"] = "PQ"
            network.generators.at[generator, "q_set"] = item["q_limit_mvar"]
            switches.append(
                {
                    **item,
                    "new_control": "PQ",
                    "new_q_set_mvar": item["q_limit_mvar"],
                    "method": "manual PV-to-PQ conversion; rerun AC PF",
                }
            )
        network.meta["synthetic_reactive_support"]["q_limit_switches"] = switches
    else:
        raise RuntimeError("PV-to-PQ Q-limit loop exceeded its generator-count guard")
    remaining = (
        _generator_q_violations(network, base_config)
        if outcome.get("status") == "AC_PF_CONVERGED"
        else []
    )
    return outcome, attempts, switches + [
        {"remaining_violation": item} for item in remaining
    ]



def validate_reactive_results(
    network: pypsa.Network, base_config: dict
) -> tuple[dict[str, pd.DataFrame], dict]:
    """Validate AC results with shunt injections included in global and nodal balances."""
    tables, base_validation = validate_results(network, base_config)
    snapshot = network.snapshots[0]
    shunts = network.shunt_impedances
    if len(shunts):
        shunt_p = network.shunt_impedances_t.p.loc[snapshot].reindex(shunts.index)
        shunt_q = network.shunt_impedances_t.q.loc[snapshot].reindex(shunts.index)
    else:
        shunt_p = pd.Series(dtype=float)
        shunt_q = pd.Series(dtype=float)
    shunt_results = shunts[["bus", "g", "b", "g_pu", "b_pu"]].copy()
    shunt_results.index.name = "shunt"
    shunt_results["p_mw"] = shunt_p
    shunt_results["q_mvar"] = shunt_q
    shunt_results["voltage_kv"] = shunt_results.bus.map(network.buses.v_nom)
    shunt_results["v_mag_pu"] = shunt_results.bus.map(
        network.buses_t.v_mag_pu.loc[snapshot]
    )
    shunt_results["q_absorbed_mvar"] = (-shunt_results.q_mvar).clip(lower=0.0)
    shunt_results["q_at_1pu_mvar"] = shunt_results.b * shunt_results.voltage_kv**2

    generation = network.generators_t.p.loc[snapshot].sum()
    q_generation = network.generators_t.q.loc[snapshot].sum()
    load_p = network.loads_t.p.loc[snapshot].sum()
    load_q = network.loads_t.q.loc[snapshot].sum()
    line = tables["line_flow_results"]
    transformer = tables["transformer_flow_results"]
    line_p = float(line.p_loss_mw.sum())
    transformer_p = float(transformer.p_loss_mw.sum())
    line_q = float(line.q_net_mvar.sum())
    transformer_q = float(transformer.q_net_mvar.sum())
    p_shunt = float(shunt_results.p_mw.sum())
    q_shunt = float(shunt_results.q_mvar.sum())
    balance = pd.DataFrame(
        [
            {
                "quantity": "P",
                "unit": "MW",
                "generator": float(generation),
                "load": float(load_p),
                "shunt_injection": -p_shunt,
                "line_net": line_p,
                "transformer_net": transformer_p,
                "branch_net": line_p + transformer_p,
                "residual": float(generation - load_p - p_shunt - line_p - transformer_p),
            },
            {
                "quantity": "Q",
                "unit": "MVAr",
                "generator": float(q_generation),
                "load": float(load_q),
                "shunt_injection": q_shunt,
                "line_net": line_q,
                "transformer_net": transformer_q,
                "branch_net": line_q + transformer_q,
                "residual": float(q_generation + q_shunt - load_q - line_q - transformer_q),
            },
        ]
    ).set_index("quantity")
    tolerance = base_config["screening"]["balance_tolerance_mva"]
    balance["passed"] = balance.residual.abs() < tolerance
    tables["power_balance_validation"] = balance
    tables["shunt_reactor_results"] = shunt_results

    # Shunt admittances are already in Ybus, so the nodal residual must not add them again.
    nodal = tables["nodal_balance_validation"].copy()
    nodal_max = float(
        np.hypot(nodal.p_residual_mw, nodal.q_residual_mvar).max()
    )
    tables["nodal_balance_validation"] = nodal

    numeric_shunt = shunt_results.select_dtypes(include="number")
    finite = bool(
        base_validation["finite_results"]
        and np.isfinite(numeric_shunt.to_numpy()).all()
        and np.isfinite(nodal[["p_residual_mw", "q_residual_mvar"]].to_numpy()).all()
    )
    limits_passed = bool(
        finite
        and base_validation["controls_verified"]
        and base_validation["loads_verified"]
        and balance.passed.all()
        and nodal_max < tolerance
    )
    violations = dict(base_validation["violations"])
    has_violations = any(violations.values())
    screening = (
        "SYNTHETIC_SCENARIO_HAS_VIOLATIONS"
        if limits_passed and has_violations
        else "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS"
        if limits_passed
        else "MODEL_DATA_REQUIRES_REVIEW"
    )
    validation = {
        **base_validation,
        "finite_results": finite,
        "balance_verified": limits_passed,
        "max_nodal_residual_mva": nodal_max,
        "screening_status": screening,
        "violations": violations,
        "power_balance_verified": bool(balance.passed.all()),
        "nodal_balance_verified": nodal_max < tolerance,
        "operating_constraints_satisfied": bool(limits_passed and not has_violations),
        "model_reliability_status": "MODEL_DATA_REQUIRES_REVIEW",
    }
    return tables, validation



def _scenario_summary(
    scenario: dict,
    network: pypsa.Network,
    outcome: dict,
    attempts: list[dict],
    validation: dict | None,
    tables: dict[str, pd.DataFrame] | None,
) -> dict:
    last = outcome["subnetworks"][0] if outcome.get("subnetworks") else {}
    controls = network.buses.control.value_counts()
    row = {
        "scenario_id": scenario["id"],
        "label": scenario["label"],
        "load_mw": float(network.loads.p_set.sum()),
        "load_mvar": float(network.loads.q_set.sum()),
        "generator_count": len(network.generators),
        "pv_bus_count": int(controls.get("PV", 0)),
        "pq_bus_count": int(controls.get("PQ", 0)),
        "slack_bus_count": int(controls.get("Slack", 0)),
        "shunt_count": len(network.shunt_impedances),
        "shunt_nominal_mvar": float(
            sum(item["nominal_q_absorption_mvar"] for item in scenario["shunt_reactors"])
        ),
        "pf_status": outcome["status"],
        "pf_rounds": len(attempts),
        "n_iter": last.get("n_iter"),
        "error": last.get("error"),
        "converged": outcome["status"] == "AC_PF_CONVERGED",
        "q_limit_switch_count": sum(
            item.get("new_control") == "PQ" for item in network.meta[
                "synthetic_reactive_support"
            ].get("q_limit_switches", [])
        ),
        "operating_screen_status": "NOT_ASSESSED",
        "q_violation_count": None,
        "generator_q_min_mvar": None,
        "generator_q_max_mvar": None,
        "slack_p_mw": None,
        "slack_q_mvar": None,
        "shunt_actual_q_mvar": None,
        "v_min_pu": None,
        "v_max_pu": None,
        "line_max_loading_percent": None,
        "transformer_max_loading_percent": None,
        "p_balance_residual_mw": None,
        "q_balance_residual_mvar": None,
        "nodal_max_residual_mva": None,
    }
    if validation is not None and tables is not None:
        generators = tables["generator_reactive_results"]
        slack_generator = generators.loc[generators.control.eq("Slack")].iloc[0]
        row.update(
            operating_screen_status=(
                "SYNTHETIC_SCENARIO_WITHIN_SCREENING_LIMITS"
                if validation["operating_constraints_satisfied"]
                else validation["screening_status"]
            ),
            q_violation_count=validation["violations"]["generator_q"],
            generator_q_min_mvar=float(generators.q_mvar.min()),
            generator_q_max_mvar=float(generators.q_mvar.max()),
            slack_p_mw=float(slack_generator.p_mw),
            slack_q_mvar=float(slack_generator.q_mvar),
            shunt_actual_q_mvar=float(tables["shunt_reactor_results"].q_mvar.sum()),
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
        )
    return row



def _result_tables(
    scenario_id: str, network: pypsa.Network, base_config: dict, reasons: dict[str, str]
) -> tuple[dict[str, pd.DataFrame], dict]:
    tables, validation = validate_reactive_results(network, base_config)
    generator = tables["generator_dispatch_results"].copy()
    generator.index.name = "generator"
    generator["scenario_id"] = scenario_id
    generator["selection_reason"] = [
        network.meta["synthetic_reactive_support"]["generator_reasons"].get(
            name, reasons.get(str(row.bus), "Synthetic generator; reason not recorded.")
        )
        for name, row in generator.iterrows()
    ]
    generator["q_control_method"] = np.where(
        generator.control.eq("PQ"),
        "PQ at configured q_set; manually converted if PV Q limit was reached",
        generator.control,
    )
    tables["generator_reactive_results"] = generator.reset_index()

    bus = tables["bus_voltage_results"].copy()
    bus.index.name = "bus"
    bus["scenario_id"] = scenario_id
    tables["bus_voltage_results"] = bus.reset_index()
    for component in ["line_flow_results", "transformer_flow_results"]:
        frame = tables[component].copy()
        frame.index.name = "equipment_id"
        frame["component"] = "line" if component == "line_flow_results" else "transformer"
        frame["scenario_id"] = scenario_id
        tables[component] = frame.reset_index()

    balances = tables["power_balance_validation"].reset_index()
    balances.insert(0, "scenario_id", scenario_id)
    balances["nodal_max_residual_mva"] = validation["max_nodal_residual_mva"]
    tables["power_balance_validation"] = balances
    nodal = tables["nodal_balance_validation"].copy()
    nodal.index.name = "bus"
    nodal["scenario_id"] = scenario_id
    tables["nodal_balance_validation"] = nodal.reset_index()

    shunts = tables["shunt_reactor_results"].reset_index()
    shunts["scenario_id"] = scenario_id
    shunt_metadata = {item["id"]: item for item in network.meta[
        "synthetic_reactive_support"
    ]["shunt_reactors"]}
    shunts["nominal_q_absorption_mvar"] = [
        shunt_metadata[name]["nominal_q_absorption_mvar"] for name in shunts.shunt
    ]
    shunts["selection_reason"] = [
        shunt_metadata[name]["selection_reason"] for name in shunts.shunt
    ]
    tables["shunt_reactor_results"] = shunts
    return tables, validation



def _write_report(
    reports: Path,
    settings: dict,
    base_config: dict,
    source: pypsa.Network,
    scenario_rows: pd.DataFrame,
    preserved: bool,
    model_sha256: str | None,
) -> None:
    buses, _, _ = select_component(source, base_config)
    lines = source.lines.loc[
        source.lines.bus0.isin(buses) & source.lines.bus1.isin(buses)
    ]
    charging = lines.groupby("v_nom").agg(
        line_count=("b", "size"),
        length_km=("length", "sum"),
        b_siemens=("b", "sum"),
        flat_q_mvar=("b_pu", "sum"),
    )
    final = scenario_rows.loc[
        scenario_rows.scenario_id.eq("reduced_27_q_limited_with_reactor")
    ].iloc[0]
    selected = settings["scenarios"][-1]
    sections = [
        "# MN_35k_poc 무효전력 보상 첫 실험",
        (
            "## 판정\n\n"
            f"선정 시나리오 수치해석: **{final.pf_status}**, "
            f"운전 스크리닝: **{final.operating_screen_status}**. "
            "이는 지정된 합성 부하와 한도 가정에서의 계산 결과이며 실제 몽골 운전상태가 아니다."
        ),
        (
            "## 기존 결과 분석\n\n"
            "기존 108대는 Slack 25(220kV) 1대와 PV 제어 107대(110kV 90대, 220kV 17대)로 "
            "구성되었다. 모든 PV 전압을 1pu로 강제해 line charging을 발전기들이 무제한 흡수하도록 했고, "
            "이 구성에서 발전기 Q 합은 약 -925.91MVAr, 선로 순 Q는 약 -934.32MVAr였다. "
            "SYNTHETIC_PV_10의 Q=-111.91MVAr로 가정한 -100MVAr 하한을 넘었다. "
            "이 PF는 Q 한도를 자동 적용하지 않으므로 수렴만으로 운전 해로 채택할 수 없다."
        ),
        (
            "220kV 선로의 1pu charging은 "
            f"{charging.loc[220.0, 'flat_q_mvar']:.3f}MVAr이다. 대상 19개 선로의 합산 길이는 "
            f"{charging.loc[220.0, 'length_km']:.3f}km, 물리 susceptance는 "
            f"{charging.loc[220.0, 'b_siemens']:.9f}S이다. 데이터의 220kV 2-bundle 선로 타입은 "
            "50Hz, 12.5nF/km이며, `Q_MVAr=b_Siemens×V_nom_kV²×V_pu²`이므로 "
            "전압 제곱과 길이/도체 정전용량이 큰 charging을 만든다. 같은 기준에서 110kV는 "
            f"{charging.loc[110.0, 'flat_q_mvar']:.3f}MVAr, 35kV는 "
            f"{charging.loc[35.0, 'flat_q_mvar']:.3f}MVAr이다. 기존 source에는 ShuntImpedance가 "
            "0개이고 변압기 shunt g/b도 0으로 가정되었다."
        ),
        (
            "PyPSA 1.2.4 component defaults와 설치 power-flow 코드는 ShuntImpedance의 `b`를 "
            "Siemens로, `b_pu=b×V_nom²`로 변환하며 실제 shunt `q=b_pu×V_pu²`를 기록한다. "
            "작은 2모선 PF 검사에서 음의 b가 음의 q(흡수), 양의 b가 양의 q(공급), "
            "1.1pu에서 Q 크기가 1pu의 1.21배임을 확인했다. 발전기 PV→PQ 재계산도 "
            "`q_set`을 Q 경계에 두고 실제 PF 결과를 확인했으며, PyPSA가 자동으로 Q 제한을 "
            "적용한다고 가정하지 않았다."
        ),
        "## 배치 및 용량",
        (
            "감축 후보는 그래프 중심성/차수 상위 110kV 후보, 관찰 전압이 높았던 106번 모선, "
            "충전량이 큰 220kV 계통의 비-Slack 모선을 사용했다. OSM 공개 발전소 27개는 "
            "전기적 접속이 확인되지 않아 발전기로 등록하지 않았다. 합성 발전기 ID는 모두 "
            "`SYNTHETIC_` 접두사를 사용한다. 발전기 가정은 P=[0,100]MW, Q=[-100,100]MVAr, "
            "S≤150MVA이며 모두 synthetic screening 값이다."
        ),
        (
            f"선정 결과는 {int(final.generator_count)}대 발전기, "
            f"{int(final.pv_bus_count)} PV / {int(final.pq_bus_count)} PQ / "
            f"{int(final.slack_bus_count)} Slack 모선, "
            f"{int(final.shunt_count)}대 합성 리액터, 총 "
            f"{final.shunt_nominal_mvar:.3f}MVAr이다. "
            f"설비 위치/정격: {selected['shunt_reactors']}. "
            "20MVAr 리액터는 220kV Slack 25에 설치한 가정으로, b=-20/220² S이며 "
            "1pu에서 Q=-20MVAr를 흡수한다. PV bus 10을 -100MVAr로 PQ 전환한 뒤, "
            "리액터가 없을 때 Slack Q는 -113.419MVAr였고 20MVAr를 적용한 최종값은 "
            "-93.419MVAr였다. 명목 용량은 220kV 선로 charging 713.980MVAr의 2.8%로, "
            "전체 charging 상쇄를 목표로 하지 않는다. 실제 설치 여부와 가능 용량은 미확인이다."
        ),
        (
            "## 동일 조건\n\n"
            "166모선/156선로/27변압기 최대 연결요소, 원본 선로·변압기 전기정수와 정격, "
            "`2013-01-01 00:00` 단일 snapshot label, 모선별 합성 부하 25.6MW/8.414313MVAr를 "
            "그대로 유지했다. PyPSA PF 설정도 `x_tol=1e-6`, `use_seed=false`, "
            "`distribute_slack=false`로 기존과 동일하다. 2013은 실제 수요 시점이 아니다."
        ),
        "## 시나리오 비교",
        markdown(scenario_rows),
        (
            "선정 시나리오에서 220kV bus 10 PV가 초기 PF에서 Q 하한을 넘었다. "
            "해당 Generator.control을 PQ로 바꾸고 `q_set=-100MVAr`로 고정해 재계산했다. "
            f"최종 발전기 Q 범위 {final.generator_q_min_mvar:.3f}~"
            f"{final.generator_q_max_mvar:.3f}MVAr, Slack P/Q "
            f"{final.slack_p_mw:.6f}MW/{final.slack_q_mvar:.6f}MVAr, "
            f"분로 실제 Q {final.shunt_actual_q_mvar:.3f}MVAr였다. "
            f"최종 Q 제한 위반 {int(final.q_violation_count)}건, 전압 "
            f"{final.v_min_pu:.6f}–{final.v_max_pu:.6f}pu, 최대 선로 부하율 "
            f"{final.line_max_loading_percent:.6f}%, 최대 변압기 부하율 "
            f"{final.transformer_max_loading_percent:.6f}%였다. "
            f"P/Q 수지 잔차는 {final.p_balance_residual_mw:.3e}MW / "
            f"{final.q_balance_residual_mvar:.3e}MVAr, 최대 모선 잔차 "
            f"{final.nodal_max_residual_mva:.3e}MVA다."
        ),
        (
            "## 보존과 한계\n\n"
            f"기존 base.nc, synthetic_pf_base.nc, 기존 power_flow 산출물 및 검증 대상 파일은 "
            f"실행 전후 SHA-256이 동일했다: **{preserved}**. 새 NetCDF: "
            f"`{settings['output_model']}` (SHA-256 `{model_sha256}`). "
            "기존 토폴로지/선로·변압기 정수/정격/단일 snapshot/부하/허용오차를 유지했고 "
            "새 결과만 reactive_support 디렉터리 및 별도 시나리오 파일에 저장했다."
        ),
        (
            "이 실험은 제약을 만족하는 합성 AC PF를 확보했지만 현실 계통의 운영 가능성을 입증하지 않는다. "
            "발전기 위치·P/Q capability curve, 실제 부하와 P/Q 분포, 분로 리액터 설치/정격, "
            "선로 길이·도체·회선 및 변압기 연결·명판자료를 검증한 후 실제 데이터로 다시 수행해야 한다. "
            "특히 25.6MW 합성 부하는 계통 대비 작아 선로 충전의 영향이 크게 나타난다."
        ),
        (
            "## 산출물\n\n"
            "`scenario_comparison.csv`, `generator_reactive_results.csv`, "
            "`shunt_reactor_results.csv`, `bus_voltage_results.csv`, "
            "`power_balance_validation.csv`, `branch_loading_results.csv`, "
            "`pf_attempts.json`, `result_validation.json`, `source_preservation.json`."
        ),
    ]
    (reports / "reactive_support_report.md").write_text("\n\n".join(sections) + "\n")



def _generate(paths, reports, settings, base_config, input_path):
    output_model = reports / "synthetic_reactive_support_base.nc"
    before = source_manifest(paths)
    source = pypsa.Network(input_path)
    previous_attempts = json.loads((paths.source_project / "reports/power_flow/pf_attempts.json").read_text())
    summary_rows = []
    attempt_records = []
    combined_tables: dict[str, list[pd.DataFrame]] = {
        "generator_reactive_results": [],
        "shunt_reactor_results": [],
        "bus_voltage_results": [],
        "power_balance_validation": [],
        "branch_loading_results": [],
        "nodal_balance_validation": [],
    }
    final_model = None
    final_validation = None

    for spec in settings["scenarios"]:
        network, reasons = build_reactive_scenario(source, base_config, settings, spec)
        diagnostics = diagnose(network)
        outcome, passes, q_switches = execute_with_q_limits(
            network, base_config, spec["enforce_pv_q_limits"]
        )
        record = {
            "scenario": spec,
            "generator_inputs": network.generators[
                ["bus", "control", "p_set", "q_set", "p_nom"]
            ].reset_index().to_dict("records"),
            "diagnostics": diagnostics,
            "attempts": passes,
            "q_limit_actions_and_remaining": q_switches,
            "shunts": network.shunt_impedances.to_dict(orient="index"),
            "final_status": outcome["status"],
        }
        attempt_records.append(record)
        assert_passive_preserved(source, network)

        validation = None
        tables = None
        if outcome["status"] == "AC_PF_CONVERGED":
            tables, validation = _result_tables(
                spec["id"], network, base_config, reasons
            )
            for name in combined_tables:
                if name == "branch_loading_results":
                    combined_tables[name].extend(
                        [tables["line_flow_results"], tables["transformer_flow_results"]]
                    )
                else:
                    combined_tables[name].append(tables[name])
            summary = _scenario_summary(
                spec,
                network,
                outcome,
                passes,
                validation,
                tables,
            )
            if spec["id"] == "reduced_27_q_limited_with_reactor":
                if not validation["operating_constraints_satisfied"]:
                    raise ScenarioFailure({"status": "OPERATING_SCREENS_FAILED", "attempts": attempt_records, "validation": validation})
                final_model = network
                final_validation = validation
        else:
            summary = _scenario_summary(
                spec, network, outcome, passes, None, None
            )
        summary_rows.append(summary)

    if final_model is None or final_validation is None:
        raise ScenarioFailure({"status": "NO_ACCEPTABLE_FINAL_SCENARIO", "attempts": attempt_records})

    scenario_frame = pd.DataFrame(summary_rows)
    scenario_frame.to_csv(reports / "scenario_comparison.csv", index=False, float_format="%.12g")
    for name, frames in combined_tables.items():
        if frames:
            pd.concat(frames, ignore_index=True, sort=False).to_csv(
                reports / (name + ".csv"), index=False, float_format="%.12g"
            )
        else:
            pd.DataFrame().to_csv(reports / (name + ".csv"), index=False)
    write_json(reports / "pf_attempts.json", {
        "historical_reference_attempts": previous_attempts,
        "new_attempts": attempt_records,
    })
    write_json(reports / "result_validation.json", final_validation)

    export_deterministic(final_model, output_model, paths=paths, protected_models=base_config["protected_models"])
    model_sha256 = sha256(output_model)
    reloaded = pypsa.Network(output_model)
    assert_passive_preserved(source, reloaded)
    for component, fields in [
        ("buses", ["v_mag_pu", "v_ang"]),
        ("lines", ["p0", "q0", "p1", "q1"]),
        ("transformers", ["p0", "q0", "p1", "q1"]),
        ("generators", ["p", "q"]),
        ("loads", ["p", "q"]),
        ("shunt_impedances", ["q"]),
    ]:
        for field in fields:
            pd.testing.assert_frame_equal(
                getattr(final_model, component + "_t")[field],
                getattr(reloaded, component + "_t")[field],
                check_freq=False,
                check_dtype=False,
                atol=1e-12,
                rtol=1e-12,
            )

    after = source_manifest(paths)
    if before != after:
        raise AssertionError("A protected base model or existing PF artifact changed")
    write_json(
        reports / "source_preservation.json",
        {"before": before, "after": after, "unchanged": before == after},
    )
    _write_report(
        reports,
        settings,
        base_config,
        source,
        scenario_frame,
        before == after,
        model_sha256,
    )
    print(scenario_frame.to_string(index=False))
    print(
        json.dumps(
            {
                "selected_scenario": "reduced_27_q_limited_with_reactor",
                "operating_constraints_satisfied": final_validation[
                    "operating_constraints_satisfied"
                ],
                "new_model": str(output_model.name),
                "new_model_sha256": model_sha256,
                "protected_outputs_unchanged": before == after,
            },
            ensure_ascii=False,
        )
    )
    return {"status": "AC_PF_CONVERGED", "validation": final_validation, "scenarios": summary_rows}


EXPECTED_FILES = ('synthetic_reactive_support_base.nc', 'scenario_comparison.csv', 'pf_attempts.json', 'result_validation.json', 'source_preservation.json', 'reactive_support_report.md', 'generator_reactive_results.csv', 'shunt_reactor_results.csv', 'bus_voltage_results.csv', 'power_balance_validation.csv', 'branch_loading_results.csv', 'nodal_balance_validation.csv')


def regenerate_for_verification(paths, destination, *, config_path=None,
                                scenario_id="reduced_27_q_limited_with_reactor"):
    """Independently regenerate original comparisons and the pinned 45-gen model."""
    if scenario_id != "reduced_27_q_limited_with_reactor":
        raise ValueError("Unknown reactive baseline scenario ID")
    base_config, settings = read_settings(paths, reactive_path=config_path)
    return scenario_bundle(paths, destination, EXPECTED_FILES, lambda reports, input_path:
                           _generate(paths, reports, settings, base_config, input_path), base_config)
