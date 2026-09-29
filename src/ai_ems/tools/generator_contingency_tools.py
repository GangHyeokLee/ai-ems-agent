from __future__ import annotations

from pathlib import Path
from typing import Any

import pypowsybl as pp

from ai_ems.network import load_network
from ai_ems.tools.security_tools import (
    run_generator_contingency_security,
)

SUPPORTED_SLACK_MODES = {
    "single",
    "distributed",
}


def build_generator_loadflow_parameters(
    slack_mode: str,
    balance_type: str | None = None,
) -> pp.loadflow.Parameters:
    """Build AC Load Flow parameters for generator contingency analysis."""

    if slack_mode not in SUPPORTED_SLACK_MODES:
        raise ValueError(
            f"Unsupported slack_mode: {slack_mode}. "
            "Expected 'single' or 'distributed'."
        )

    if slack_mode == "single":
        if balance_type is not None:
            raise ValueError(
                "balance_type is only valid " "when slack_mode='distributed'."
            )

        return pp.loadflow.Parameters(
            distributed_slack=False,
        )

    effective_balance_type = balance_type or "PROPORTIONAL_TO_GENERATION_P_MAX"

    try:
        pypowsybl_balance_type = getattr(
            pp.loadflow.BalanceType,
            effective_balance_type,
        )
    except AttributeError as exc:
        raise ValueError(
            f"Unsupported balance_type: " f"{effective_balance_type}"
        ) from exc

    return pp.loadflow.Parameters(
        distributed_slack=True,
        balance_type=pypowsybl_balance_type,
    )


def generator_snapshot(
    network,
) -> dict[str, dict[str, Any]]:
    """Capture generator operating state after a Load Flow."""

    generators = network.get_generators()

    output: dict[str, dict[str, Any]] = {}

    for generator_id, row in generators.iterrows():
        actual_generation_mw = -float(row["p"]) if row["p"] == row["p"] else None

        min_p_mw = float(row["min_p"]) if row["min_p"] == row["min_p"] else None

        max_p_mw = float(row["max_p"]) if row["max_p"] == row["max_p"] else None

        target_p_mw = (
            float(row["target_p"]) if row["target_p"] == row["target_p"] else None
        )

        headroom_mw = None

        if max_p_mw is not None and actual_generation_mw is not None:
            headroom_mw = max_p_mw - actual_generation_mw

        bus_id = None

        if "bus_id" in row.index and row["bus_id"] == row["bus_id"]:
            bus_id = str(row["bus_id"])

        output[str(generator_id)] = {
            "generator_id": str(generator_id),
            "connected": bool(row["connected"]),
            "bus_id": bus_id,
            "target_p_mw": target_p_mw,
            "actual_generation_mw": actual_generation_mw,
            "min_p_mw": min_p_mw,
            "max_p_mw": max_p_mw,
            "headroom_mw": headroom_mw,
        }

    return output


def build_generator_changes(
    base_generators: dict[str, dict[str, Any]],
    post_generators: dict[str, dict[str, Any]],
    outage_generator_id: str,
) -> list[dict[str, Any]]:
    """Compare surviving generator outputs before and after the outage."""

    changes: list[dict[str, Any]] = []

    for generator_id, before in base_generators.items():
        if generator_id == outage_generator_id:
            continue

        after = post_generators.get(generator_id)

        if after is None:
            continue

        if not before["connected"]:
            continue

        if not after["connected"]:
            continue

        before_generation = before["actual_generation_mw"]
        after_generation = after["actual_generation_mw"]

        delta_generation_mw = None

        if before_generation is not None and after_generation is not None:
            delta_generation_mw = after_generation - before_generation

        changes.append(
            {
                "generator_id": generator_id,
                "bus_id": before["bus_id"],
                "generation_before_mw": before_generation,
                "generation_after_mw": after_generation,
                "delta_generation_mw": delta_generation_mw,
                "min_p_mw": before["min_p_mw"],
                "max_p_mw": before["max_p_mw"],
                "headroom_before_mw": before["headroom_mw"],
                "headroom_after_mw": after["headroom_mw"],
            }
        )

    return sorted(
        changes,
        key=lambda item: abs(
            item["delta_generation_mw"]
            if item["delta_generation_mw"] is not None
            else 0.0
        ),
        reverse=True,
    )


