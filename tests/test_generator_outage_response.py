from ai_ems.agent.formatters import format_generator_outage_response
from ai_ems.tools import workflow_tools
from ai_ems.tools.generator_control_tools import _select_diverse_candidates


def _security_result(post_status: str = "CONVERGED") -> dict:
    return {
        "pre_status": "CONVERGED",
        "post_status": post_status,
        "pre_violated_equipment_count": 0,
        "violated_equipment_count": 1 if post_status == "CONVERGED" else 0,
        "violation_comparison": {
            "new_count": 1 if post_status == "CONVERGED" else 0,
            "resolved_count": 0,
            "remaining_count": 0,
            "new": (
                [{"equipment_id": "LINE-134-193"}]
                if post_status == "CONVERGED"
                else []
            ),
            "resolved": [],
            "remaining": [],
        },
    }


def _generator_detail(
    *,
    post_status: str = "CONVERGED",
    post_converged: bool = True,
    overloads: list[dict] | None = None,
) -> dict:
    return {
        "post_contingency_converged": post_converged,
        "balance_type": "PROPORTIONAL_TO_GENERATION_P_MAX",
        "major_overloads": (
            [
                {
                    "equipment_id": "LINE-134-193",
                    "loading_percent": 105.60,
                }
            ]
            if overloads is None
            else overloads
        ),
        "security_analysis": _security_result(post_status),
    }


def _validation(improvement_mva: float) -> dict:
    return {
        "post_contingency": {
            "converged": True,
            "apparent_power_mva": 2293.71,
        },
        "after_redispatch": {
            "converged": True,
            "apparent_power_mva": 2293.71 - improvement_mva,
        },
        "improvement_mva": improvement_mva,
        "improved": improvement_mva > 0,
        "loading_before_percent": 105.60,
        "loading_after_percent": 105.35,
        "violation_remaining": True,
        "whole_network_validation": {
            "operator_strategy_status": "CONVERGED",
            "new_violation_detected": False,
            "new_violations": [],
            "remaining_violations": [
                {"equipment_id": "LINE-134-193"}
            ],
            "violated_equipment_count_before": 1,
            "violated_equipment_count_after": 1,
        },
    }


def _formatter_result() -> dict:
    validation = _validation(5.60)
    candidate = {
        "rank": 1,
        "ac_validation_rank": 1,
        "redispatch": {
            "up_generator_id": "GEN-106",
            "down_generator_id": "GEN-193",
            "delta_mw": 10.0,
        },
        "prediction": {
            "active_power_change_mw": 5.56,
            "abs_p1_reduction_mw": 5.56,
        },
        "ac_validation": validation,
        "target_selection": "most_severe_violation",
    }

    return {
        "analysis_type": "Generator Outage Response Analysis",
        "analysis_status": "COMPLETED",
        "outage_generator_id": "GEN-124#1",
        "slack_mode": "distributed",
        "balance_type": "PROPORTIONAL_TO_GENERATION_P_MAX",
        "delta_mw": 10.0,
        "initial_security": {
            "pre_status": "CONVERGED",
            "post_status": "CONVERGED",
            "pre_violated_equipment_count": 0,
            "post_violated_equipment_count": 1,
            "violation_comparison": {
                "new_count": 1,
                "new": [{"equipment_id": "LINE-134-193"}],
            },
        },
        "initial_major_overloads": [
            {
                "equipment_id": "LINE-134-193",
                "loading_percent": 105.60,
            }
        ],
        "monitored_line_id": "LINE-134-193",
        "target_selection": "most_severe_violation",
        "candidate_count": 1,
        "candidates": [candidate],
        "best_tested_candidate": candidate,
    }


def test_generator_outage_response_completes_and_reranks_by_ac_validation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workflow_tools,
        "analyze_generator_contingency",
        lambda **kwargs: _generator_detail(),
    )
    monkeypatch.setattr(
        workflow_tools,
        "generate_generator_outage_redispatch_candidates",
        lambda **kwargs: {
            "candidates": [
                {
                    "rank": 1,
                    "up_generator_id": "GEN-A",
                    "down_generator_id": "GEN-D",
                    "delta_mw": 10.0,
                    "predicted_active_power_change_mw": 5.0,
                    "predicted_abs_p1_reduction_mw": 5.0,
                },
                {
                    "rank": 2,
                    "up_generator_id": "GEN-B",
                    "down_generator_id": "GEN-D",
                    "delta_mw": 10.0,
                    "predicted_active_power_change_mw": 4.0,
                    "predicted_abs_p1_reduction_mw": 4.0,
                },
            ]
        },
    )

    def validate(**kwargs):
        improvement = 4.0 if kwargs["up_generator_id"] == "GEN-A" else 6.0
        return _validation(improvement)

    monkeypatch.setattr(
        workflow_tools,
        "validate_generator_outage_balanced_redispatch",
        validate,
    )

    result = workflow_tools.analyze_generator_outage_response(
        case_path="dummy.mat",
        outage_generator_id="GEN-124#1",
        slack_mode="distributed",
        top_n=2,
    )

    assert result["analysis_status"] == "COMPLETED"
    assert result["monitored_line_id"] == "LINE-134-193"
    assert result["target_selection"] == "most_severe_violation"
    assert result["candidate_count"] == 2
    assert result["best_tested_candidate"]["redispatch"]["up_generator_id"] == (
        "GEN-B"
    )
    assert result["best_tested_candidate"]["rank"] == 2
    assert result["best_tested_candidate"]["ac_validation_rank"] == 1


