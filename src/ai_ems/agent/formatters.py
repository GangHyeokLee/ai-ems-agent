from typing import Any


def format_contingency_response(result: dict[str, Any]) -> str:
    outage_line_id = result["outage_line_id"]
    monitored_line_id = result["monitored_line_id"]
    candidate_count = result["candidate_count"]
    initial_security = result.get("initial_security")
    comparison = {}
    best = result["best_tested_candidate"]

    target_label = (
        "가장 심한 위반 선로(자동 선택)"
        if result.get("target_selection") == "most_severe_violation"
        else "분석 대상 선로"
    )

    if initial_security is not None:
        comparison = initial_security.get(
            "violation_comparison",
            {},
        )

    if best is None:
        lines = [
            f"{outage_line_id} 사고에 대한 대응방안 분석 결과입니다.",
            "",
            f"- {target_label}: {monitored_line_id}",
        ]
        _append_initial_security_summary(lines, initial_security, comparison)
        lines.append("- 검토 가능한 Redispatch 후보를 찾지 못했습니다.")
        return "\n".join(lines)

    redispatch = best["redispatch"]
    validation = best["ac_validation"]
    after_redispatch = validation["after_redispatch"]

    lines = [
        f"{outage_line_id} 사고에 대한 대응방안 분석 결과입니다.",
        "",
    ]

    _append_initial_security_summary(lines, initial_security, comparison)

    lines.extend(
        [
            f"- {target_label}: {monitored_line_id}",
            f"- 검토한 Redispatch 후보: {candidate_count}개",
            (
                f"- 시험 후보 중 1순위: "
                f"{redispatch['up_generator_id']} +{redispatch['delta_mw']:.1f} MW / "
                f"{redispatch['down_generator_id']} -{redispatch['delta_mw']:.1f} MW"
            ),
        ]
    )

    if not after_redispatch["converged"]:
        lines.extend(
            [
                "- AC 조류계산이 수렴하지 않아 해당 후보의 개선 효과를 확인할 수 없습니다.",
                "- 추가 후보를 생성하거나 제어 조건을 조정한 뒤 다시 물리해석으로 검증할 필요가 있습니다.",
                "- 주의: 현재 결과는 최적 Redispatch를 의미하지 않습니다.",
            ]
        )
        return "\n".join(lines)

    before_mva = validation["post_contingency"]["apparent_power_mva"]
    after_mva = after_redispatch["apparent_power_mva"]
    improvement_mva = validation["improvement_mva"]
    improved = validation.get("improved")
    before_loading = validation["loading_before_percent"]
    after_loading = validation["loading_after_percent"]

    if before_mva is not None and after_mva is not None and improvement_mva is not None:
        if improvement_mva >= 0:
            lines.append(
                f"- AC 조류계산 결과 피상전력: {before_mva:.2f} MVA → "
                f"{after_mva:.2f} MVA (약 {improvement_mva:.2f} MVA 감소)"
            )
        else:
            lines.append(
                f"- AC 조류계산 결과 피상전력: {before_mva:.2f} MVA → "
                f"{after_mva:.2f} MVA (약 {abs(improvement_mva):.2f} MVA 증가)"
            )

    if before_loading is not None and after_loading is not None:
        loading_change = before_loading - after_loading
        if loading_change >= 0:
            change_text = f"약 {loading_change:.2f}%p 감소"
        else:
            change_text = f"약 {abs(loading_change):.2f}%p 증가"
        lines.append(
            f"- 부하율: {before_loading:.2f}% → {after_loading:.2f}% "
            f"({change_text})"
        )

    if improved is False:
        if validation["violation_remaining"] is True:
            lines.append(
                "- 결과: 시험한 조치에서 해당 선로 부하가 오히려 증가했고 위반도 남아 있습니다."
            )
        else:
            lines.append(
                "- 결과: 시험한 조치에서 해당 선로 부하가 감소하지 않았습니다."
            )
    elif validation["violation_remaining"] is True:
        lines.extend(
            [
                "- 결과: 과부하는 완화되었지만 위반은 여전히 남아 있습니다.",
                "- 추가 제어 후보 또는 제어량을 생성한 뒤 다시 물리해석으로 검증할 필요가 있습니다.",
            ]
        )
    elif validation["violation_remaining"] is False:
        lines.append("- 결과: 시험한 조치에서 해당 선로의 과부하가 해소되었습니다.")
    else:
        lines.append(
            "- 결과: 설비 한계 정보가 없어 위반 해소 여부를 판정할 수 없습니다."
        )

    lines.append(
        "- 주의: 현재 결과는 시험한 후보 중 최선의 결과이며 최적 Redispatch를 의미하지 않습니다."
    )

    whole = validation.get("whole_network_validation")

    if whole is not None:
        if whole["operator_strategy_status"] != "CONVERGED":
            lines.append(
                "- 전체 계통 Security 재검증이 수렴하지 않아 계통 전체 영향은 판정할 수 없습니다."
            )
        elif whole["new_violation_detected"]:
            new_ids = ", ".join(
                item["equipment_id"] for item in whole["new_violations"]
            )
            lines.append(
                "- 전체 계통 Security 재검증 결과 Redispatch로 인해 추가로 발생한 "
                f"신규 위반이 확인되었습니다: {new_ids}"
            )
        else:
            lines.append(
                "- 전체 계통 Security 재검증 결과 Redispatch로 인해 추가로 발생한 "
                "신규 위반은 확인되지 않았습니다."
            )

        remaining = whole["remaining_violations"]

        if remaining:
            remaining_ids = ", ".join(item["equipment_id"] for item in remaining)
            lines.append(
                "- 사고 후 발생한 위반 설비 중 제어 후에도 남아 있습니다: "
                f"{remaining_ids}"
            )
        else:
            lines.append("- 사고 후 발생한 위반 설비는 재검증 결과 모두 해소되었습니다.")
    else:
        lines.append("- 전체 계통의 신규 위반 여부는 별도 검증이 필요합니다.")

    return "\n".join(lines)


def _append_initial_security_summary(
    lines: list[str],
    initial_security: dict[str, Any] | None,
    comparison: dict[str, Any],
) -> None:
    if initial_security is None:
        return

    pre_count = initial_security.get(
        "pre_violated_equipment_count",
        0,
    )
    post_count = initial_security.get(
        "post_violated_equipment_count",
        0,
    )
    new_count = comparison.get("new_count", 0)

    lines.append(
        f"- 사고 전 위반 설비: {pre_count}개 / "
        f"사고 후 위반 설비: {post_count}개 / "
        f"사고로 인한 신규 위반: {new_count}개"
    )

    new_violations = comparison.get("new", [])
    if new_violations:
        new_ids = ", ".join(
            item["equipment_id"]
            for item in new_violations
        )
        lines.append(
            f"- 사고로 새로 발생한 위반 설비: {new_ids}"
        )

    remaining = comparison.get("remaining", [])
    if remaining:
        worsened_ids = [
            item["after"]["equipment_id"]
            for item in remaining
            if item.get("trend") == "worsened"
        ]
        if worsened_ids:
            lines.append(
                "- 사고 전부터 존재했고 사고 후 악화된 위반 설비: "
                + ", ".join(worsened_ids)
            )
