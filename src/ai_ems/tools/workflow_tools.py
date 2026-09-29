from ai_ems.network import load_network
from ai_ems.tools.control_tools import (
    generate_redispatch_candidates,
    validate_balanced_redispatch,
)
from ai_ems.tools.generator_contingency_tools import analyze_generator_contingency
from ai_ems.tools.generator_control_tools import (
    generate_generator_outage_redispatch_candidates,
    validate_generator_outage_balanced_redispatch,
)
from ai_ems.tools.security_tools import (
    run_line_contingency,
    select_most_severe_violated_line,
)


def _ac_sort_key(item):
    validation = item["ac_validation"]
    whole = validation.get("whole_network_validation")

    loadflow_converged = validation["after_redispatch"]["converged"]
    security_converged = (
        whole is not None
        and whole.get("operator_strategy_status") == "CONVERGED"
    )
    converged = loadflow_converged and security_converged

    new_violation = (
        whole.get("new_violation_detected", True)
        if whole is not None
        else True
    )
    violation_count = (
        whole.get("violated_equipment_count_after", float("inf"))
        if whole is not None
        else float("inf")
    )

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

    security_result = run_line_contingency(
        network,
        outage_line_id=outage_line_id,
    )

    initial_security = {
        "pre_status": security_result["pre_status"],
        "post_status": security_result["post_status"],
        "pre_violated_equipment_count": security_result["pre_violated_equipment_count"],
        "post_violated_equipment_count": security_result["violated_equipment_count"],
        "violation_comparison": security_result["violation_comparison"],
    }

    if security_result["post_status"] != "CONVERGED":
        return {
            "analysis_type": "Contingency Response Analysis",
            "analysis_status": "SECURITY_NOT_CONVERGED",
            "outage_line_id": outage_line_id,
            "monitored_line_id": None,
            "target_selection": "not_available",
            "delta_mw": delta_mw,
            "candidate_count": 0,
            "candidates": [],
            "best_tested_candidate": None,
            "initial_security": initial_security,
        }

    target_selection = "user_specified"

    if monitored_line_id is None:
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
        "analysis_status": "COMPLETED",
        "outage_line_id": outage_line_id,
        "monitored_line_id": monitored_line_id,
        "target_selection": target_selection,
        "delta_mw": delta_mw,
        "candidate_count": len(validated_candidates),
        "candidates": validated_candidates,
        "best_tested_candidate": (
            validated_candidates[0] if validated_candidates else None
        ),
        "initial_security": initial_security,
    }


def analyze_generator_outage_response(
    case_path,
    outage_generator_id: str,
    monitored_line_id: str | None = None,
    slack_mode: str = "distributed",
    balance_type: str | None = None,
    delta_mw: float = 10.0,
    top_n: int = 3,
) -> dict:
    """Analyze corrective redispatch candidates after a generator outage.

    Flow:
    generator outage -> Security Analysis -> overloaded-line selection ->
    post-contingency sensitivity -> balanced redispatch candidates ->
    AC Load Flow / whole-network Security revalidation.

    The returned best_tested_candidate is only the best among tested candidates;
    it is not an OPF/SCED optimum.
    """
    if delta_mw <= 0:
        raise ValueError("delta_mw must be greater than zero.")
    if top_n <= 0:
        raise ValueError("top_n must be greater than zero.")

    detail = analyze_generator_contingency(
        case_path=case_path,
        generator_id=outage_generator_id,
        slack_mode=slack_mode,
        balance_type=balance_type,
        top_n_overloads=max(top_n, 10),
    )

    security = detail["security_analysis"]
    initial_security = {
        "pre_status": security.get("pre_status"),
        "post_status": security.get("post_status"),
        "pre_violated_equipment_count": security.get(
            "pre_violated_equipment_count",
            0,
        ),
        "post_violated_equipment_count": security.get(
            "violated_equipment_count",
            0,
        ),
        "violation_comparison": security.get("violation_comparison", {}),
    }

    common = {
        "analysis_type": "Generator Outage Response Analysis",
        "outage_generator_id": outage_generator_id,
        "slack_mode": slack_mode,
        "balance_type": detail.get("balance_type"),
        "delta_mw": float(delta_mw),
        "initial_security": initial_security,
        "initial_major_overloads": detail.get("major_overloads", []),
    }

    if (
        not detail.get("post_contingency_converged")
        or security.get("post_status") != "CONVERGED"
    ):
        return {
            **common,
            "analysis_status": "SECURITY_NOT_CONVERGED",
            "monitored_line_id": None,
            "target_selection": "not_available",
            "candidate_count": 0,
            "candidates": [],
            "best_tested_candidate": None,
        }

    target_selection = "user_specified"

    if monitored_line_id is None:
        overloads = detail.get("major_overloads", [])
        if not overloads:
            return {
                **common,
                "analysis_status": "NO_OVERLOADED_LINE",
                "monitored_line_id": None,
                "target_selection": "not_required",
                "candidate_count": 0,
                "candidates": [],
                "best_tested_candidate": None,
            }

        selected = max(
            overloads,
            key=lambda item: float(item["loading_percent"]),
        )
        monitored_line_id = selected["equipment_id"]
        target_selection = "most_severe_violation"

    candidate_result = generate_generator_outage_redispatch_candidates(
        case_path=case_path,
        outage_generator_id=outage_generator_id,
        monitored_line_id=monitored_line_id,
        slack_mode=slack_mode,
        balance_type=balance_type,
        delta_mw=delta_mw,
        top_n=top_n,
    )

    validated_candidates = []

    for candidate in candidate_result["candidates"]:
        validation = validate_generator_outage_balanced_redispatch(
            case_path=case_path,
            outage_generator_id=outage_generator_id,
            monitored_line_id=monitored_line_id,
            up_generator_id=candidate["up_generator_id"],
            down_generator_id=candidate["down_generator_id"],
            delta_mw=candidate["delta_mw"],
            slack_mode=slack_mode,
            balance_type=balance_type,
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
                    "abs_p1_reduction_mw": candidate[
                        "predicted_abs_p1_reduction_mw"
                    ],
                },
                "ac_validation": validation,
                "target_selection": target_selection,
            }
        )

    validated_candidates.sort(key=_ac_sort_key)

    for rank, item in enumerate(validated_candidates, start=1):
        item["ac_validation_rank"] = rank

    return {
        **common,
        "analysis_status": "COMPLETED",
        "monitored_line_id": monitored_line_id,
        "target_selection": target_selection,
        "candidate_count": len(validated_candidates),
        "candidates": validated_candidates,
        "best_tested_candidate": (
            validated_candidates[0] if validated_candidates else None
        ),
    }
