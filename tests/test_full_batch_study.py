from pathlib import Path

import pandas as pd

from studies.kpg193_full_batch.core import (
    classify_result,
    compare_with_legacy,
    rank_contingencies,
    summarize_violations,
)
from studies.kpg193_full_batch.reporting import build_html_report


def test_classification_uses_connectivity_component_creation():
    assert classify_result("CONVERGED", 0) == "CONVERGED_CLEAN"
    assert classify_result("CONVERGED", 2) == "CONVERGED_VIOLATION"
    assert classify_result("FAILED", 0) == "NON_CONVERGED"
    assert (
        classify_result(
            "CONVERGED",
            0,
            {"created_connected_component_count": 1},
        )
        == "ISLANDED"
    )
    assert classify_result(None, 0, error="boom") == "EXECUTION_ERROR"


def test_requested_outage_in_disconnected_elements_is_not_islanding():
    assert (
        classify_result(
            "CONVERGED",
            0,
            {"disconnected_element_ids": ["LINE-1-2"]},
        )
        == "CONVERGED_CLEAN"
    )


def test_additional_disconnected_elements_mean_islanding():
    assert (
        classify_result(
            "CONVERGED",
            0,
            {
                "disconnected_element_ids": ["LINE-1-2", "LOAD-2"],
                "additional_disconnected_element_ids": ["LOAD-2"],
            },
        )
        == "ISLANDED"
    )


def test_no_impact_is_a_successful_clean_result():
    assert classify_result("NO_IMPACT", 0) == "CONVERGED_CLEAN"


def test_ranking_is_physics_first_and_deterministic():
    rows = [
        {
            "contingency_id": "B",
            "classification": "CONVERGED_VIOLATION",
            "violation_count": 1,
            "max_loading_percent": 120.0,
        },
        {
            "contingency_id": "A",
            "classification": "CONVERGED_VIOLATION",
            "violation_count": 1,
            "max_loading_percent": 120.0,
        },
        {
            "contingency_id": "I",
            "classification": "ISLANDED",
            "violation_count": 0,
        },
        {
            "contingency_id": "E",
            "classification": "EXECUTION_ERROR",
            "violation_count": 0,
        },
    ]
    ranked = rank_contingencies(rows)
    assert [row["contingency_id"] for row in ranked] == ["I", "A", "B", "E"]
    assert ranked[-1]["review_rank"] is None


def test_violation_grouping_keeps_equipment_level_severity():
    grouped = summarize_violations(
        [
            {
                "subject_id": "LINE-1-2",
                "limit_type": "APPARENT_POWER",
                "limit_name": "permanent",
                "limit": 100.0,
                "value": 120.0,
                "side": "ONE",
            },
            {
                "subject_id": "LINE-1-2",
                "limit_type": "APPARENT_POWER",
                "limit_name": "permanent",
                "limit": 100.0,
                "value": 110.0,
                "side": "TWO",
            },
        ]
    )
    assert len(grouped) == 1
    assert grouped[0]["loading_percent"] == 120.0
    assert grouped[0]["record_count"] == 2


def test_legacy_comparison_maps_parallel_lines_by_occurrence():
    study = [
        {
            "element_type": "LINE",
            "element_id": "LINE-1-2#1",
            "contingency_id": "LINE_OUT::LINE-1-2#1",
            "from_bus": 1,
            "to_bus": 2,
            "classification": "CONVERGED_CLEAN",
            "raw_status": "CONVERGED",
        },
        {
            "element_type": "LINE",
            "element_id": "LINE-1-2#0",
            "contingency_id": "LINE_OUT::LINE-1-2#0",
            "from_bus": 1,
            "to_bus": 2,
            "classification": "CONVERGED_VIOLATION",
            "raw_status": "CONVERGED",
        },
    ]
    legacy = pd.DataFrame(
        [
            {
                "outage_branch": 1,
                "outage_from_bus": 1,
                "outage_to_bus": 2,
                "status": "LIMIT_VIOLATION",
                "converged": True,
            },
            {
                "outage_branch": 2,
                "outage_from_bus": 2,
                "outage_to_bus": 1,
                "status": "OK",
                "converged": True,
            },
        ]
    )
    comparison, mapping = compare_with_legacy(study, legacy)
    assert list(mapping["element_id"]) == ["LINE-1-2#0", "LINE-1-2#1"]
    assert comparison["classification_match"].all()


def test_html_report_is_self_contained():
    summary = pd.DataFrame(
        [
            {
                "review_priority": 3,
                "review_rank": 1,
                "contingency_id": "LINE_OUT::LINE-1-2",
                "classification": "CONVERGED_CLEAN",
            }
        ]
    )
    rendered = build_html_report(
        {"created_at_utc": "now", "pypowsybl_version": "1.16.1"},
        summary,
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
    )
    assert "KPG-193 Full Batch Security Study" in rendered
    assert "CONVERGED_CLEAN" in rendered