def loadflow_balance_snapshot(
    network,
    parameters: pp.loadflow.Parameters,
) -> dict[str, Any]:
    """
    Run AC Load Flow and capture balancing-related results.

    This function captures:
    - convergence
    - reference bus
    - slack bus mismatch
    - distributed active power
    """

    results = pp.loadflow.run_ac(
        network,
        parameters=parameters,
    )

    converged = bool(results) and all(
        component.status.name == "CONVERGED" for component in results
    )

    components: list[dict[str, Any]] = []

    total_distributed_active_power_mw = 0.0
    total_active_power_mismatch_mw = 0.0

    for component in results:
        distributed_active_power_mw = float(component.distributed_active_power)

        total_distributed_active_power_mw += distributed_active_power_mw

        slack_buses: list[dict[str, Any]] = []

        for slack in component.slack_bus_results:
            active_power_mismatch_mw = float(slack.active_power_mismatch)

            total_active_power_mismatch_mw += active_power_mismatch_mw

            slack_buses.append(
                {
                    "bus_id": str(slack.id),
                    "active_power_mismatch_mw": active_power_mismatch_mw,
                }
            )

        components.append(
            {
                "status": component.status.name,
                "reference_bus_id": str(component.reference_bus_id),
                "distributed_active_power_mw": distributed_active_power_mw,
                "slack_buses": slack_buses,
            }
        )

    return {
        "converged": converged,
        "components": components,
        "distributed_active_power_mw": total_distributed_active_power_mw,
        "active_power_mismatch_mw": total_active_power_mismatch_mw,
    }


def power_balance_snapshot(
    network,
) -> dict[str, float]:
    """
    Capture generation, load, and line active-power loss.

    Generator p follows the PyPowSyBl terminal convention,
    therefore -p is treated as generated active power.
    """

    generators = network.get_generators()
    loads = network.get_loads()
    lines = network.get_lines()

    connected_generators = generators[generators["connected"]]

    connected_loads = loads[loads["connected"]]

    total_generation_mw = float((-connected_generators["p"]).sum())

    total_load_mw = float(connected_loads["p"].sum())

    line_active_power_loss_mw = float((lines["p1"] + lines["p2"]).sum())

    return {
        "observed_generation_mw": total_generation_mw,
        "load_mw": total_load_mw,
        "line_active_power_loss_mw": line_active_power_loss_mw,
    }


