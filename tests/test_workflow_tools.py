from ai_ems.tools import workflow_tools


def test_contingency_response_stops_when_security_does_not_converge(
    monkeypatch,
) -> None:
    security_result = {
        "pre_status": "CONVERGED",
        "post_status": "MAX_ITERATION_REACHED",
        "pre_violated_equipment_count": 0,
        "violated_equipment_count": 0,
        "violation_comparison": {
            "new_count": 0,
            "resolved_count": 0,
            "remaining_count": 0,
            "new": [],
            "resolved": [],
            "remaining": [],
        },
    }

    monkeypatch.setattr(
        workflow_tools,
        "load_network",
        lambda case_path: object(),
    )
    monkeypatch.setattr(
        workflow_tools,
        "run_line_contingency",
        lambda network, outage_line_id: security_result,
    )

    result = workflow_tools.analyze_contingency_response(
        case_path="dummy.mat",
        outage_line_id="LINE-28-80",
    )

    assert result["analysis_status"] == "SECURITY_NOT_CONVERGED"
    assert result["outage_line_id"] == "LINE-28-80"
    assert result["monitored_line_id"] is None
    assert result["candidate_count"] == 0
    assert result["candidates"] == []
    assert result["best_tested_candidate"] is None
    assert result["initial_security"]["post_status"] == "MAX_ITERATION_REACHED"
