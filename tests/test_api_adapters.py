import pytest
from pydantic import ValidationError

from ai_ems.api.schemas import (
    GeneratorContingencyAnalysisRequest,
)

from ai_ems.api.adapters import (
    to_contingency_response,
    to_redispatch_validation_response,
    to_security_response,
    to_sensitivity_response,
    to_generator_contingency_response,
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


def test_sensitivity_response_adapter() -> None:
    domain_result = {
        "outage_line_id": "LINE-183-190",
        "monitored_line_id": "LINE-176-190",
        "candidate_count": 2,
        "candidates": [
            {
                "generator_id": "GEN-190",
                "sensitivity": -0.589638,
                "abs_sensitivity": 0.589638,
            },
            {
                "generator_id": "GEN-10#0",
                "sensitivity": 0.421,
                "abs_sensitivity": 0.421,
            },
        ],
    }

    response = to_sensitivity_response(
        domain_result,
        target_selection="most_severe_violation",
    )

    assert response.outage_line_id == "LINE-183-190"
    assert response.monitored_line_id == "LINE-176-190"
    assert response.target_selection == "most_severe_violation"
    assert response.candidate_count == 2
    assert response.candidates[0].generator_id == "GEN-190"
    assert response.candidates[0].sensitivity == -0.589638


def test_redispatch_validation_response_adapter() -> None:
    domain_result = {
        "outage_line_id": "LINE-183-190",
        "monitored_line_id": "LINE-176-190",
        "redispatch": {
            "up_generator_id": "GEN-10#0",
            "down_generator_id": "GEN-190",
            "delta_mw": 10.0,
        },
        "post_contingency": {
            "converged": True,
            "apparent_power_mva": 2476.13,
        },
        "after_redispatch": {
            "converged": True,
            "apparent_power_mva": 2469.21,
        },
        "limit_mva": 2172.0,
        "apparent_power_change_mva": -6.92,
        "improvement_mva": 6.92,
        "improved": True,
        "loading_before_percent": 114.00,
        "loading_after_percent": 113.68,
        "violation_remaining": True,
        "whole_network_validation": {
            "operator_strategy_status": "CONVERGED",
            "new_violation_detected": False,
            "violated_equipment_count_before": 1,
            "violated_equipment_count_after": 1,
            "new_violations": [],
            "resolved_violations": [],
            "remaining_violations": [
                {
                    "equipment_id": "LINE-176-190",
                }
            ],
        },
    }

    response = to_redispatch_validation_response(domain_result)

    assert response.outage_line_id == "LINE-183-190"
    assert response.up_generator_id == "GEN-10#0"
    assert response.down_generator_id == "GEN-190"
    assert response.improved is True
    assert response.violation_remaining is True
    assert response.whole_network_converged is True
    assert response.new_violation_detected is False
    assert response.remaining_violation_ids == ["LINE-176-190"]


def test_generator_contingency_response_adapter():
    domain_result = {
        "generator_id": "GEN-124#1",
        "contingency_id": "GEN_OUT_GEN-124#1",
        "slack_mode": "distributed",
        "balance_type": "PROPORTIONAL_TO_GENERATION_P_MAX",
        "balancing_interpretation": (
            "Load-flow balancing assumption; " "not operator corrective redispatch."
        ),
        "base_converged": True,
        "post_contingency_converged": True,
        "outage_generator": {
            "generator_id": "GEN-124#1",
            "connected": True,
            "bus_id": "VL-124_0",
            "target_p_mw": 984.329,
            "actual_generation_mw": 984.329,
            "min_p_mw": 950.0,
            "max_p_mw": 1000.0,
            "headroom_mw": 15.671,
        },
        "base": {
            "loadflow": {
                "converged": True,
                "components": [],
                "distributed_active_power_mw": 0.0,
                "active_power_mismatch_mw": 0.0,
            },
            "power_balance": {
                "observed_generation_mw": 10000.0,
                "load_mw": 9168.0,
                "line_active_power_loss_mw": 832.0,
                "balance_based_loss_mw": 832.0,
            },
        },
        "post_contingency": {
            "loadflow": {
                "converged": True,
                "components": [],
                "distributed_active_power_mw": 969.18,
                "active_power_mismatch_mw": 0.433,
            },
            "power_balance": {
                "observed_generation_mw": 9985.0,
                "load_mw": 9168.0,
                "line_active_power_loss_mw": 817.316,
                "balance_based_loss_mw": 817.311,
            },
        },
        "loss_change": {
            "balance_based_loss_change_mw": -14.689,
            "line_active_power_loss_change_mw": -14.684,
        },
        "generator_changes": [
            {
                "generator_id": "GEN-82#5",
                "bus_id": "VL-82_0",
                "generation_before_mw": 1378.26,
                "generation_after_mw": 1397.686,
                "delta_generation_mw": 19.426,
                "min_p_mw": 0.0,
                "max_p_mw": 1400.0,
                "headroom_before_mw": 21.74,
                "headroom_after_mw": 2.314,
            }
        ],
        "major_overloads": [
            {
                "equipment_id": "LINE-134-193",
                "limit_type": "APPARENT_POWER",
                "limit_name": "RateC",
                "unit": "MVA",
                "limit": 2172.0,
                "value": 2293.708,
                "violation_amount": 121.708,
                "loading_percent": 105.603,
            }
        ],
        "security_analysis": {
            "pre_status": "CONVERGED",
            "post_status": "CONVERGED",
            "pre_violated_equipment_count": 0,
            "violated_equipment_count": 1,
            "pre_violated_equipment": [],
            "violated_equipment": [
                {
                    "equipment_id": "LINE-134-193",
                    "limit_type": "APPARENT_POWER",
                    "limit_name": "RateC",
                    "unit": "MVA",
                    "limit": 2172.0,
                    "value": 2293.708,
                    "violation_amount": 121.708,
                    "loading_percent": 105.603,
                }
            ],
        },
    }

    response = to_generator_contingency_response(domain_result)

    assert response.generator_id == "GEN-124#1"
    assert response.slack_mode == "distributed"

    assert response.post_loadflow.distributed_active_power_mw == 969.18

    assert len(response.major_overloads) == 1

    assert response.major_overloads[0].equipment_id == "LINE-134-193"

    assert len(response.generator_changes) == 1

    assert response.generator_changes[0].delta_generation_mw == 19.426

    assert response.post_violated_equipment_count == 1


def test_generator_contingency_response_adapter_handles_non_convergence():
    domain_result = {
        "generator_id": "GEN-X",
        "contingency_id": "GEN_OUT_GEN-X",
        "slack_mode": "single",
        "balance_type": None,
        "balancing_interpretation": (
            "Load-flow balancing assumption; " "not operator corrective redispatch."
        ),
        "base_converged": True,
        "post_contingency_converged": False,
        "outage_generator": {
            "generator_id": "GEN-X",
            "connected": True,
            "bus_id": "VL-X",
            "target_p_mw": 1000.0,
            "actual_generation_mw": 1000.0,
            "min_p_mw": 0.0,
            "max_p_mw": 1200.0,
            "headroom_mw": 200.0,
        },
        "base": {
            "loadflow": {
                "converged": True,
                "components": [],
                "distributed_active_power_mw": 0.0,
                "active_power_mismatch_mw": 0.0,
            },
            "power_balance": {
                "observed_generation_mw": 10000.0,
                "load_mw": 9000.0,
                "line_active_power_loss_mw": 1000.0,
                "balance_based_loss_mw": 1000.0,
            },
        },
        "post_contingency": {
            "loadflow": {
                "converged": False,
                "components": [],
                "distributed_active_power_mw": 0.0,
                "active_power_mismatch_mw": 0.0,
            },
            "power_balance": None,
        },
        "loss_change": {
            "balance_based_loss_change_mw": None,
            "line_active_power_loss_change_mw": None,
        },
        "generator_changes": [],
        "major_overloads": [],
        "security_analysis": {
            "pre_status": "CONVERGED",
            "post_status": "FAILED",
            "pre_violated_equipment_count": 0,
            "violated_equipment_count": 0,
            "pre_violated_equipment": [],
            "violated_equipment": [],
        },
    }

    response = to_generator_contingency_response(domain_result)

    assert response.post_contingency_converged is False
    assert response.post_power_balance is None
    assert response.balance_based_loss_change_mw is None
    assert response.line_active_power_loss_change_mw is None
    assert response.generator_changes == []


def test_generator_contingency_request_rejects_invalid_slack_mode():
    with pytest.raises(ValidationError):
        GeneratorContingencyAnalysisRequest(
            generator_id="GEN-124#1",
            slack_mode="wrong",
        )


def test_generator_contingency_request_rejects_invalid_top_n():
    with pytest.raises(ValidationError):
        GeneratorContingencyAnalysisRequest(
            generator_id="GEN-124#1",
            top_n_overloads=0,
        )
