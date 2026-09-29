from __future__ import annotations

from math import hypot
from pathlib import Path
from typing import Any

import pypowsybl as pp

from ai_ems.network import load_network
from ai_ems.tools.generator_contingency_tools import (
    build_generator_loadflow_parameters,
    generator_snapshot,
    loadflow_balance_snapshot,
)
from ai_ems.tools.security_tools import (
    get_limit_unit,
    run_generator_contingency_security,
)
from ai_ems.tools.sensitivity_tools import (
    rank_generator_sensitivities_after_generator_outage,
)


def generate_generator_outage_redispatch_candidates(
    case_path: str | Path,
    outage_generator_id: str,
    monitored_line_id: str,
    slack_mode: str = "distributed",
    balance_type: str | None = None,
    delta_mw: float = 10.0,
    top_n: int = 3,
) -> dict[str, Any]:
    """Generate balanced redispatch candidates after a generator outage.

    The generator outage is evaluated with the requested Load Flow balancing
    assumption. Sensitivities are calculated on the same post-contingency
    condition, and candidate pairs request +delta_mw / -delta_mw from surviving
    generators.

    Feasibility checks use both the configured target and the solved
    post-contingency actual generation against min/max limits. The sensitivity
    ranking is only a local linear prediction; candidates must be revalidated
    with AC Load Flow / Security Analysis before they are treated as effective
    corrective actions.
    """
    if delta_mw <= 0:
        raise ValueError("delta_mw must be greater than zero.")
    if top_n <= 0:
        raise ValueError("top_n must be greater than zero.")

    parameters = build_generator_loadflow_parameters(
        slack_mode=slack_mode,
        balance_type=balance_type,
    )

    base_network = load_network(case_path)
    generators = base_network.get_generators()
    lines = base_network.get_lines()

    if outage_generator_id not in generators.index:
        raise ValueError(f"Unknown outage generator: {outage_generator_id}")
    if not bool(generators.loc[outage_generator_id, "connected"]):
        raise ValueError(f"Generator is already disconnected: {outage_generator_id}")
    if monitored_line_id not in lines.index:
        raise ValueError(f"Unknown monitored line: {monitored_line_id}")

    security_result = run_generator_contingency_security(
        base_network,
        outage_generator_id=outage_generator_id,
        parameters=parameters,
        monitored_line_ids=[monitored_line_id],
    )

    if security_result["post_status"] != "CONVERGED":
        raise RuntimeError(
            "Post-generator-contingency Security Analysis did not converge: "
            f"{security_result['post_status']}"
        )

    monitored_branches = security_result.get("monitored_branches", [])
    if not monitored_branches:
        raise RuntimeError(f"No monitored result returned for {monitored_line_id}.")

    post_p1_mw = float(monitored_branches[0]["p1_mw"])

    post_network = load_network(case_path)
    post_network.update_generators(
        id=outage_generator_id,
        connected=False,
    )

    post_loadflow = loadflow_balance_snapshot(
        post_network,
        parameters,
    )
    if not post_loadflow["converged"]:
        raise RuntimeError("Post-generator-contingency AC Load Flow did not converge.")

    post_generators = generator_snapshot(post_network)

    surviving_count = sum(
        1
        for generator_id, item in post_generators.items()
        if generator_id != outage_generator_id and item["connected"]
    )

    sensitivity_result = rank_generator_sensitivities_after_generator_outage(
        network=base_network,
        outage_generator_id=outage_generator_id,
        monitored_line_id=monitored_line_id,
        loadflow_parameters=parameters,
        top_n=surviving_count,
    )

    sensitivities = {
        item["generator_id"]: float(item["sensitivity"])
        for item in sensitivity_result["candidates"]
    }

    feasible_up: list[dict[str, Any]] = []
    feasible_down: list[dict[str, Any]] = []

    for generator_id, sensitivity in sensitivities.items():
        state = post_generators.get(generator_id)
        if state is None or not state["connected"]:
            continue

        actual_mw = state.get("actual_generation_mw")
        target_mw = state.get("target_p_mw")
        min_mw = state.get("min_p_mw")
        max_mw = state.get("max_p_mw")

        if actual_mw is None or target_mw is None:
            continue

        up_actual_mw = actual_mw + float(delta_mw)
        up_target_mw = target_mw + float(delta_mw)
        if _within_limits(up_actual_mw, min_mw, max_mw) and _within_limits(
            up_target_mw,
            min_mw,
            max_mw,
        ):
            feasible_up.append(
                {
                    "generator_id": generator_id,
                    "bus_id": state.get("bus_id"),
                    "sensitivity": sensitivity,
                    "post_actual_before_mw": actual_mw,
                    "post_actual_screened_after_mw": up_actual_mw,
                    "target_before_mw": target_mw,
                    "target_after_mw": up_target_mw,
                }
            )

        down_actual_mw = actual_mw - float(delta_mw)
        down_target_mw = target_mw - float(delta_mw)
        if _within_limits(down_actual_mw, min_mw, max_mw) and _within_limits(
            down_target_mw,
            min_mw,
            max_mw,
        ):
            feasible_down.append(
                {
                    "generator_id": generator_id,
                    "bus_id": state.get("bus_id"),
                    "sensitivity": sensitivity,
                    "post_actual_before_mw": actual_mw,
                    "post_actual_screened_after_mw": down_actual_mw,
                    "target_before_mw": target_mw,
                    "target_after_mw": down_target_mw,
                }
            )

    ranked_pairs: list[dict[str, Any]] = []

    for up in feasible_up:
        for down in feasible_down:
            if up["generator_id"] == down["generator_id"]:
                continue

            predicted_change_mw = (up["sensitivity"] - down["sensitivity"]) * float(
                delta_mw
            )
            predicted_p1_mw = post_p1_mw + predicted_change_mw
            predicted_abs_reduction_mw = abs(post_p1_mw) - abs(predicted_p1_mw)

            if predicted_abs_reduction_mw <= 0:
                continue

            ranked_pairs.append(
                {
                    "up_generator_id": up["generator_id"],
                    "down_generator_id": down["generator_id"],
                    "up_bus_id": up.get("bus_id"),
                    "down_bus_id": down.get("bus_id"),
                    "delta_mw": float(delta_mw),
                    "net_requested_change_mw": 0.0,
                    "up_sensitivity_mw_per_mw": up["sensitivity"],
                    "down_sensitivity_mw_per_mw": down["sensitivity"],
                    "post_contingency_p1_mw": post_p1_mw,
                    "predicted_active_power_change_mw": predicted_change_mw,
                    "predicted_p1_mw": predicted_p1_mw,
                    "predicted_abs_p1_reduction_mw": predicted_abs_reduction_mw,
                    "up_post_actual_before_mw": up["post_actual_before_mw"],
                    "up_target_before_mw": up["target_before_mw"],
                    "up_target_after_mw": up["target_after_mw"],
                    "down_post_actual_before_mw": down["post_actual_before_mw"],
                    "down_target_before_mw": down["target_before_mw"],
                    "down_target_after_mw": down["target_after_mw"],
                }
            )

    ranked_pairs.sort(
        key=lambda item: (
            -item["predicted_abs_p1_reduction_mw"],
            item["up_generator_id"],
            item["down_generator_id"],
        )
    )

    candidates = _select_diverse_candidates(
        ranked_pairs,
        top_n=top_n,
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank

    return {
        "analysis_type": (
            "Generator Outage Sensitivity-based Redispatch Candidate Generation"
        ),
        "outage_generator_id": outage_generator_id,
        "monitored_line_id": monitored_line_id,
        "slack_mode": slack_mode,
        "balance_type": (
            parameters.balance_type.name if parameters.distributed_slack else None
        ),
        "delta_mw": float(delta_mw),
        "post_contingency_p1_mw": post_p1_mw,
        "candidate_count": len(candidates),
        "candidate_basis": (
            "Predicted reduction in absolute post-generator-contingency "
            "branch active-power flow"
        ),
        "prediction_quantity": "ACTIVE_POWER",
        "prediction_unit": "MW",
        "balancing_interpretation": (
            "Load-flow balancing assumption; redispatch candidates are "
            "separate explicit corrective-action requests."
        ),
        "candidates": candidates,
    }


def validate_generator_outage_balanced_redispatch(
    case_path: str | Path,
    outage_generator_id: str,
    monitored_line_id: str,
    up_generator_id: str,
    down_generator_id: str,
    delta_mw: float,
    slack_mode: str = "distributed",
    balance_type: str | None = None,
) -> dict[str, Any]:
    """Apply and physically revalidate a balanced redispatch after a generator outage."""
    if delta_mw <= 0:
        raise ValueError("delta_mw must be greater than zero.")
    if up_generator_id == down_generator_id:
        raise ValueError("up_generator_id and down_generator_id must be different.")
    if outage_generator_id in {up_generator_id, down_generator_id}:
        raise ValueError("The tripped generator cannot be used for redispatch.")

    parameters = build_generator_loadflow_parameters(
        slack_mode=slack_mode,
        balance_type=balance_type,
    )

    base_network = load_network(case_path)
    generators = base_network.get_generators()
    lines = base_network.get_lines()

    for generator_id, label in [
        (outage_generator_id, "Outage generator"),
        (up_generator_id, "Up generator"),
        (down_generator_id, "Down generator"),
    ]:
        if generator_id not in generators.index:
            raise ValueError(f"{label} not found: {generator_id}")

    if not bool(generators.loc[outage_generator_id, "connected"]):
        raise ValueError(f"Generator is already disconnected: {outage_generator_id}")
    if not bool(generators.loc[up_generator_id, "connected"]):
        raise ValueError(f"Up generator is disconnected: {up_generator_id}")
    if not bool(generators.loc[down_generator_id, "connected"]):
        raise ValueError(f"Down generator is disconnected: {down_generator_id}")
    if monitored_line_id not in lines.index:
        raise ValueError(f"Unknown monitored line: {monitored_line_id}")

    security_result = run_generator_contingency_security(
        base_network,
        outage_generator_id=outage_generator_id,
        parameters=parameters,
        monitored_line_ids=[monitored_line_id],
    )

    if security_result["post_status"] != "CONVERGED":
        raise RuntimeError(
            "Post-generator-contingency Security Analysis did not converge: "
            f"{security_result['post_status']}"
        )

    monitored_branches = security_result.get("monitored_branches", [])
    if not monitored_branches:
        raise RuntimeError(f"No monitored result returned for {monitored_line_id}.")

    before_mva = float(monitored_branches[0]["apparent_power_mva"])
    limit_mva = _find_apparent_power_limit(
        security_result,
        monitored_line_id,
    )

    control_network = load_network(case_path)
    control_network.update_generators(
        id=outage_generator_id,
        connected=False,
    )

    post_loadflow = loadflow_balance_snapshot(control_network, parameters)
    if not post_loadflow["converged"]:
        raise RuntimeError("Post-generator-contingency AC Load Flow did not converge.")

    before_generators = generator_snapshot(control_network)
    up_before = before_generators[up_generator_id]
    down_before = before_generators[down_generator_id]

    up_target_before = float(up_before["target_p_mw"])
    down_target_before = float(down_before["target_p_mw"])
    up_target_after = up_target_before + float(delta_mw)
    down_target_after = down_target_before - float(delta_mw)

    if not _within_limits(
        up_target_after,
        up_before["min_p_mw"],
        up_before["max_p_mw"],
    ):
        raise ValueError(
            f"Requested target for {up_generator_id} is outside generator limits."
        )
    if not _within_limits(
        down_target_after,
        down_before["min_p_mw"],
        down_before["max_p_mw"],
    ):
        raise ValueError(
            f"Requested target for {down_generator_id} is outside generator limits."
        )

    up_actual_before = up_before["actual_generation_mw"]
    down_actual_before = down_before["actual_generation_mw"]
    if up_actual_before is not None and not _within_limits(
        up_actual_before + float(delta_mw),
        up_before["min_p_mw"],
        up_before["max_p_mw"],
    ):
        raise ValueError(
            f"Requested increase would exceed solved post-contingency limits for {up_generator_id}."
        )
    if down_actual_before is not None and not _within_limits(
        down_actual_before - float(delta_mw),
        down_before["min_p_mw"],
        down_before["max_p_mw"],
    ):
        raise ValueError(
            f"Requested decrease would exceed solved post-contingency limits for {down_generator_id}."
        )

    control_network.update_generators(
        id=[up_generator_id, down_generator_id],
        target_p=[up_target_after, down_target_after],
    )

    after_loadflow = loadflow_balance_snapshot(control_network, parameters)

    response: dict[str, Any] = {
        "analysis_type": "Generator Outage Balanced Redispatch Validation",
        "outage_generator_id": outage_generator_id,
        "monitored_line_id": monitored_line_id,
        "slack_mode": slack_mode,
        "balance_type": (
            parameters.balance_type.name if parameters.distributed_slack else None
        ),
        "redispatch": {
            "up_generator_id": up_generator_id,
            "down_generator_id": down_generator_id,
            "delta_mw": float(delta_mw),
            "net_requested_change_mw": 0.0,
            "up_target_before_mw": up_target_before,
            "up_target_after_mw": up_target_after,
            "down_target_before_mw": down_target_before,
            "down_target_after_mw": down_target_after,
            "up_actual_before_mw": up_actual_before,
            "down_actual_before_mw": down_actual_before,
        },
        "post_contingency": {
            "converged": True,
            "apparent_power_mva": before_mva,
        },
        "after_redispatch": {
            "converged": after_loadflow["converged"],
            "apparent_power_mva": None,
            "up_actual_after_mw": None,
            "down_actual_after_mw": None,
        },
        "limit_mva": limit_mva,
        "apparent_power_change_mva": None,
        "improvement_mva": None,
        "improved": None,
        "loading_before_percent": (
            before_mva / limit_mva * 100.0
            if limit_mva is not None and limit_mva > 0
            else None
        ),
        "loading_after_percent": None,
        "violation_remaining": None,
        "whole_network_validation": None,
    }

    if not after_loadflow["converged"]:
        return response

    line = control_network.get_lines().loc[monitored_line_id]
    after_mva = max(
        hypot(float(line["p1"]), float(line["q1"])),
        hypot(float(line["p2"]), float(line["q2"])),
    )

    after_generators = generator_snapshot(control_network)
    up_actual_after = after_generators[up_generator_id]["actual_generation_mw"]
    down_actual_after = after_generators[down_generator_id]["actual_generation_mw"]

    change_mva = after_mva - before_mva
    response["after_redispatch"]["apparent_power_mva"] = after_mva
    response["after_redispatch"]["up_actual_after_mw"] = up_actual_after
    response["after_redispatch"]["down_actual_after_mw"] = down_actual_after
    response["apparent_power_change_mva"] = change_mva
    response["improvement_mva"] = before_mva - after_mva
    response["improved"] = after_mva < before_mva

    if limit_mva is not None and limit_mva > 0:
        response["loading_after_percent"] = after_mva / limit_mva * 100.0
        response["violation_remaining"] = after_mva > limit_mva

    response["whole_network_validation"] = (
        _validate_whole_network_generator_outage_redispatch(
            case_path=case_path,
            outage_generator_id=outage_generator_id,
            up_generator_id=up_generator_id,
            down_generator_id=down_generator_id,
            delta_mw=delta_mw,
            parameters=parameters,
        )
    )

    return response


def _validate_whole_network_generator_outage_redispatch(
    case_path: str | Path,
    outage_generator_id: str,
    up_generator_id: str,
    down_generator_id: str,
    delta_mw: float,
    parameters: pp.loadflow.Parameters,
) -> dict[str, Any]:
    network = load_network(case_path)

    contingency_id = f"GEN_OUT_{outage_generator_id}"
    strategy_id = "GENERATOR_OUTAGE_REDISPATCH_VALIDATION"
    up_action_id = "UP_GENERATOR"
    down_action_id = "DOWN_GENERATOR"

    analysis = pp.security.create_analysis()
    analysis.add_single_element_contingency(
        outage_generator_id,
        contingency_id,
    )
    analysis.add_generator_active_power_action(
        up_action_id,
        up_generator_id,
        True,
        float(delta_mw),
    )
    analysis.add_generator_active_power_action(
        down_action_id,
        down_generator_id,
        True,
        -float(delta_mw),
    )
    analysis.add_operator_strategy(
        strategy_id,
        contingency_id,
        [up_action_id, down_action_id],
    )

    result = analysis.run_ac(
        network,
        parameters=parameters,
    )

    post = result.find_post_contingency_result(contingency_id)
    strategy = result.find_operator_strategy_results(strategy_id)

    before = _summarize_limit_violations(post.limit_violations)
    after = _summarize_limit_violations(strategy.limit_violations)

    before_map = {_violation_key(item): item for item in before}
    after_map = {_violation_key(item): item for item in after}

    before_keys = set(before_map)
    after_keys = set(after_map)

    new_keys = after_keys - before_keys
    resolved_keys = before_keys - after_keys
    remaining_keys = before_keys & after_keys

    return {
        "analysis_type": "Generator Outage Whole-network Security Validation",
        "post_contingency_status": post.status.name,
        "operator_strategy_status": strategy.status.name,
        "violated_equipment_count_before": len(before),
        "violated_equipment_count_after": len(after),
        "new_violation_detected": bool(new_keys),
        "new_violations": [after_map[key] for key in sorted(new_keys)],
        "resolved_violations": [before_map[key] for key in sorted(resolved_keys)],
        "remaining_violations": [after_map[key] for key in sorted(remaining_keys)],
    }


def _find_apparent_power_limit(
    security_result: dict[str, Any],
    monitored_line_id: str,
) -> float | None:
    for item in security_result.get("violated_equipment", []):
        if (
            item.get("equipment_id") == monitored_line_id
            and item.get("limit_type") == "APPARENT_POWER"
            and item.get("limit") is not None
        ):
            return float(item["limit"])
    return None


def _summarize_limit_violations(violations) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[Any]] = {}

    for violation in violations:
        limit_type = (
            violation.limit_type.name
            if hasattr(violation.limit_type, "name")
            else str(violation.limit_type)
        )
        key = (
            str(violation.subject_id),
            limit_type,
            str(violation.limit_name),
        )
        grouped.setdefault(key, []).append(violation)

    output: list[dict[str, Any]] = []

    for (equipment_id, limit_type, limit_name), items in grouped.items():
        values = [float(item.value) for item in items]
        limits = [float(item.limit) for item in items]

        summary: dict[str, Any] = {
            "equipment_id": equipment_id,
            "limit_type": limit_type,
            "limit_name": limit_name,
            "unit": get_limit_unit(limit_type),
            "record_count": len(items),
        }

        if values and limits:
            if limit_type == "LOW_VOLTAGE":
                value = min(values)
                limit = max(limits)
                violation_amount = limit - value
            else:
                value = max(values)
                limit = min(limits)
                violation_amount = value - limit

            summary["value"] = value
            summary["limit"] = limit
            summary["violation_amount"] = violation_amount

            if limit_type in {
                "CURRENT",
                "ACTIVE_POWER",
                "APPARENT_POWER",
            }:
                summary["loading_percent"] = (
                    value / limit * 100.0 if limit != 0 else None
                )

        output.append(summary)

    return output


def _violation_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("equipment_id", "")),
        str(item.get("limit_type", "")),
        str(item.get("limit_name", "")),
    )


def _within_limits(
    value_mw: float,
    min_mw: float | None,
    max_mw: float | None,
) -> bool:
    if min_mw is not None and value_mw < min_mw:
        return False
    if max_mw is not None and value_mw > max_mw:
        return False
    return True


def _select_diverse_candidates(
    ranked_pairs: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_effects: set[tuple[Any, ...]] = set()

    for candidate in ranked_pairs:
        up_bus_id = candidate.get("up_bus_id")
        down_bus_id = candidate.get("down_bus_id")

        if up_bus_id is not None and down_bus_id is not None:
            effect_key = (
                "bus_pair",
                up_bus_id,
                down_bus_id,
            )
        else:
            effect_key = (
                "sensitivity_pair",
                round(
                    candidate["up_sensitivity_mw_per_mw"],
                    6,
                ),
                round(
                    candidate["down_sensitivity_mw_per_mw"],
                    6,
                ),
            )

        if effect_key in seen_effects:
            continue

        seen_effects.add(effect_key)
        selected.append(candidate)

        if len(selected) >= top_n:
            break

    return selected
