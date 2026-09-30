from ai_ems.agent.formatters import format_generator_risk_response
from ai_ems.tools import generator_risk_tools


def _screening_result(classification: str = "CONVERGED_VIOLATION") -> dict:
    return {
        "analysis_type": "Generator Contingency Screening",
        "slack_mode": "distributed",
        "total_contingencies": 100,
        "classification_counts": {
            "CONVERGED_CLEAN": 76,
            "CONVERGED_VIOLATION": 24,
        },
        "top_contingencies": [
            {
                "rank": 1,
                "generator_id": "GEN-124#1",
                "classification": classification,
                "primary_violation_equipment_id": "LINE-134-193",
                "max_loading_percent": 105.60,
            }
        ],
    }


def _response_result() -> dict:
    return {
        "analysis_type": "Generator Outage Response Analysis",
        "analysis_status": "NO_OVERLOADED_LINE",
        "outage_generator_id": "GEN-124#1",
        "initial_security": {"post_status": "CONVERGED"},
    }


def test_generator_risk_response_runs_screening_then_top_rank_response(
    monkeypatch,
) -> None:
    calls = {}

    def screening(**kwargs):
        calls["screening"] = kwargs
        return _screening_result()

    def response(**kwargs):
        calls["response"] = kwargs
        return _response_result()

    monkeypatch.setattr(
        generator_risk_tools,
        "run_generator_contingency_screening",
        screening,
    )
    monkeypatch.setattr(
        generator_risk_tools,
        "analyze_generator_outage_response",
        response,
    )

    result = generator_risk_tools.analyze_generator_risk_response(
        case_path="dummy.mat",
        slack_mode="distributed",
        screening_top_n=5,
        response_top_n=3,
        delta_mw=10.0,
    )

    assert result["analysis_type"] == "Generator Risk Response Analysis"
    assert result["analysis_status"] == "COMPLETED"
    assert result["selected_contingency"]["generator_id"] == "GEN-124#1"
    assert result["response"]["outage_generator_id"] == "GEN-124#1"
    assert calls["response"]["outage_generator_id"] == "GEN-124#1"
    assert calls["response"]["slack_mode"] == "distributed"
    assert calls["response"]["top_n"] == 3
    assert calls["response"]["delta_mw"] == 10.0


def test_generator_risk_response_does_not_run_redispatch_for_non_violation_top(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        generator_risk_tools,
        "run_generator_contingency_screening",
        lambda **kwargs: _screening_result("CONVERGED_CLEAN"),
    )
    monkeypatch.setattr(
        generator_risk_tools,
        "analyze_generator_outage_response",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("response analysis must not run")
        ),
    )

    result = generator_risk_tools.analyze_generator_risk_response(
        case_path="dummy.mat",
    )

    assert result["analysis_status"] == "TOP_CONTINGENCY_NOT_REDISPATCHABLE"
    assert result["response"] is None


def test_generator_risk_response_formatter_combines_screening_and_response() -> None:
    result = {
        "analysis_type": "Generator Risk Response Analysis",
        "analysis_status": "COMPLETED",
        "slack_mode": "distributed",
        "screening": _screening_result(),
        "selected_contingency": _screening_result()["top_contingencies"][0],
        "response": _response_result(),
    }

    text = format_generator_risk_response(result)

    assert "발전기 N-1 위험사고 및 대응방안 통합 분석" in text
    assert "발전기 N-1 Screening 결과" in text
    assert "GEN-124#1" in text
    assert "Screening 1위 발전기 사고" in text
    assert "GEN-124#1 발전기 사고 대응방안 분석" in text


def test_generator_risk_response_is_registered_for_deterministic_formatting() -> None:
    from ai_ems.agent.graph import DETERMINISTIC_FORMATTERS

    analysis_type, formatter = DETERMINISTIC_FORMATTERS[
        "generator_risk_response_analysis"
    ]

    assert analysis_type == "Generator Risk Response Analysis"
    assert formatter is format_generator_risk_response