def analyze_generator_contingency(
    case_path: str | Path,
    generator_id: str,
    slack_mode: str = "single",
    balance_type: str | None = None,
    top_n_overloads: int = 10,
) -> dict[str, Any]:
    """
    Analyze a single generator outage using AC Load Flow
    and Security Analysis.

    Distributed slack is a Load Flow balancing assumption.
    It must not be interpreted as operator corrective redispatch.
    """

    if top_n_overloads <= 0:
        raise ValueError("top_n_overloads must be greater than 0.")

    parameters = build_generator_loadflow_parameters(
        slack_mode=slack_mode,
        balance_type=balance_type,
    )

    #
    # Base case
    #

    base_network = load_network(case_path)

    generators = base_network.get_generators()

    if generator_id not in generators.index:
        raise ValueError(f"Unknown generator: {generator_id}")

    if not bool(
        generators.loc[
            generator_id,
            "connected",
        ]
    ):
        raise ValueError("Generator is already disconnected: " f"{generator_id}")

    base_loadflow = loadflow_balance_snapshot(
        base_network,
        parameters,
    )

    if not base_loadflow["converged"]:
        raise RuntimeError("Base-case AC Load Flow did not converge.")

    base_generators = generator_snapshot(
        base_network,
    )

    base_power_balance = power_balance_snapshot(
        base_network,
    )

    base_balance_based_loss_mw = (
        base_power_balance["observed_generation_mw"]
        + base_loadflow["active_power_mismatch_mw"]
        - base_power_balance["load_mw"]
    )

    base_result = {
        "loadflow": base_loadflow,
        "power_balance": {
            **base_power_balance,
            "balance_based_loss_mw": base_balance_based_loss_mw,
        },
    }

    #
    # Post-contingency AC Load Flow
    #

    post_network = load_network(case_path)

    post_network.update_generators(
        id=generator_id,
        connected=False,
    )

    post_loadflow = loadflow_balance_snapshot(
        post_network,
        parameters,
    )

    #
    # Security Analysis
    #
    # Use a fresh base network because Security Analysis
    # applies the contingency internally.
    #

    security_network = load_network(case_path)

    security_result = run_generator_contingency_security(
        security_network,
        outage_generator_id=generator_id,
        parameters=parameters,
    )

    line_ids = {str(line_id) for line_id in security_network.get_lines().index}

    major_overloads = build_major_overloads(
        security_result,
        line_ids=line_ids,
        top_n=top_n_overloads,
    )

    common_result = {
        "analysis_type": "Generator Contingency Detail Analysis",
        "generator_id": generator_id,
        "contingency_id": security_result["contingency_id"],
        "slack_mode": slack_mode,
        "balance_type": (
            parameters.balance_type.name if parameters.distributed_slack else None
        ),
        "balancing_interpretation": (
            "Load-flow balancing " "assumption; not operator corrective redispatch."
        ),
        "base_converged": True,
        "post_contingency_converged": post_loadflow["converged"],
        "outage_generator": base_generators[generator_id],
        "base": base_result,
        "major_overloads": major_overloads,
        "security_analysis": security_result,
    }

    #
    # Post-contingency non-convergence
    #

    if not post_loadflow["converged"]:
        return {
            **common_result,
            "post_contingency": {
                "loadflow": post_loadflow,
                "power_balance": None,
            },
            "loss_change": {
                "balance_based_loss_change_mw": None,
                "line_active_power_loss_change_mw": None,
            },
            "generator_changes": [],
        }

    #
    # Post-contingency converged result
    #

    post_generators = generator_snapshot(
        post_network,
    )

    post_power_balance = power_balance_snapshot(
        post_network,
    )

    post_balance_based_loss_mw = (
        post_power_balance["observed_generation_mw"]
        + post_loadflow["active_power_mismatch_mw"]
        - post_power_balance["load_mw"]
    )

    generator_changes = build_generator_changes(
        base_generators=base_generators,
        post_generators=post_generators,
        outage_generator_id=generator_id,
    )

    loss_change_mw = post_balance_based_loss_mw - base_balance_based_loss_mw

    line_loss_change_mw = (
        post_power_balance["line_active_power_loss_mw"]
        - base_power_balance["line_active_power_loss_mw"]
    )

    return {
        **common_result,
        "post_contingency": {
            "loadflow": post_loadflow,
            "power_balance": {
                **post_power_balance,
                "balance_based_loss_mw": post_balance_based_loss_mw,
            },
        },
        "loss_change": {
            "balance_based_loss_change_mw": loss_change_mw,
            "line_active_power_loss_change_mw": line_loss_change_mw,
        },
        "generator_changes": generator_changes,
    }


def build_major_overloads(
    security_result: dict[str, Any],
    line_ids: set[str],
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Select the most severe post-contingency line overloads."""

    if top_n <= 0:
        raise ValueError("top_n must be greater than 0.")

    overloads = []

    for item in security_result.get(
        "violated_equipment",
        [],
    ):
        equipment_id = str(item.get("equipment_id", ""))

        if equipment_id not in line_ids:
            continue

        loading_percent = item.get("loading_percent")

        if loading_percent is None:
            continue

        if float(loading_percent) <= 100.0:
            continue

        overloads.append(
            {
                "equipment_id": equipment_id,
                "limit_type": item.get("limit_type"),
                "limit_name": item.get("limit_name"),
                "unit": item.get("unit"),
                "limit": item.get("limit"),
                "value": item.get("value"),
                "loading_percent": float(loading_percent),
                "violation_amount": item.get("violation_amount"),
            }
        )

    overloads.sort(
        key=lambda item: item["loading_percent"],
        reverse=True,
    )

    return overloads[:top_n]
