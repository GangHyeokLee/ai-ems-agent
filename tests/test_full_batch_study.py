from pathlib import Path

import pandas as pd
import pypowsybl as pp
import pytest

from studies.kpg193_full_batch.core import (
    classify_result,
    compare_with_legacy,
    rank_contingencies,
    summarize_violations,
)
from studies.kpg193_full_batch.reporting import build_html_report
from studies.kpg193_full_batch.runner import (
    FullBatchStudy,
    StudyConfig,
)


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

def test_study_config_rejects_mixed_generator_modes():
    with pytest.raises(ValueError):
        StudyConfig(
            case_file=Path("case.mat"),
            output_dir=Path("output"),
            include_generators=True,
            generator_only=True,
        )


def test_distributed_slack_requires_generator_only():
    with pytest.raises(ValueError):
        StudyConfig(
            case_file=Path("case.mat"),
            output_dir=Path("output"),
            generator_slack="distributed",
        )


def test_generator_only_builds_only_generator_specs():
    study = FullBatchStudy.__new__(FullBatchStudy)
    study.config = StudyConfig(
        case_file=Path("case.mat"),
        output_dir=Path("output"),
        generator_only=True,
        generator_slack="distributed",
    )
    study.lines = pd.DataFrame(
        {"bus1_id": ["1"], "bus2_id": ["2"]},
        index=["LINE-1-2"],
    )
    study.generators = pd.DataFrame(
        {
            "connected": [True, False],
            "bus_id": ["BUS-1", "BUS-2"],
            "target_p": [100.0, 50.0],
            "max_p": [150.0, 80.0],
            "voltage_regulator_on": [True, False],
        },
        index=["GEN-1", "GEN-2"],
    )

    specs = study._build_specs()

    assert len(specs) == 1
    assert specs[0].element_type == "GENERATOR"
    assert specs[0].element_id == "GEN-1"
    assert specs[0].generator_bus_id == "BUS-1"
    assert specs[0].generator_target_p_mw == 100.0
    assert specs[0].generator_max_p_mw == 150.0


def test_generator_distributed_slack_parameters():
    study = FullBatchStudy.__new__(FullBatchStudy)
    study.config = StudyConfig(
        case_file=Path("case.mat"),
        output_dir=Path("output"),
        generator_only=True,
        generator_slack="distributed",
    )

    parameters = study._security_loadflow_parameters()

    assert parameters.distributed_slack is True
    assert (
        parameters.balance_type
        == pp.loadflow.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX
    )