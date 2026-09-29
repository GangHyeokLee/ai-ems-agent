from typing import Any


def format_generator_outage_response(
    result: dict[str, Any],
) -> str:
    generator_id = result["outage_generator_id"]
    status = result.get("analysis_status")
    initial_security = result.get("initial_security", {})

    if status == "SECURITY_NOT_CONVERGED":
        return "\n".join(
            [
                f"## {generator_id} 발전기 사고 대응방안 분석",
                "",
                "- 사고 후 계통해석이 수렴하지 않아 교정제어 후보를 평가할 수 없습니다.",
                (
                    "- Security Analysis 상태: "
                    f"{initial_security.get('post_status', 'UNKNOWN')}"
                ),
                "- 따라서 민감도 분석, Redispatch 후보 생성 및 제어 효과 검증을 수행하지 않았습니다.",
                "- 주의: 이는 계통에 문제가 없다는 의미가 아니라 해석 결과를 얻지 못했다는 의미입니다.",
            ]
        )

    if status == "NO_OVERLOADED_LINE":
        return "\n".join(
            [
                f"## {generator_id} 발전기 사고 대응방안 분석",
                "",
                "- 사고 후 AC Load Flow / Security Analysis는 수렴했습니다.",
                "- 사고 후 교정제어 대상으로 선택할 과부하 선로가 확인되지 않았습니다.",
                "- 따라서 추가 Redispatch 후보 검토는 수행하지 않았습니다.",
            ]
        )

    monitored_line_id = result.get("monitored_line_id")
    candidate_count = result.get("candidate_count", 0)
    best = result.get("best_tested_candidate")

    lines = [
        f"## {generator_id} 발전기 사고 대응방안 분석",
        "",
        f"- **Load Flow balancing 가정**: {result.get('slack_mode', '-')}",
    ]

    balance_type = result.get("balance_type")
    if balance_type is not None:
        lines.append(f"- **분산 기준**: {balance_type}")

    pre_count = initial_security.get(
        "pre_violated_equipment_count",
        0,
    )
    post_count = initial_security.get(
        "post_violated_equipment_count",
        0,
    )
    comparison = initial_security.get(
        "violation_comparison",
        {},
    )
    new_count = comparison.get("new_count", 0)

    lines.extend(
        [
            (
                "- **Security Analysis 위반 설비**: "
                f"사고 전 {pre_count}개 / 사고 후 {post_count}개"
            ),
            f"- **사고로 인한 신규 위반**: {new_count}개",
        ]
    )

    if monitored_line_id is not None:
        selection_text = (
            "가장 심한 과부하 자동 선택"
            if result.get("target_selection") == "most_severe_violation"
            else "사용자 지정"
        )
        lines.append(
            f"- **분석 대상 선로**: {monitored_line_id} ({selection_text})"
        )

    lines.append(f"- **검토한 Redispatch 후보**: {candidate_count}개")

    candidates = result.get("candidates", [])

    if candidates:
        lines.extend(
            [
                "",
                "### 후보별 물리해석 검증",
                "",
                (
                    "| 민감도 순위 | AC 검증 순위 | Redispatch | "
                    "민감도 예상 | 실제 MVA 개선 | 제어 후 부하율 |"
                ),
                "|---:|---:|---|---:|---:|---:|",
            ]
        )

        for item in candidates:
            redispatch = item["redispatch"]
            prediction = item["prediction"]
            validation = item["ac_validation"]

            predicted_reduction = prediction.get(
                "abs_p1_reduction_mw"
            )
            improvement_mva = validation.get(
                "improvement_mva"
            )
            loading_after = validation.get(
                "loading_after_percent"
            )

            predicted_text = (
                f"{predicted_reduction:.2f} MW"
                if predicted_reduction is not None
                else "-"
            )
            improvement_text = (
                f"{improvement_mva:.2f} MVA"
                if improvement_mva is not None
                else "-"
            )
            loading_text = (
                f"{loading_after:.2f}%"
                if loading_after is not None
                else "-"
            )

            action_text = (
                f"{redispatch['up_generator_id']} +"
                f"{redispatch['delta_mw']:.1f} MW / "
                f"{redispatch['down_generator_id']} -"
                f"{redispatch['delta_mw']:.1f} MW"
            )

            lines.append(
                "| "
                f"{item.get('rank', '-')} | "
                f"{item.get('ac_validation_rank', '-')} | "
                f"{action_text} | "
                f"{predicted_text} | "
                f"{improvement_text} | "
                f"{loading_text} |"
            )

    if best is None:
        lines.extend(
            [
                "",
                "### 결과",
                "",
                "- 검토 가능한 Redispatch 후보를 찾지 못했습니다.",
            ]
        )
        return "\n".join(lines)

    redispatch = best["redispatch"]
    validation = best["ac_validation"]
    after = validation["after_redispatch"]

    lines.extend(
        [
            "",
            "### 시험 후보 중 최선의 결과",
            "",
            (
                f"- **Redispatch**: "
                f"{redispatch['up_generator_id']} "
                f"+{redispatch['delta_mw']:.1f} MW / "
                f"{redispatch['down_generator_id']} "
                f"-{redispatch['delta_mw']:.1f} MW"
            ),
        ]
    )

    prediction = best.get("prediction", {})
    predicted_reduction = prediction.get(
        "abs_p1_reduction_mw"
    )
    if predicted_reduction is not None:
        lines.append(
            "- **민감도 기반 예상**: "
            f"선로 유효전력 조류 절댓값 약 "
            f"{predicted_reduction:.2f} MW 감소"
        )

    if not after.get("converged"):
        lines.extend(
            [
                "- AC Load Flow가 수렴하지 않아 해당 후보의 실제 개선 효과를 판정할 수 없습니다.",
                "- 다른 후보 또는 제어량을 시험한 뒤 다시 물리해석으로 검증해야 합니다.",
                "- 현재 결과는 최적 Redispatch를 의미하지 않습니다.",
            ]
        )
        return "\n".join(lines)

    before_mva = validation["post_contingency"].get(
        "apparent_power_mva"
    )
    after_mva = after.get("apparent_power_mva")
    improvement_mva = validation.get("improvement_mva")

    if (
        before_mva is not None
        and after_mva is not None
        and improvement_mva is not None
    ):
        if improvement_mva >= 0:
            lines.append(
                "- **AC 재검증 피상전력**: "
                f"{before_mva:.2f} MVA → "
                f"{after_mva:.2f} MVA "
                f"(약 {improvement_mva:.2f} MVA 감소)"
            )
        else:
            lines.append(
                "- **AC 재검증 피상전력**: "
                f"{before_mva:.2f} MVA → "
                f"{after_mva:.2f} MVA "
                f"(약 {abs(improvement_mva):.2f} MVA 증가)"
            )

    before_loading = validation.get(
        "loading_before_percent"
    )
    after_loading = validation.get(
        "loading_after_percent"
    )

    if (
        before_loading is not None
        and after_loading is not None
    ):
        change = before_loading - after_loading

        if change >= 0:
            change_text = f"{change:.2f}%p 감소"
        else:
            change_text = (
                f"{abs(change):.2f}%p 증가"
            )

        lines.append(
            "- **부하율**: "
            f"{before_loading:.2f}% → "
            f"{after_loading:.2f}% "
            f"({change_text})"
        )

    if validation.get("violation_remaining") is True:
        lines.append(
            "- **결과**: 과부하는 완화되었지만 "
            "설비 한계 위반은 여전히 남아 있습니다."
        )
    elif validation.get("violation_remaining") is False:
        lines.append(
            "- **결과**: 시험한 Redispatch에서 "
            "해당 선로의 과부하가 해소되었습니다."
        )
    else:
        lines.append(
            "- **결과**: 설비 한계 정보가 없어 "
            "위반 해소 여부를 판정할 수 없습니다."
        )

    whole = validation.get("whole_network_validation")

    if whole is not None:
        lines.extend(
            [
                "",
                "### 전체 계통 Security 재검증",
                "",
            ]
        )

        if whole.get("operator_strategy_status") != "CONVERGED":
            lines.append(
                "- Operator Strategy 계산이 수렴하지 않아 "
                "전체 계통 영향을 판정할 수 없습니다."
            )
        else:
            before_count = whole.get(
                "violated_equipment_count_before",
                0,
            )
            after_count = whole.get(
                "violated_equipment_count_after",
                0,
            )

            lines.append(
                "- 위반 설비: "
                f"제어 전 {before_count}개 / "
                f"제어 후 {after_count}개"
            )

            if whole.get("new_violation_detected"):
                new_ids = ", ".join(
                    item["equipment_id"]
                    for item in whole.get(
                        "new_violations",
                        [],
                    )
                )
                lines.append(
                    "- Redispatch 이후 신규 위반이 "
                    f"확인되었습니다: {new_ids}"
                )
            else:
                lines.append(
                    "- Redispatch로 인해 새로 발생한 "
                    "위반 설비는 확인되지 않았습니다."
                )

            remaining = whole.get(
                "remaining_violations",
                [],
            )

            if remaining:
                remaining_ids = ", ".join(
                    item["equipment_id"]
                    for item in remaining
                )
                lines.append(
                    "- 제어 후에도 남아 있는 위반 설비: "
                    f"{remaining_ids}"
                )
            else:
                lines.append(
                    "- 사고 후 위반 설비는 "
                    "제어 후 모두 해소되었습니다."
                )

    lines.extend(
        [
            "",
            "### 해석 주의사항",
            "",
            "- 민감도 결과는 Redispatch 후보를 선별하기 위한 국소 선형 영향도이며 최적화 결과가 아닙니다.",
            "- 민감도 예측은 선로 유효전력 조류의 MW 변화이고, AC 재검증 결과는 피상전력 MVA이므로 두 값을 동일한 물리량처럼 직접 비교하지 않습니다.",
            "- 위 Redispatch는 시험한 후보 중 최선의 결과이며 OPF/SCED 최적해를 의미하지 않습니다.",
            "- Single/Distributed slack은 Load Flow balancing 가정이고, 위 Redispatch는 별도로 명시적으로 적용한 교정제어입니다.",
            "- 이 결과는 정적 AC Load Flow / Security Analysis 결과이며 동특성 안정도를 검증한 것은 아닙니다.",
        ]
    )

    return "\n".join(lines)