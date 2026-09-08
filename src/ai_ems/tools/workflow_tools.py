from ai_ems.network import load_network
from ai_ems.tools.control_tools import (
    generate_redispatch_candidates,
    validate_balanced_redispatch,
)
from ai_ems.tools.security_tools import (
    run_line_contingency,
    select_most_severe_violated_line,
)


def _ac_sort_key(item):
    validation = item["ac_validation"]
    whole = validation["whole_network_validation"]

    converged = (
        validation["after_redispatch"]["converged"]
        and whole["operator_strategy_status"] == "CONVERGED"
    )

    new_violation = whole["new_violation_detected"]
    violation_count = whole["violated_equipment_count_after"]

    improvement = validation["improvement_mva"]
    improvement_key = (
        -round(improvement, 6) if improvement is not None else float("inf")
    )

    return (
        0 if converged else 1,
        0 if not new_violation else 1,
        violation_count,
        improvement_key,
        item["rank"],
    )


def analyze_contingency_response(
    case_path,
    outage_line_id: str,
    monitored_line_id: str | None = None,
    delta_mw: float = 10.0,
    top_n: int = 3,
) -> dict:
    network = load_network(case_path)

    target_selection = "user_specified"

    if monitored_line_id is None:
        security_result = run_line_contingency(
            network,
            outage_line_id=outage_line_id,
        )

        selected = select_most_severe_violated_line(
            network,
            security_result,
        )

        if selected is None:
            raise ValueError(
                "No overloaded transmission line found after the contingency."
            )

        monitored_line_id = selected["equipment_id"]
        target_selection = "most_severe_violation"

    candidate_result = generate_redispatch_candidates(
        network,
        outage_line_id=outage_line_id,
        monitored_line_id=monitored_line_id,
        delta_mw=delta_mw,
        top_n=top_n,
    )

    validated_candidates = []

    for candidate in candidate_result["candidates"]:
        validation = validate_balanced_redispatch(
            case_path=case_path,
            outage_line_id=outage_line_id,
            monitored_line_id=monitored_line_id,
            up_generator_id=candidate["up_generator_id"],
            down_generator_id=candidate["down_generator_id"],
            delta_mw=candidate["delta_mw"],
        )

        validated_candidates.append(
            {
                "rank": candidate["rank"],
                "redispatch": {
                    "up_generator_id": candidate["up_generator_id"],
                    "down_generator_id": candidate["down_generator_id"],
                    "delta_mw": candidate["delta_mw"],
                },
                "prediction": {
                    "active_power_change_mw": candidate[
                        "predicted_active_power_change_mw"
                    ],
                    "abs_p1_reduction_mw": candidate["predicted_abs_p1_reduction_mw"],
                },
                "ac_validation": validation,
                "target_selection": target_selection,
            }
        )

    validated_candidates.sort(key=_ac_sort_key)

    for rank, item in enumerate(validated_candidates, start=1):
        item["ac_validation_rank"] = rank

    return {
        "analysis_type": "Contingency Response Analysis",
        "outage_line_id": outage_line_id,
        "monitored_line_id": monitored_line_id,
        "target_selection": target_selection,
        "delta_mw": delta_mw,
        "candidate_count": len(validated_candidates),
        "candidates": validated_candidates,
        "best_tested_candidate": (
            validated_candidates[0] if validated_candidates else None
        ),
    }
