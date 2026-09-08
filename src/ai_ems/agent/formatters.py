from typing import Any


def format_contingency_response(result: dict[str, Any]) -> str:
    outage_line_id = result["outage_line_id"]
    monitored_line_id = result["monitored_line_id"]
    candidate_count = result["candidate_count"]
    best = result["best_tested_candidate"]

    target_label = (
        "가장 심한 위반 선로(자동 선택)"
        if result.get("target_selection") == "most_severe_violation"
        else "분석 대상 선로"
    )

    if best is None:
        return (
            f"{outage_line_id} 사고에 대한 대응방안 분석 결과입니다.\n\n"
            f"- {target_label}: {monitored_line_id}\n"
            "- 검토 가능한 Redispatch 후보를 찾지 못했습니다."
        )

    redispatch = best["redispatch"]
    validation = best["ac_validation"]
    after_redispatch = validation["after_redispatch"]

    lines = [
        f"{outage_line_id} 사고에 대한 대응방안 분석 결과입니다.",
        "",
        f"- {target_label}: {monitored_line_id}",
        f"- 검토한 Redispatch 후보: {candidate_count}개",
        (
            f"- 시험 후보 중 1순위: "
            f"{redispatch['up_generator_id']} +{redispatch['delta_mw']:.1f} MW / "
            f"{redispatch['down_generator_id']} -{redispatch['delta_mw']:.1f} MW"
        ),
    ]

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
    before_loading = validation["loading_before_percent"]
    after_loading = validation["loading_after_percent"]

    if before_mva is not None and after_mva is not None and improvement_mva is not None:
        lines.append(
            f"- AC 조류계산 결과 피상전력: {before_mva:.2f} MVA → "
            f"{after_mva:.2f} MVA (약 {improvement_mva:.2f} MVA 감소)"
        )

    if before_loading is not None and after_loading is not None:
        loading_change = before_loading - after_loading
        lines.append(
            f"- 부하율: {before_loading:.2f}% → {after_loading:.2f}% "
            f"(약 {loading_change:.2f}%p 감소)"
        )

    if validation["violation_remaining"] is True:
        lines.extend(
            [
                "- 결과: 과부하는 완화되었지만 위반은 여전히 남아 있습니다.",
                "- 추가 제어 후보 또는 제어량을 생성한 뒤 다시 물리해석으로 검증할 필요가 있습니다.",
            ]
        )
    elif validation["violation_remaining"] is False:
        lines.append(
            "- 결과: 시험한 조치에서 해당 선로의 과부하가 해소되었습니다."
        )
    else:
        lines.append(
            "- 결과: 설비 한계 정보가 없어 위반 해소 여부를 판정할 수 없습니다."
        )

    lines.extend(
        [
            "- 주의: 현재 결과는 시험한 후보 중 최선의 결과이며 최적 Redispatch를 의미하지 않습니다.",
            "- 현재 검증은 지정된 대상 선로 기준이며, 전체 계통의 신규 위반 여부는 별도 검증이 필요합니다.",
        ]
    )

    return "\n".join(lines)
