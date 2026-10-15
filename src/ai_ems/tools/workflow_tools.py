from ai_ems.network import load_network
from ai_ems.tools.adaptive_redispatch import adaptive_candidate_sort_key
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
    search_mode: str = "fixed",
    max_deltas_per_pair: int = 5,
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
    if search_mode not in {"fixed", "adaptive"}:
        raise ValueError("search_mode must be 'fixed' or 'adaptive'.")
    if max_deltas_per_pair <= 0:
        raise ValueError("max_deltas_per_pair must be greater than zero.")

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
        "search_mode": search_mode,
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
        **(
            {"search_mode": "adaptive", "max_deltas_per_pair": max_deltas_per_pair}
            if search_mode == "adaptive" else {}
        ),
    )

    validated_candidates = []

    for candidate in candidate_result["candidates"]:
        try:
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
        except (RuntimeError, ValueError) as exc:
            if search_mode != "adaptive":
                raise
            validated_candidates.append({
                "rank": candidate["rank"],
                "redispatch": {
                    "up_generator_id": candidate["up_generator_id"],
                    "down_generator_id": candidate["down_generator_id"],
                    "delta_mw": candidate["delta_mw"],
                },
                "validation_status": "FAILED",
                "validation_error": str(exc),
                "risk_assessment": {"recommendation_eligible": False,
                                    "rejection_reasons": ["VALIDATION_FAILURE"]},
                "ac_validation": {},
            })
            continue

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
                **({
                    "estimated_delta_mw": candidate.get("estimated_delta_mw"),
                    "feasible_delta_max_mw": candidate.get("feasible_delta_max_mw"),
                    "estimation_method": candidate.get("estimation_method"),
                    "risk_assessment": _assess_adaptive_candidate(validation),
                } if search_mode == "adaptive" else {}),
                "target_selection": target_selection,
            }
        )

    if search_mode == "adaptive":
        validated_candidates.sort(key=adaptive_candidate_sort_key)
    else:
        validated_candidates.sort(key=_ac_sort_key)

    for rank, item in enumerate(validated_candidates, start=1):
        item["ac_validation_rank"] = rank

    recommended = next(
        (item for item in validated_candidates
         if item.get("risk_assessment", {}).get("recommendation_eligible")),
        None,
    ) if search_mode == "adaptive" else None

    return {
        **common,
        "analysis_status": (
            "NO_RECOMMENDABLE_CANDIDATE"
            if search_mode == "adaptive" and recommended is None
            else "COMPLETED"
        ),
        "monitored_line_id": monitored_line_id,
        "target_selection": target_selection,
        "candidate_count": len(validated_candidates),
        **({
            "recommended_candidate": recommended,
            "valid_candidate_count": sum(
                1 for item in validated_candidates
                if item.get("risk_assessment", {}).get("recommendation_eligible")
            ),
        } if search_mode == "adaptive" else {}),
        "candidates": validated_candidates,
        "best_tested_candidate": (
            validated_candidates[0] if validated_candidates else None
        ),
    }


def _assess_adaptive_candidate(validation: dict) -> dict:
    """Include AC convergence and all-generator physical bounds in eligibility."""
    whole = validation.get("whole_network_validation") or {}
    assessment = dict(whole.get("risk_assessment") or {
        "recommendation_eligible": False,
        "rejection_reasons": ["MISSING_SECURITY_RISK_METRICS"],
    })
    reasons = list(assessment.get("rejection_reasons", []))
    if not validation.get("after_redispatch", {}).get("converged"):
        reasons.append("AC_NOT_CONVERGED")
    if validation.get("generator_limit_violations"):
        reasons.append("GENERATOR_LIMIT_VIOLATION")
    assessment["rejection_reasons"] = reasons
    assessment["recommendation_eligible"] = (
        bool(assessment.get("recommendation_eligible")) and not reasons
    )
    return assessment
