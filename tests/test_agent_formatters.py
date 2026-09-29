from ai_ems.agent.formatters import (
    format_contingency_response,
    format_generator_contingency_response,
)


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
                "improved": True,
                "loading_before_percent": 103.21488904853693,
                "loading_after_percent": 102.81412991530779,
                "violation_remaining": True,
            },
        },
    }


def _generator_contingency_result(slack_mode: str) -> dict:
    distributed = slack_mode == "distributed"

    return {
        "analysis_type": "Generator Contingency Analysis",
        "generator_id": "GEN-124#1",
        "contingency_id": "GEN_OUT_GEN-124#1",
        "slack_mode": slack_mode,
        "balance_type": (
            "PROPORTIONAL_TO_GENERATION_P_MAX" if distributed else None
        ),
        "outage_generator": {
            "generator_id": "GEN-124#1",
            "actual_generation_mw": 984.3288325675776,
        },
        "base_converged": True,
        "post_contingency_converged": True,
        "post_loadflow": {
            "components": [
                {
                    "reference_bus_id": "VL-190_0",
                }
            ],
            "distributed_active_power_mw": (
                969.1795744821459 if distributed else 0.0
            ),
            "active_power_mismatch_mw": (
                0.4325424305619663 if distributed else 993.5141963911412
            ),
        },
        "loss_change": {
            "line_active_power_loss_change_mw": (
                -14.711273135987994 if distributed else 9.205234622448984
            ),
        },
        "top_generator_changes": (
            [
                {
                    "generator_id": "GEN-82#5",
                    "generation_before_mw": 1378.2597181169867,
                    "generation_after_mw": 1397.685958251739,
                    "delta_generation_mw": 19.426240134752334,
                },
                {
                    "generator_id": "GEN-75#1",
                    "generation_before_mw": 988.2000450120529,
                    "generation_after_mw": 1002.7697251131173,
                    "delta_generation_mw": 14.569680101064364,
                },
            ]
            if distributed
            else [
                {
                    "generator_id": "GEN-10#0",
                    "generation_before_mw": 278.5717188472344,
                    "generation_after_mw": 278.5717188472344,
                    "delta_generation_mw": 0.0,
                }
            ]
        ),
        "major_overloads": [
            {
                "equipment_id": "LINE-134-193",
                "unit": "MVA",
                "limit": 2172.0,
                "value": 2293.707612786606 if distributed else 2371.3426860781724,
                "loading_percent": (
                    105.60348125168537 if distributed else 109.17784005884772
                ),
                "violation_amount": (
                    121.7076127866062 if distributed else 199.3426860781724
                ),
            }
        ],
        "security": {
            "pre_violated_equipment_count": 0,
            "post_violated_equipment_count": 1,
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


def test_format_contingency_response_handles_worsening_candidate() -> None:
    result = _base_result()
    validation = result["best_tested_candidate"]["ac_validation"]
    validation["after_redispatch"]["apparent_power_mva"] = 1972.0
    validation["improvement_mva"] = -4.7242147348866
    validation["improved"] = False
    validation["loading_after_percent"] = 103.46
    validation["violation_remaining"] = True

    text = format_contingency_response(result)

    assert "약 4.72 MVA 증가" in text
    assert "약 0.25%p 증가" in text
    assert "해당 선로 부하가 오히려 증가했고 위반도 남아 있습니다." in text
    assert "과부하는 완화되었지만" not in text


def test_format_contingency_response_distinguishes_contingency_and_redispatch_violations() -> (
    None
):
    result = _base_result()
    result["outage_line_id"] = "LINE-81-84"
    result["monitored_line_id"] = "LINE-16-28"
    result["initial_security"] = {
        "pre_status": "CONVERGED",
        "post_status": "CONVERGED",
        "pre_violated_equipment_count": 0,
        "post_violated_equipment_count": 2,
        "violation_comparison": {
            "new_count": 2,
            "resolved_count": 0,
            "remaining_count": 0,
            "new": [
                {"equipment_id": "LINE-134-193"},
                {"equipment_id": "LINE-16-28"},
            ],
            "resolved": [],
            "remaining": [],
        },
    }
    result["best_tested_candidate"]["ac_validation"]["whole_network_validation"] = {
        "operator_strategy_status": "CONVERGED",
        "new_violation_detected": False,
        "new_violations": [],
        "remaining_violations": [
            {"equipment_id": "LINE-134-193"},
            {"equipment_id": "LINE-16-28"},
        ],
    }

    text = format_contingency_response(result)

    assert (
        "사고 전 위반 설비: 0개 / 사고 후 위반 설비: 2개 / 사고로 인한 신규 위반: 2개"
        in text
    )
    assert "사고로 새로 발생한 위반 설비: LINE-134-193, LINE-16-28" in text
    assert "Redispatch로 인해 추가로 발생한 신규 위반은 확인되지 않았습니다." in text
    assert (
        "사고 후 발생한 위반 설비 중 제어 후에도 남아 있습니다: LINE-134-193, LINE-16-28"
        in text
    )
    assert "기존 위반 설비가 남아 있습니다" not in text


def test_format_contingency_response_handles_initial_security_nonconvergence() -> None:
    result = {
        "analysis_type": "Contingency Response Analysis",
        "analysis_status": "SECURITY_NOT_CONVERGED",
        "outage_line_id": "LINE-28-80",
        "monitored_line_id": None,
        "target_selection": "not_available",
        "delta_mw": 10.0,
        "candidate_count": 0,
        "candidates": [],
        "best_tested_candidate": None,
        "initial_security": {
            "pre_status": "CONVERGED",
            "post_status": "MAX_ITERATION_REACHED",
            "pre_violated_equipment_count": 0,
            "post_violated_equipment_count": 0,
            "violation_comparison": {
                "new_count": 0,
                "resolved_count": 0,
                "remaining_count": 0,
                "new": [],
                "resolved": [],
                "remaining": [],
            },
        },
    }

    text = format_contingency_response(result)

    assert "상정사고 분석이 수렴하지 않았습니다" in text
    assert "MAX_ITERATION_REACHED" in text
    assert "위반 여부를 판정할 수 없습니다" in text
    assert "Sensitivity Analysis 및 Redispatch 후보 검토를 수행하지 않았습니다" in text
    assert "위반이 없다는 의미가 아니라" in text


def test_format_generator_contingency_response_single_slack_is_deterministic() -> None:
    text = format_generator_contingency_response(
        _generator_contingency_result("single")
    )

    assert "탈락 전 발전 출력: 984.33 MW" in text
    assert "Load Flow balancing 가정: 단일 슬랙" in text
    assert "사고 후 유효전력 mismatch: 993.51 MW" in text
    assert "reference/slack bus: VL-190_0" in text
    assert "특정 발전기가 실제로 993.51 MW를 공급했다는 의미가 아닙니다." in text
    assert "생존 발전기 출력 변화: 상위 비교 기록에서 모두 0.00 MW" in text
    assert "2371.34 MVA / 한계 2172.00 MVA" in text
    assert "부하율 109.18%" in text
    assert "한계 초과 199.34 MVA" in text
    assert "선로 유효전력 손실 변화: 약 9.21 MW 증가" in text
    assert "운영자 Redispatch가 아닙니다" in text


def test_format_generator_contingency_response_distributed_slack_is_deterministic() -> None:
    text = format_generator_contingency_response(
        _generator_contingency_result("distributed")
    )

    assert "Load Flow balancing 가정: 분산 슬랙" in text
    assert "분산 기준: PROPORTIONAL_TO_GENERATION_P_MAX" in text
    assert "분산 슬랙 보상량: 969.18 MW" in text
    assert "잔여 유효전력 mismatch: 0.43 MW" in text
    assert "GEN-82#5: 1378.26 MW → 1397.69 MW (+19.43 MW)" in text
    assert "GEN-75#1: 988.20 MW → 1002.77 MW (+14.57 MW)" in text
    assert "2293.71 MVA / 한계 2172.00 MVA" in text
    assert "부하율 105.60%" in text
    assert "한계 초과 121.71 MVA" in text
    assert "선로 유효전력 손실 변화: 약 14.71 MW 감소" in text
    assert "운영자 Redispatch가 아닙니다" in text
