import pytest
import json

from ai_ems.config import CASE_FILE
from ai_ems.tools.generator_contingency_tools import (
    analyze_generator_contingency,
    build_generator_loadflow_parameters,
)


def test_invalid_slack_mode():
    with pytest.raises(
        ValueError,
        match="Unsupported slack_mode",
    ):
        build_generator_loadflow_parameters(
            "invalid",
        )


def test_single_generator_contingency():
    result = analyze_generator_contingency(
        CASE_FILE,
        generator_id="GEN-124#1",
        slack_mode="single",
    )

    assert result["post_contingency_converged"]

    assert result["outage_generator"]["actual_generation_mw"] == pytest.approx(
        984.3288,
        abs=0.01,
    )

    post_loadflow = result["post_contingency"]["loadflow"]

    assert post_loadflow["distributed_active_power_mw"] == pytest.approx(
        0.0,
        abs=0.01,
    )

    assert post_loadflow["active_power_mismatch_mw"] == pytest.approx(
        993.514,
        abs=0.1,
    )

    overload = result["major_overloads"][0]

    assert overload["equipment_id"] == "LINE-134-193"

    assert overload["loading_percent"] == pytest.approx(
        109.178,
        abs=0.05,
    )


def test_distributed_generator_contingency():
    result = analyze_generator_contingency(
        CASE_FILE,
        generator_id="GEN-124#1",
        slack_mode="distributed",
    )

    post_loadflow = result["post_contingency"]["loadflow"]

    assert post_loadflow["distributed_active_power_mw"] == pytest.approx(
        969.180,
        abs=0.1,
    )

    assert post_loadflow["active_power_mismatch_mw"] == pytest.approx(
        0.433,
        abs=0.1,
    )

    overload = result["major_overloads"][0]

    assert overload["equipment_id"] == "LINE-134-193"

    assert overload["loading_percent"] == pytest.approx(
        105.603,
        abs=0.05,
    )

    changes = {item["generator_id"]: item for item in result["generator_changes"]}

    assert changes["GEN-82#5"]["delta_generation_mw"] == pytest.approx(
        19.426,
        abs=0.05,
    )


def test_single_slack_does_not_change_generator_outputs():
    result = analyze_generator_contingency(
        CASE_FILE,
        generator_id="GEN-124#1",
        slack_mode="single",
    )

    max_delta = max(
        abs(item["delta_generation_mw"])
        for item in result["generator_changes"]
        if item["delta_generation_mw"] is not None
    )

    assert max_delta == pytest.approx(
        0.0,
        abs=1e-6,
    )


def test_generator_contingency_result_is_json_serializable():
    result = analyze_generator_contingency(
        CASE_FILE,
        generator_id="GEN-124#1",
        slack_mode="distributed",
    )

    serialized = json.dumps(
        result,
        allow_nan=False,
    )

    assert serialized


def test_unknown_generator():
    with pytest.raises(
        ValueError,
        match="Unknown generator",
    ):
        analyze_generator_contingency(
            CASE_FILE,
            generator_id="GEN-NOT-EXIST",
        )


def test_invalid_balance_type():
    with pytest.raises(
        ValueError,
        match="Unsupported balance_type",
    ):
        analyze_generator_contingency(
            CASE_FILE,
            generator_id="GEN-124#1",
            slack_mode="distributed",
            balance_type="INVALID_BALANCE_TYPE",
        )


def test_invalid_top_n_overloads():
    with pytest.raises(
        ValueError,
        match="top_n_overloads must be greater than 0",
    ):
        analyze_generator_contingency(
            CASE_FILE,
            generator_id="GEN-124#1",
            top_n_overloads=0,
        )
