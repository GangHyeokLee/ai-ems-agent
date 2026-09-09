from ai_ems.api.adapters import (
    to_contingency_response,
    to_security_response,
)


def test_security_response_adapter() -> None:
    domain_result = {
        "outage_line_id": "LINE-81-84",
        "base_converged": True,
        "pre_status": "CONVERGED",
        "post_status": "CONVERGED",
        "pre_violated_equipment_count": 0,
        "violated_equipment_count": 2,
        "violation_comparison": {
            "new_count": 2,
            "remaining_count": 0,
            "resolved_count": 0,
            "new": [
                {
                    "equipment_id": "LINE-16-28",
                    "limit_type": "APPARENT_POWER",
                    "limit_name": "RateC",
                    "unit": "MVA",
                    "limit": 4344.0,
                    "value": 4465.24,
                    "violation_amount": 121.24,
                    "loading_percent": 102.79,
                },
                {
                    "equipment_id": "LINE-134-193",
                    "limit_type": "APPARENT_POWER",
                    "limit_name": "RateC",
                    "unit": "MVA",
                    "limit": 2172.0,
                    "value": 2194.38,
                    "violation_amount": 22.38,
                    "loading_percent": 101.03,
                },
            ],
            "remaining": [],
            "resolved": [],
        },
    }

    response = to_security_response(domain_result)

    assert response.outage_line_id == "LINE-81-84"
    assert response.base_converged is True
    assert response.pre_violated_equipment_count == 0
    assert response.post_violated_equipment_count == 2
    assert response.new_violation_count == 2
    assert len(response.new_violations) == 2
    assert response.new_violations[0].unit == "MVA"


def test_security_response_adapter_uses_post_state_for_remaining() -> None:
    domain_result = {
        "outage_line_id": "LINE-X",
        "base_converged": True,
        "pre_status": "CONVERGED",
        "post_status": "CONVERGED",
        "pre_violated_equipment_count": 1,
        "violated_equipment_count": 1,
        "violation_comparison": {
            "new_count": 0,
            "remaining_count": 1,
            "resolved_count": 0,
            "new": [],
            "resolved": [],
            "remaining": [
                {
                    "before": {
                        "equipment_id": "LINE-A",
                        "limit_type": "APPARENT_POWER",
                        "unit": "MVA",
                        "limit": 100.0,
                        "value": 105.0,
                        "violation_amount": 5.0,
                        "loading_percent": 105.0,
                    },
                    "after": {
                        "equipment_id": "LINE-A",
                        "limit_type": "APPARENT_POWER",
                        "unit": "MVA",
                        "limit": 100.0,
                        "value": 110.0,
                        "violation_amount": 10.0,
                        "loading_percent": 110.0,
                    },
                    "violation_amount_change": 5.0,
                    "trend": "worsened",
                }
            ],
        },
    }

    response = to_security_response(domain_result)

    assert response.remaining_violation_count == 1
    assert response.remaining_violations[0].equipment_id == "LINE-A"
    assert response.remaining_violations[0].value == 110.0


def test_contingency_response_adapter() -> None:
    candidate = {
        "rank": 1,
        "ac_validation_rank": 1,
        "redispatch": {
            "up_generator_id": "GEN-19#0",
            "down_generator_id": "GEN-82",
            "delta_mw": 10.0,
        },
        "prediction": {
            "active_power_change_mw": 3.98,
            "abs_p1_reduction_mw": 3.98,
        },
        "ac_validation": {
            "post_contingency": {
                "converged": True,
                "apparent_power_mva": 4465.24,
            },
            "after_redispatch": {
                "converged": True,
                "apparent_power_mva": 4460.85,
            },
            "improvement_mva": 4.39,
            "loading_before_percent": 102.79,
            "loading_after_percent": 102.69,
            "violation_remaining": True,
            "whole_network_validation": {
                "operator_strategy_status": "CONVERGED",
                "new_violation_detected": False,
                "violated_equipment_count_after": 2,
                "remaining_violations": [
                    {
                        "equipment_id": "LINE-16-28",
                    },
                    {
                        "equipment_id": "LINE-134-193",
                    },
                ],
            },
        },
    }

    domain_result = {
        "outage_line_id": "LINE-81-84",
        "monitored_line_id": "LINE-16-28",
        "target_selection": "most_severe_violation",
        "delta_mw": 10.0,
        "candidate_count": 1,
        "candidates": [candidate],
        "best_tested_candidate": candidate,
        "initial_security": {
            "pre_violated_equipment_count": 0,
            "post_violated_equipment_count": 2,
            "violation_comparison": {
                "new_count": 2,
                "new": [
                    {
                        "equipment_id": "LINE-16-28",
                    },
                    {
                        "equipment_id": "LINE-134-193",
                    },
                ],
            },
        },
    }

    response = to_contingency_response(domain_result)

    assert response.outage_line_id == "LINE-81-84"
    assert response.target_line_id == "LINE-16-28"
    assert response.candidate_count == 1
    assert response.contingency_new_violation_count == 2

    best = response.best_candidate

    assert best is not None
    assert best.up_generator_id == "GEN-19#0"
    assert best.down_generator_id == "GEN-82"
    assert best.whole_network_converged is True
    assert best.new_violation_detected is False
    assert best.violation_remaining is True
    assert best.remaining_violation_ids == [
        "LINE-16-28",
        "LINE-134-193",
    ]


def test_contingency_response_adapter_handles_no_candidate() -> None:
    domain_result = {
        "outage_line_id": "LINE-X",
        "monitored_line_id": "LINE-Y",
        "target_selection": "user_specified",
        "delta_mw": 10.0,
        "candidate_count": 0,
        "candidates": [],
        "best_tested_candidate": None,
        "initial_security": {
            "pre_violated_equipment_count": 0,
            "post_violated_equipment_count": 0,
            "violation_comparison": {
                "new_count": 0,
                "new": [],
            },
        },
    }

    response = to_contingency_response(domain_result)

    assert response.candidate_count == 0
    assert response.candidates == []
    assert response.best_candidate is None