import pytest

from ai_ems.tools.adaptive_redispatch import (
    adaptive_candidate_sort_key,
    build_adaptive_redispatch_candidates,
    calculate_feasible_delta_range,
    estimate_redispatch_delta,
    estimate_two_end_redispatch_delta,
    evaluate_violation_risk,
    generate_delta_candidates,
)


def state(actual=50.0, target=50.0, low=0.0, high=100.0, bus="A"):
    return {
        "connected": True, "actual_generation_mw": actual,
        "target_p_mw": target, "min_p_mw": low, "max_p_mw": high,
        "bus_id": bus,
    }


def test_feasible_delta_uses_actual_and_target_and_both_directions():
    assert calculate_feasible_delta_range(
        state(actual=90, target=95), state(actual=15, target=20)
    ) == 5.0
    assert calculate_feasible_delta_range(
        state(high=None), state()
    ) == 0.0
    assert calculate_feasible_delta_range(
        state(actual=120), state()
    ) == 0.0


def test_estimate_constant_q_is_mw_not_mva():
    # With Q=0, P=120 MW and MVA limit=100, k=-0.5 => 40 MW.
    assert estimate_redispatch_delta(120, 0, 100, -0.5, 80) == pytest.approx(40)
    assert estimate_redispatch_delta(120, 0, 100, 0, 80) is None
    assert estimate_redispatch_delta(120, 150, 100, -0.5, 80) is None
    assert estimate_redispatch_delta(120, None, 100, -0.5, 80) is None
    assert estimate_redispatch_delta(120, 0, 100, +0.5, 80) is None


def test_delta_candidates_include_baseline_and_respect_bound():
    result = generate_delta_candidates(30, 20, max_candidates=5)
    assert result[0] == 10
    assert len(result) <= 5
    assert all(0 < x <= 20 for x in result)
    assert len(set(result)) == len(result)
    assert generate_delta_candidates(None, 3)[0] == 3
    assert generate_delta_candidates(None, 0) == []


def test_adaptive_pair_can_use_less_than_fixed_ten_mw():
    generators = {
        "UP": state(actual=96, target=96, bus="UP-BUS"),
        "DOWN": state(actual=25, target=25, bus="DOWN-BUS"),
    }
    candidates = build_adaptive_redispatch_candidates(
        generators, {"UP": -0.7, "DOWN": 0.1}, 120, 0, -119, 0,
        100, top_n=1
    )
    assert candidates
    assert all(0 < x["delta_mw"] <= 4 for x in candidates)
    assert all(x["up_generator_id"] == "UP" for x in candidates)


def violation(name, value, limit=100, typ="APPARENT_POWER"):
    return {"equipment_id": name, "limit_type": typ,
            "limit_name": "RateC", "value": value, "limit": limit}


def test_risk_rejects_new_and_worsened_violations():
    before = [violation("A", 110)]
    safer = evaluate_violation_risk(before, [violation("A", 105)],
                                    "CONVERGED", "CONVERGED")
    assert safer["recommendation_eligible"]
    new = evaluate_violation_risk(
        before, [violation("A", 104), violation("B", 105)],
        "CONVERGED", "CONVERGED"
    )
    assert "NEW_VIOLATION" in new["rejection_reasons"]
    worse = evaluate_violation_risk(
        before, [violation("A", 111)], "CONVERGED", "CONVERGED"
    )
    assert "EXISTING_VIOLATION_WORSENED" in worse["rejection_reasons"]


def test_risk_unknown_type_cannot_be_recommended():
    result = evaluate_violation_risk(
        [violation("A", 110)], [violation("A", 105, typ="UNKNOWN")],
        "CONVERGED", "CONVERGED"
    )
    assert not result["recommendation_eligible"]
    assert "UNQUANTIFIED_VIOLATION" in result["rejection_reasons"]


def test_rank_prefers_global_reduction():
    def result(excess, rank):
        return {
            "rank": rank, "redispatch": {"delta_mw": 10},
            "ac_validation": {
                "after_redispatch": {"converged": True},
                "whole_network_validation": {"operator_strategy_status": "CONVERGED"},
                "improvement_mva": 10,
            },
            "risk_assessment": {
                "recommendation_eligible": True,
                "risk_metrics": {"max_normalized_excess": excess,
                                 "total_normalized_excess": excess,
                                 "remaining_violation_count": 1},
            },
        }
    assert sorted([result(0.05, 1), result(0.02, 2)],
                  key=adaptive_candidate_sort_key)[0]["rank"] == 2

