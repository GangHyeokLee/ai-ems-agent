from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_ems.network import load_network
from ai_ems.tools.generator_contingency_tools import (
    build_generator_loadflow_parameters,
    generator_snapshot,
    loadflow_balance_snapshot,
)
from ai_ems.tools.security_tools import run_generator_contingency_security
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
        raise ValueError(
            f"Generator is already disconnected: {outage_generator_id}"
        )
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
        raise RuntimeError(
            f"No monitored result returned for {monitored_line_id}."
        )

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
        raise RuntimeError(
            "Post-generator-contingency AC Load Flow did not converge."
        )

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

            predicted_change_mw = (
                up["sensitivity"] - down["sensitivity"]
            ) * float(delta_mw)
            predicted_p1_mw = post_p1_mw + predicted_change_mw
            predicted_abs_reduction_mw = (
                abs(post_p1_mw) - abs(predicted_p1_mw)
            )

            if predicted_abs_reduction_mw <= 0:
                continue

            ranked_pairs.append(
                {
                    "up_generator_id": up["generator_id"],
                    "down_generator_id": down["generator_id"],
                    "delta_mw": float(delta_mw),
                    "net_requested_change_mw": 0.0,
                    "up_sensitivity_mw_per_mw": up["sensitivity"],
                    "down_sensitivity_mw_per_mw": down["sensitivity"],
                    "post_contingency_p1_mw": post_p1_mw,
                    "predicted_active_power_change_mw": predicted_change_mw,
                    "predicted_p1_mw": predicted_p1_mw,
                    "predicted_abs_p1_reduction_mw": predicted_abs_reduction_mw,
                    "up_post_actual_before_mw": up[
                        "post_actual_before_mw"
                    ],
                    "up_target_before_mw": up["target_before_mw"],
                    "up_target_after_mw": up["target_after_mw"],
                    "down_post_actual_before_mw": down[
                        "post_actual_before_mw"
                    ],
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

    candidates = ranked_pairs[:top_n]
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
            parameters.balance_type.name
            if parameters.distributed_slack
            else None
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
