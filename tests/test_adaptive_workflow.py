from ai_ems.tools import workflow_tools


def _detail():
    return {
        "post_contingency_converged": True,
        "balance_type": "PROPORTIONAL_TO_GENERATION_P_MAX",
        "major_overloads": [{"equipment_id": "LINE-A", "loading_percent": 105}],
        "security_analysis": {
            "pre_status": "CONVERGED", "post_status": "CONVERGED",
            "pre_violated_equipment_count": 0, "violated_equipment_count": 1,
            "violation_comparison": {},
        },
    }


def _candidate(delta, rank):
    return {
        "rank": rank, "up_generator_id": "UP", "down_generator_id": "DOWN",
        "delta_mw": delta, "estimated_delta_mw": 15.0,
        "feasible_delta_max_mw": 30.0,
        "estimation_method": "CONSTANT_Q_P1_APPROXIMATION",
        "predicted_active_power_change_mw": -delta,
        "predicted_abs_p1_reduction_mw": delta,
    }


def _validation(after_excess):
    return {
        "after_redispatch": {"converged": True},
        "improvement_mva": 10.0,
        "whole_network_validation": {
            "operator_strategy_status": "CONVERGED",
            "risk_assessment": {
                "recommendation_eligible": True, "rejection_reasons": [],
                "risk_metrics": {
                    "max_normalized_excess": after_excess,
                    "total_normalized_excess": after_excess,
                    "remaining_violation_count": 1,
                },
            },
        },
        "generator_limit_violations": [],
    }


def test_adaptive_workflow_ranks_global_risk_and_reports_recommendation(monkeypatch):
    monkeypatch.setattr(workflow_tools, "analyze_generator_contingency",
                        lambda **kwargs: _detail())
    monkeypatch.setattr(workflow_tools,
                        "generate_generator_outage_redispatch_candidates",
                        lambda **kwargs: {"candidates": [_candidate(10, 1), _candidate(20, 2)]})
    monkeypatch.setattr(workflow_tools,
                        "validate_generator_outage_balanced_redispatch",
                        lambda **kwargs: _validation(
                            0.05 if kwargs["delta_mw"] == 10 else 0.02))
    result = workflow_tools.analyze_generator_outage_response(
        "dummy.mat", "GEN-A", search_mode="adaptive", top_n=2)
    assert result["analysis_status"] == "COMPLETED"
    assert result["candidate_count"] == 2
    assert result["valid_candidate_count"] == 2
    assert result["recommended_candidate"]["redispatch"]["delta_mw"] == 20
    assert result["best_tested_candidate"]["redispatch"]["delta_mw"] == 20


def test_adaptive_failure_isolated_from_other_candidates(monkeypatch):
    monkeypatch.setattr(workflow_tools, "analyze_generator_contingency",
                        lambda **kwargs: _detail())
    monkeypatch.setattr(workflow_tools,
                        "generate_generator_outage_redispatch_candidates",
                        lambda **kwargs: {"candidates": [_candidate(10, 1), _candidate(20, 2)]})
    def validate(**kwargs):
        if kwargs["delta_mw"] == 10:
            raise RuntimeError("solver did not converge")
        return _validation(0.02)
    monkeypatch.setattr(workflow_tools,
                        "validate_generator_outage_balanced_redispatch", validate)
    result = workflow_tools.analyze_generator_outage_response(
        "dummy.mat", "GEN-A", search_mode="adaptive")
    assert result["valid_candidate_count"] == 1
    assert result["recommended_candidate"]["redispatch"]["delta_mw"] == 20
    assert any(item.get("validation_status") == "FAILED"
               for item in result["candidates"])


def test_adaptive_never_recommends_generator_limit_violation(monkeypatch):
    monkeypatch.setattr(workflow_tools, "analyze_generator_contingency",
                        lambda **kwargs: _detail())
    monkeypatch.setattr(workflow_tools,
                        "generate_generator_outage_redispatch_candidates",
                        lambda **kwargs: {"candidates": [_candidate(10, 1)]})
    def invalid(**kwargs):
        result = _validation(0.01)
        result["generator_limit_violations"] = [{"generator_id": "OTHER"}]
        return result
    monkeypatch.setattr(workflow_tools,
                        "validate_generator_outage_balanced_redispatch", invalid)
    result = workflow_tools.analyze_generator_outage_response(
        "dummy.mat", "GEN-A", search_mode="adaptive")
    assert result["analysis_status"] == "NO_RECOMMENDABLE_CANDIDATE"
    assert result["recommended_candidate"] is None
    assert "GENERATOR_LIMIT_VIOLATION" in (
        result["candidates"][0]["risk_assessment"]["rejection_reasons"])
