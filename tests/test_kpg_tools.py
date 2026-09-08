from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from ai_ems import (  # noqa: E402
    generate_redispatch_candidates,
    get_line,
    get_network_summary,
    list_generators,
    load_network,
    run_ac_load_flow,
    run_line_contingency,
    validate_balanced_redispatch,
)
from ai_ems.tools.workflow_tools import analyze_contingency_response


CASE_FILE = PROJECT_ROOT / "data" / "KPG193_ver2_0_pypowsybl.mat"


def test_kpg_network_and_loadflow() -> None:
    network = load_network(CASE_FILE)

    summary = get_network_summary(network)
    assert summary["buses"] == 193
    assert summary["lines"] == 385
    assert summary["generators"] == 205
    assert summary["nominal_voltages_kv"] == [154.0, 345.0, 765.0]

    loadflow = run_ac_load_flow(network)
    assert loadflow["converged"] is True
    assert loadflow["bus_voltage_pu"]["min"] > 0.95
    assert loadflow["bus_voltage_pu"]["max"] < 1.05


def test_kpg_line_and_generator_queries() -> None:
    network = load_network(CASE_FILE)
    run_ac_load_flow(network)

    line = get_line(network, "LINE-16-22")
    assert line["line_id"] == "LINE-16-22"
    assert "p1" in line
    assert "q1" in line

    generators = list_generators(network, limit=5)
    assert len(generators) == 5
    assert all("generator_id" in item for item in generators)


def test_kpg_security_known_violation() -> None:
    network = load_network(CASE_FILE)

    result = run_line_contingency(
        network,
        outage_line_id="LINE-16-28",
        monitored_line_ids=["LINE-16-22"],
    )

    assert result["base_converged"] is True
    assert result["post_status"] == "CONVERGED"
    assert result["violation_count"] == 2
    assert result["violated_equipment_count"] == 1

    equipment = result["violated_equipment"][0]
    assert equipment["equipment_id"] == "LINE-16-22"
    assert equipment["limit_type"] == "APPARENT_POWER"
    assert equipment["unit"] == "MVA"
    assert equipment["sides"] == ["ONE", "TWO"]
    assert equipment["value"] > equipment["limit"]
    assert equipment["violation_amount"] > 0.0
    assert equipment["violation_direction"] == "above_maximum"
    assert equipment["loading_percent"] > 100.0


def test_kpg_redispatch_candidate_generation() -> None:
    network = load_network(CASE_FILE)

    result = generate_redispatch_candidates(
        network,
        outage_line_id="LINE-16-28",
        monitored_line_id="LINE-16-22",
        delta_mw=10.0,
        top_n=3,
    )

    assert result["analysis_type"] == (
        "Sensitivity-based Redispatch Candidate Generation"
    )
    assert result["prediction_quantity"] == "ACTIVE_POWER"
    assert result["prediction_unit"] == "MW"
    assert result["candidate_count"] == 3
    assert result["post_contingency_p1_mw"] < 0.0

    previous_reduction = float("inf")
    for expected_rank, candidate in enumerate(result["candidates"], start=1):
        assert candidate["rank"] == expected_rank
        assert candidate["up_generator_id"] != candidate["down_generator_id"]
        assert candidate["net_requested_change_mw"] == 0.0
        assert candidate["predicted_abs_p1_reduction_mw"] > 0.0
        assert (
            candidate["predicted_abs_p1_reduction_mw"]
            <= previous_reduction
        )
        previous_reduction = candidate["predicted_abs_p1_reduction_mw"]

        assert candidate["up_target_after_mw"] == (
            candidate["up_target_before_mw"] + 10.0
        )
        assert candidate["down_target_after_mw"] == (
            candidate["down_target_before_mw"] - 10.0
        )


def test_kpg_balanced_redispatch_improves_known_violation() -> None:
    result = validate_balanced_redispatch(
        case_path=CASE_FILE,
        outage_line_id="LINE-16-28",
        monitored_line_id="LINE-16-22",
        up_generator_id="GEN-19#0",
        down_generator_id="GEN-36#0",
        delta_mw=10.0,
    )

    assert result["analysis_type"] == "Balanced Redispatch AC Validation"
    assert result["after_redispatch"]["converged"] is True
    assert result["redispatch"]["net_requested_change_mw"] == 0.0
    assert result["improved"] is True
    assert result["improvement_mva"] > 0.0
    assert result["loading_after_percent"] < result["loading_before_percent"]
    assert result["violation_remaining"] is True

def test_kpg_contingency_response_workflow() -> None:
    result = analyze_contingency_response(
        case_path = CASE_FILE,
        outage_line_id = "LINE-16-28",
        delta_mw = 10.0,
        top_n = 3,
    )

    assert result["analysis_type"] == "Contingency Response Analysis"
    assert result["monitored_line_id"] == "LINE-16-22"
    assert result["target_selection"] == "most_severe_violation"
    assert result["candidate_count"] == 3

    best = result["best_tested_candidate"]

    assert best is not None
    assert best["ac_validation_rank"] == 1
    assert best["ac_validation"]["after_redispatch"]["converged"] is True
    assert best["ac_validation"]["improved"] is True
    assert best["ac_validation"]["improvement_mva"] > 0.0