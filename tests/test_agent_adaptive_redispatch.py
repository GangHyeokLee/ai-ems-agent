"""Agent-facing Adaptive Redispatch routing and formatting regression tests."""
import pytest

from ai_ems.agent import tools as agent_tools
from ai_ems.agent.formatters.generator_response import format_generator_outage_response


def _get_tool(name, monkeypatch, **overrides):
    calls = []
    monkeypatch.setattr(agent_tools, "analyze_generator_outage_response",
                        lambda **kwargs: calls.append(kwargs) or {"search_mode": kwargs.get("search_mode", "fixed")})
    monkeypatch.setattr(agent_tools, "analyze_generator_risk_response",
                        lambda **kwargs: calls.append(kwargs) or {"search_mode": kwargs.get("search_mode", "fixed")})
    available = {tool.name: tool for tool in agent_tools.create_agent_tools(network=None, case_path="dummy.mat")}
    return available[name], calls


def test_agent_specific_generator_default_keeps_fixed(monkeypatch):
    tool, calls = _get_tool("generator_outage_response_analysis", monkeypatch)
    result = tool.invoke({"generator_id": "GEN-A"})
    assert result["search_mode"] == "fixed"
    assert "search_mode" not in calls[-1]


def test_agent_specific_generator_forwards_adaptive(monkeypatch):
    tool, calls = _get_tool("generator_outage_response_analysis", monkeypatch)
    result = tool.invoke({"generator_id": "GEN-A", "search_mode": "adaptive",
                          "max_deltas_per_pair": 5})
    assert result["search_mode"] == "adaptive"
    assert calls[-1]["max_deltas_per_pair"] == 5


def test_agent_risk_workflow_forwards_adaptive(monkeypatch):
    tool, calls = _get_tool("generator_risk_response_analysis", monkeypatch)
    result = tool.invoke({"search_mode": "adaptive", "max_deltas_per_pair": 5})
    assert result["search_mode"] == "adaptive"
    assert calls[-1]["max_deltas_per_pair"] == 5


def test_agent_rejects_excessive_delta_budget(monkeypatch):
    tool, _ = _get_tool("generator_outage_response_analysis", monkeypatch)
    with pytest.raises(ValueError, match="max_deltas_per_pair"):
        tool.invoke({"generator_id": "GEN-A", "search_mode": "adaptive",
                     "max_deltas_per_pair": 11})


def _adaptive_candidate(*, eligible=True, remaining=True, failure=False):
    item = {
        "rank": 1,
        "ac_validation_rank": 1,
        "redispatch": {"up_generator_id": "GEN-106",
                       "down_generator_id": "GEN-193", "delta_mw": 36.8879},
        "prediction": {"abs_p1_reduction_mw": 19.0},
        "estimated_delta_mw": 36.8879,
        "feasible_delta_max_mw": 36.8879,
        "estimation_method": "AC_TWO_END_FINITE_DIFFERENCE",
        "estimate_clipped_by_headroom": True,
        "risk_assessment": {"recommendation_eligible": eligible,
                            "rejection_reasons": [] if eligible else ["NEW_VIOLATION"]},
        "ac_validation": {
            "post_contingency": {"apparent_power_mva": 2293.7076},
            "after_redispatch": {"converged": True, "apparent_power_mva": 2273.0347},
            "improvement_mva": 20.6729,
            "loading_before_percent": 105.6035,
            "loading_after_percent": 104.6517,
            "violation_remaining": remaining,
            "whole_network_validation": {
                "operator_strategy_status": "CONVERGED",
                "violated_equipment_count_before": 1,
                "violated_equipment_count_after": 1 if remaining else 0,
                "new_violation_detected": False,
                "remaining_violations": [{"equipment_id": "LINE-134-193"}] if remaining else [],
            },
        },
    }
    if failure:
        item["validation_status"] = "FAILED"
        item["ac_validation"] = {}
    return item


def _adaptive_result(candidate):
    return {
        "analysis_status": "COMPLETED",
        "analysis_type": "Generator Outage Response Analysis",
        "outage_generator_id": "GEN-124#1",
        "search_mode": "adaptive",
        "slack_mode": "distributed",
        "initial_security": {},
        "monitored_line_id": "LINE-134-193",
        "candidate_count": 1,
        "valid_candidate_count": 1,
        "candidates": [candidate],
        "best_tested_candidate": candidate,
        "recommended_candidate": candidate,
    }


def test_adaptive_formatter_keeps_residual_overload_explicit():
    rendered = format_generator_outage_response(_adaptive_result(_adaptive_candidate()))
    assert "Adaptive" in rendered
    assert "AC_TWO_END_FINITE_DIFFERENCE" in rendered
    assert "36.89 MW" in rendered
    assert "104.65%" in rendered
    assert "한계 위반은 여전히 남아 있습니다" in rendered
    assert "추천 기준 통과는 위반 완전 해소나 실제 운전 승인을 의미하지 않습니다" in rendered


def test_adaptive_formatter_handles_failed_candidate_without_crash():
    item = _adaptive_candidate(failure=True)
    result = _adaptive_result(item)
    result["best_tested_candidate"] = item
    result["recommended_candidate"] = None
    rendered = format_generator_outage_response(result)
    assert "검증 실패" in rendered
    assert "검토 가능한 Redispatch 후보를 찾지 못했습니다" in rendered


def test_adaptive_formatter_distinguishes_not_eligible():
    item = _adaptive_candidate(eligible=False)
    result = _adaptive_result(item)
    result["recommended_candidate"] = None
    result["valid_candidate_count"] = 0
    result["analysis_status"] = "NO_RECOMMENDABLE_CANDIDATE"
    rendered = format_generator_outage_response(result)
    assert "추천 기준 통과**: 아니오" in rendered
    assert "NEW_VIOLATION" in rendered
    assert "추천 후보**: 없음" in rendered