def test_generator_outage_response_stops_when_no_overloaded_line(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workflow_tools,
        "analyze_generator_contingency",
        lambda **kwargs: _generator_detail(overloads=[]),
    )
    monkeypatch.setattr(
        workflow_tools,
        "generate_generator_outage_redispatch_candidates",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("candidate generation must not run")
        ),
    )

    result = workflow_tools.analyze_generator_outage_response(
        case_path="dummy.mat",
        outage_generator_id="GEN-166",
    )

    assert result["analysis_status"] == "NO_OVERLOADED_LINE"
    assert result["monitored_line_id"] is None
    assert result["target_selection"] == "not_required"
    assert result["candidate_count"] == 0
    assert result["best_tested_candidate"] is None


def test_generator_outage_response_stops_when_security_does_not_converge(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workflow_tools,
        "analyze_generator_contingency",
        lambda **kwargs: _generator_detail(
            post_status="MAX_ITERATION_REACHED",
            post_converged=False,
            overloads=[],
        ),
    )
    monkeypatch.setattr(
        workflow_tools,
        "generate_generator_outage_redispatch_candidates",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("candidate generation must not run")
        ),
    )

    result = workflow_tools.analyze_generator_outage_response(
        case_path="dummy.mat",
        outage_generator_id="GEN-124#1",
    )

    assert result["analysis_status"] == "SECURITY_NOT_CONVERGED"
    assert result["monitored_line_id"] is None
    assert result["target_selection"] == "not_available"
    assert result["candidate_count"] == 0
    assert result["best_tested_candidate"] is None
    assert result["initial_security"]["post_status"] == "MAX_ITERATION_REACHED"


def test_generator_outage_formatter_separates_sensitivity_and_ac_quantities() -> None:
    text = format_generator_outage_response(_formatter_result())

    assert "GEN-106 +10.0 MW / GEN-193 -10.0 MW" in text
    assert "선로 유효전력 조류 절댓값 약 5.56 MW 감소" in text
    assert "2293.71 MVA → 2288.11 MVA" in text
    assert "과부하는 완화되었지만 설비 한계 위반은 여전히 남아 있습니다" in text
    assert "새로 발생한 위반 설비는 확인되지 않았습니다" in text
    assert "MW 변화" in text
    assert "피상전력 MVA" in text
    assert "동일한 물리량처럼 직접 비교하지 않습니다" in text


def test_generator_outage_formatter_handles_no_overloaded_line() -> None:
    result = {
        "analysis_type": "Generator Outage Response Analysis",
        "analysis_status": "NO_OVERLOADED_LINE",
        "outage_generator_id": "GEN-166",
        "initial_security": {"post_status": "CONVERGED"},
    }

    text = format_generator_outage_response(result)

    assert "GEN-166 발전기 사고 대응방안 분석" in text
    assert "Security Analysis는 수렴했습니다" in text
    assert "과부하 선로가 확인되지 않았습니다" in text
    assert "추가 Redispatch 후보 검토는 수행하지 않았습니다" in text


def test_select_diverse_candidates_skips_duplicate_bus_pairs() -> None:
    ranked_pairs = [
        {
            "up_generator_id": "GEN-106",
            "down_generator_id": "GEN-193",
            "up_bus_id": "VL-106_0",
            "down_bus_id": "VL-193_0",
            "up_sensitivity_mw_per_mw": 0.09,
            "down_sensitivity_mw_per_mw": -0.46,
        },
        {
            "up_generator_id": "GEN-106#1",
            "down_generator_id": "GEN-193#0",
            "up_bus_id": "VL-106_0",
            "down_bus_id": "VL-193_0",
            "up_sensitivity_mw_per_mw": 0.09,
            "down_sensitivity_mw_per_mw": -0.46,
        },
        {
            "up_generator_id": "GEN-100#2",
            "down_generator_id": "GEN-193",
            "up_bus_id": "VL-100_0",
            "down_bus_id": "VL-193_0",
            "up_sensitivity_mw_per_mw": 0.06,
            "down_sensitivity_mw_per_mw": -0.46,
        },
    ]

    selected = _select_diverse_candidates(ranked_pairs, top_n=3)

    assert len(selected) == 2
    assert [item["up_generator_id"] for item in selected] == [
        "GEN-106",
        "GEN-100#2",
    ]


def test_generator_outage_response_is_registered_for_deterministic_formatting() -> None:
    from ai_ems.agent.graph import DETERMINISTIC_FORMATTERS

    analysis_type, formatter = DETERMINISTIC_FORMATTERS[
        "generator_outage_response_analysis"
    ]

    assert analysis_type == "Generator Outage Response Analysis"
    assert formatter is format_generator_outage_response