def test_two_terminal_estimate_handles_p2_dominant_overload():
    before = {"p1_mw": -95.0, "q1_mvar": 0.0,
              "p2_mw": 120.0, "q2_mvar": 0.0}
    probe = {"p1_mw": -92.0, "q1_mvar": 0.0,
             "p2_mw": 115.0, "q2_mvar": 0.0}
    estimate = estimate_two_end_redispatch_delta(
        before, probe, probe_delta_mw=10.0,
        limit_mva=100.0, max_delta_mw=50.0
    )
    assert estimate is not None
    assert estimate["estimated_delta_mw"] == pytest.approx(40.0)
    assert not estimate["estimate_clipped_by_headroom"]


def test_two_terminal_estimate_records_unreachable_limit():
    before = {"p1_mw": -95.0, "q1_mvar": 0.0,
              "p2_mw": 120.0, "q2_mvar": 0.0}
    probe = {"p1_mw": -92.0, "q1_mvar": 0.0,
             "p2_mw": 115.0, "q2_mvar": 0.0}
    estimate = estimate_two_end_redispatch_delta(
        before, probe, 10.0, 100.0, 25.0
    )
    assert estimate is not None
    assert estimate["estimated_delta_mw"] == pytest.approx(25.0)
    assert estimate["unclamped_estimated_delta_mw"] == pytest.approx(40.0)
    assert estimate["estimate_clipped_by_headroom"]


def test_two_terminal_estimate_requires_all_overloaded_ends_to_clear():
    before = {"p1_mw": -120.0, "q1_mvar": 0.0,
              "p2_mw": 130.0, "q2_mvar": 0.0}
    probe = {"p1_mw": -115.0, "q1_mvar": 0.0,
             "p2_mw": 125.0, "q2_mvar": 0.0}
    estimate = estimate_two_end_redispatch_delta(before, probe, 10, 100, 80)
    assert estimate is not None
    assert estimate["estimated_delta_mw"] == pytest.approx(60)


def test_two_terminal_estimate_falls_back_if_overload_increases():
    before = {"p1_mw": -95.0, "q1_mvar": 0.0,
              "p2_mw": 120.0, "q2_mvar": 0.0}
    probe = {"p1_mw": -92.0, "q1_mvar": 0.0,
             "p2_mw": 125.0, "q2_mvar": 0.0}
    assert estimate_two_end_redispatch_delta(
        before, probe, 10, 100, 80
    ) is None


def test_candidate_budget_keeps_bound_with_five_points():
    result = generate_delta_candidates(None, 36.88791733190601, max_candidates=5)
    assert len(result) == 5
    assert result[0] == pytest.approx(10.0)
    assert max(result) == pytest.approx(36.88791733190601)


def test_candidate_budget_keeps_125_percent_estimate_if_possible():
    result = generate_delta_candidates(20, 40, max_candidates=5)
    assert 25.0 in result
    assert 40.0 in result
    assert 20.0 in result
    assert 10.0 in result


def test_two_terminal_probe_is_used_for_p2_dominant_line():
    generators = {
        "UP": state(actual=50, target=50, bus="UP-BUS"),
        "DOWN": state(actual=50, target=50, bus="DOWN-BUS"),
    }
    calls = []
    def probe(up, down, delta):
        calls.append((up, down, delta))
        return {"p1_mw": -95 + 0.3 * delta, "q1_mvar": 0,
                "p2_mw": 120 - 0.5 * delta, "q2_mvar": 0}
    candidates = build_adaptive_redispatch_candidates(
        generators, {"UP": -0.7, "DOWN": 0.1},
        -95, 0, 120, 0, 100,
        top_n=1, max_deltas_per_pair=5, flow_probe=probe
    )
    assert calls
    assert candidates
    assert all(c["estimation_method"] == "AC_TWO_END_FINITE_DIFFERENCE"
               for c in candidates)
    assert candidates[0]["estimated_delta_mw"] == pytest.approx(40.0)
