from typing import Any

from ai_ems.api.schemas import (
    ContingencyResponseResponse,
    RedispatchCandidateResponse,
    RedispatchValidationResponse,
    SecurityAnalysisResponse,
    SensitivityAnalysisResponse,
    SensitivityCandidateResponse,
    ViolationSummary,
)


def _violation_summary(
    item: dict[str, Any],
) -> ViolationSummary:
    return ViolationSummary(
        equipment_id=item["equipment_id"],
        limit_type=item.get("limit_type"),
        limit_name=item.get("limit_name"),
        unit=item.get("unit"),
        limit=item.get("limit"),
        value=item.get("value"),
        violation_amount=item.get("violation_amount"),
        loading_percent=item.get("loading_percent"),
    )


def to_security_response(
    result: dict[str, Any],
) -> SecurityAnalysisResponse:
    comparison = result.get(
        "violation_comparison",
        {},
    )

    remaining = []

    for item in comparison.get("remaining", []):
        after = item.get("after")

        if after is not None:
            remaining.append(_violation_summary(after))

    return SecurityAnalysisResponse(
        outage_line_id=result["outage_line_id"],
        base_converged=result["base_converged"],
        pre_status=result.get("pre_status"),
        post_status=result.get("post_status"),
        pre_violated_equipment_count=result.get(
            "pre_violated_equipment_count",
            0,
        ),
        post_violated_equipment_count=result.get(
            "violated_equipment_count",
            0,
        ),
        new_violation_count=comparison.get(
            "new_count",
            0,
        ),
        remaining_violation_count=comparison.get(
            "remaining_count",
            0,
        ),
        resolved_violation_count=comparison.get(
            "resolved_count",
            0,
        ),
        new_violations=[_violation_summary(item) for item in comparison.get("new", [])],
        remaining_violations=remaining,
        resolved_violations=[
            _violation_summary(item)
            for item in comparison.get(
                "resolved",
                [],
            )
        ],
    )


def _candidate_response(
    candidate: dict[str, Any],
) -> RedispatchCandidateResponse:
    redispatch = candidate["redispatch"]
    prediction = candidate["prediction"]
    validation = candidate["ac_validation"]

    post = validation.get(
        "post_contingency",
        {},
    )
    after = validation.get(
        "after_redispatch",
        {},
    )

    whole = validation.get(
        "whole_network_validation",
        {},
    )

    operator_status = whole.get("operator_strategy_status")

    return RedispatchCandidateResponse(
        rank=candidate["rank"],
        ac_validation_rank=candidate.get("ac_validation_rank"),
        up_generator_id=redispatch["up_generator_id"],
        down_generator_id=redispatch["down_generator_id"],
        delta_mw=redispatch["delta_mw"],
        predicted_active_power_change_mw=(prediction.get("active_power_change_mw")),
        predicted_abs_p1_reduction_mw=(prediction.get("abs_p1_reduction_mw")),
        apparent_power_before_mva=post.get("apparent_power_mva"),
        apparent_power_after_mva=after.get("apparent_power_mva"),
        improvement_mva=validation.get("improvement_mva"),
        loading_before_percent=validation.get("loading_before_percent"),
        loading_after_percent=validation.get("loading_after_percent"),
        violation_remaining=validation.get("violation_remaining"),
        whole_network_converged=(operator_status == "CONVERGED"),
        new_violation_detected=whole.get(
            "new_violation_detected",
            False,
        ),
        violated_equipment_count_after=whole.get("violated_equipment_count_after"),
        remaining_violation_ids=[
            item["equipment_id"]
            for item in whole.get(
                "remaining_violations",
                [],
            )
        ],
    )


def to_contingency_response(
    result: dict[str, Any],
) -> ContingencyResponseResponse:
    initial = result.get(
        "initial_security",
        {},
    )

    comparison = initial.get(
        "violation_comparison",
        {},
    )

    candidates = [
        _candidate_response(item)
        for item in result.get(
            "candidates",
            [],
        )
    ]

    best = result.get("best_tested_candidate")

    return ContingencyResponseResponse(
        outage_line_id=result["outage_line_id"],
        target_line_id=result.get("monitored_line_id"),
        target_selection=result.get(
            "target_selection",
            "unknown",
        ),
        delta_mw=result["delta_mw"],
        candidate_count=result["candidate_count"],
        pre_violated_equipment_count=(
            initial.get(
                "pre_violated_equipment_count",
                0,
            )
        ),
        post_violated_equipment_count=(
            initial.get(
                "post_violated_equipment_count",
                0,
            )
        ),
        contingency_new_violation_count=(
            comparison.get(
                "new_count",
                0,
            )
        ),
        contingency_new_violation_ids=[
            item["equipment_id"]
            for item in comparison.get(
                "new",
                [],
            )
        ],
        candidates=candidates,
        best_candidate=(_candidate_response(best) if best is not None else None),
    )


def to_sensitivity_response(
    result: dict[str, Any],
    target_selection: str,
) -> SensitivityAnalysisResponse:
    return SensitivityAnalysisResponse(
        outage_line_id=result["outage_line_id"],
        monitored_line_id=result["monitored_line_id"],
        target_selection=target_selection,
        candidate_count=result["candidate_count"],
        candidates=[
            SensitivityCandidateResponse(
                generator_id=item["generator_id"],
                sensitivity=item["sensitivity"],
                abs_sensitivity=item["abs_sensitivity"],
            )
            for item in result.get(
                "candidates",
                [],
            )
        ],
    )


def to_redispatch_validation_response(
    result: dict[str, Any],
) -> RedispatchValidationResponse:
    redispatch = result["redispatch"]
    post = result["post_contingency"]
    after = result["after_redispatch"]

    whole = result.get(
        "whole_network_validation",
        {},
    )

    return RedispatchValidationResponse(
        outage_line_id=result["outage_line_id"],
        monitored_line_id=result["monitored_line_id"],
        up_generator_id=redispatch["up_generator_id"],
        down_generator_id=redispatch["down_generator_id"],
        delta_mw=redispatch["delta_mw"],
        post_contingency_converged=post["converged"],
        after_redispatch_converged=after["converged"],
        limit_mva=result.get("limit_mva"),
        apparent_power_before_mva=post.get("apparent_power_mva"),
        apparent_power_after_mva=after.get("apparent_power_mva"),
        apparent_power_change_mva=result.get("apparent_power_change_mva"),
        improvement_mva=result.get("improvement_mva"),
        improved=result.get("improved"),
        loading_before_percent=result.get("loading_before_percent"),
        loading_after_percent=result.get("loading_after_percent"),
        violation_remaining=result.get("violation_remaining"),
        whole_network_converged=(whole.get("operator_strategy_status") == "CONVERGED"),
        new_violation_detected=whole.get(
            "new_violation_detected",
            False,
        ),
        violated_equipment_count_before=whole.get("violated_equipment_count_before"),
        violated_equipment_count_after=whole.get("violated_equipment_count_after"),
        new_violation_ids=[
            item["equipment_id"]
            for item in whole.get(
                "new_violations",
                [],
            )
        ],
        resolved_violation_ids=[
            item["equipment_id"]
            for item in whole.get(
                "resolved_violations",
                [],
            )
        ],
        remaining_violation_ids=[
            item["equipment_id"]
            for item in whole.get(
                "remaining_violations",
                [],
            )
        ],
    )
