from ai_ems.agent.formatters import format_contingency_response


def _base_result() -> dict:
    return {
        "analysis_type": "Contingency Response Analysis",
        "outage_line_id": "LINE-16-28",
        "monitored_line_id": "LINE-16-22",
        "target_selection": "most_severe_violation",
        "candidate_count": 3,
        "best_tested_candidate": {
            "rank": 1,
            "redispatch": {
                "up_generator_id": "GEN-19#0",
                "down_generator_id": "GEN-36#0",
                "delta_mw": 10.0,
            },
            "ac_validation": {
                "post_contingency": {
                    "converged": True,
                    "apparent_power_mva": 1967.2757852651134,
                },
                "after_redispatch": {
                    "converged": True,
                    "apparent_power_mva": 1959.6373161857662,
                },
                "improvement_mva": 7.63846907934726,
                "loading_before_percent": 103.21488904853693,
                "loading_after_percent": 102.81412991530779,
                "violation_remaining": True,
            },
        },
    }


def test_format_contingency_response_uses_deterministic_physical_wording() -> None:
    text = format_contingency_response(_base_result())

    assert "가장 심한 위반 선로(자동 선택): LINE-16-22" in text
    assert "GEN-19#0 +10.0 MW / GEN-36#0 -10.0 MW" in text
    assert "1967.28 MVA → 1959.64 MVA" in text
    assert "약 7.64 MVA 감소" in text
    assert "103.21% → 102.81%" in text
    assert "약 0.40%p 감소" in text
    assert "과부하는 완화되었지만 위반은 여전히 남아 있습니다." in text
    assert "최적 Redispatch를 의미하지 않습니다." in text
    assert "전체 계통의 신규 위반 여부는 별도 검증이 필요합니다." in text


def test_format_contingency_response_handles_nonconvergence() -> None:
    result = _base_result()
    result["best_tested_candidate"]["ac_validation"]["after_redispatch"] = {
        "converged": False,
        "apparent_power_mva": None,
    }
    result["best_tested_candidate"]["ac_validation"]["improvement_mva"] = None
    result["best_tested_candidate"]["ac_validation"]["loading_after_percent"] = None
    result["best_tested_candidate"]["ac_validation"]["violation_remaining"] = None

    text = format_contingency_response(result)

    assert "AC 조류계산이 수렴하지 않아" in text
    assert "개선 효과를 확인할 수 없습니다." in text
