from typing import Any


def format_contingency_response(result: dict[str, Any]) -> str:
    outage_line_id = result["outage_line_id"]
    initial_security = result.get("initial_security")

    if result.get("analysis_status") == "SECURITY_NOT_CONVERGED":
        post_status = (
            initial_security.get("post_status")
            if initial_security is not None
            else "UNKNOWN"
        )

        return "\n".join(
            [
                f"{outage_line_id} 사고의 상정사고 분석이 수렴하지 않았습니다.",
                "",
                f"- 해석 상태: {post_status}",
                "- 사고 후 조류계산이 수렴하지 않아 위반 여부를 판정할 수 없습니다.",
                "- 따라서 과부하 선로 선택, Sensitivity Analysis 및 Redispatch 후보 검토를 수행하지 않았습니다.",
                "- 주의: 이는 위반이 없다는 의미가 아니라 해석 결과를 얻지 못했다는 의미입니다.",
            ]
        )

    monitored_line_id = result["monitored_line_id"]
    candidate_count = result["candidate_count"]
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
            lines.append(
                "- 사고 후 발생한 위반 설비는 재검증 결과 모두 해소되었습니다."
            )
    else:
        lines.append("- 전체 계통의 신규 위반 여부는 별도 검증이 필요합니다.")

    return "\n".join(lines)


def format_generator_contingency_response(result: dict[str, Any]) -> str:
    generator_id = result["generator_id"]
    slack_mode = result["slack_mode"]
    outage_generator = result["outage_generator"]
    post_loadflow = result["post_loadflow"]
    security = result["security"]

    lines = [
        f"{generator_id} 발전기 탈락 분석 결과입니다.",
        "",
        (
            f"- AC Load Flow 수렴: 사고 전 {result['base_converged']} / "
            f"사고 후 {result['post_contingency_converged']}"
        ),
    ]

    outage_p = outage_generator.get("actual_generation_mw")
    if outage_p is not None:
        lines.append(f"- 탈락 전 발전 출력: {outage_p:.2f} MW")

    components = post_loadflow.get("components", [])
    reference_bus_id = components[0].get("reference_bus_id") if components else None
    distributed_mw = post_loadflow.get("distributed_active_power_mw")
    mismatch_mw = post_loadflow.get("active_power_mismatch_mw")

    if slack_mode == "distributed":
        lines.append("- Load Flow balancing 가정: 분산 슬랙")
        if result.get("balance_type") is not None:
            lines.append(f"- 분산 기준: {result['balance_type']}")
        if distributed_mw is not None:
            lines.append(f"- 분산 슬랙 보상량: {distributed_mw:.2f} MW")
        if mismatch_mw is not None:
            lines.append(f"- 잔여 유효전력 mismatch: {mismatch_mw:.2f} MW")

        changed_generators = [
            item
            for item in result.get("top_generator_changes", [])
            if abs(item.get("delta_generation_mw") or 0.0) > 1e-6
        ]
        if changed_generators:
            lines.append("- Load Flow balancing에 따른 주요 발전기 출력 변화:")
            for item in changed_generators:
                before = item.get("generation_before_mw")
                after = item.get("generation_after_mw")
                delta = item.get("delta_generation_mw")
                if before is None or after is None or delta is None:
                    continue
                lines.append(
                    f"  - {item['generator_id']}: {before:.2f} MW → "
                    f"{after:.2f} MW ({delta:+.2f} MW)"
                )
        else:
            lines.append("- 생존 발전기의 유의미한 출력 변화는 확인되지 않았습니다.")
    else:
        lines.append("- Load Flow balancing 가정: 단일 슬랙")
        if distributed_mw is not None:
            lines.append(f"- 분산 슬랙 보상량: {distributed_mw:.2f} MW")
        if mismatch_mw is not None:
            lines.append(f"- 사고 후 유효전력 mismatch: {mismatch_mw:.2f} MW")
        if reference_bus_id is not None:
            lines.append(f"- reference/slack bus: {reference_bus_id}")
        if mismatch_mw is not None:
            lines.append(
                "- 이 mismatch는 단일 슬랙 Load Flow balancing 가정에서 보고된 값이며, "
                f"특정 발전기가 실제로 {mismatch_mw:.2f} MW를 공급했다는 의미가 아닙니다."
            )

        top_changes = result.get("top_generator_changes", [])
        nonzero_changes = [
            item
            for item in top_changes
            if abs(item.get("delta_generation_mw") or 0.0) > 1e-6
        ]
        if nonzero_changes:
            lines.append("- 일부 생존 발전기의 계산된 출력 변화가 확인되었습니다.")
        elif top_changes:
            lines.append("- 생존 발전기 출력 변화: 상위 비교 기록에서 모두 0.00 MW")

    pre_count = security.get("pre_violated_equipment_count", 0)
    post_count = security.get("post_violated_equipment_count", 0)
    lines.append(
        f"- Security Analysis 위반 설비: 사고 전 {pre_count}개 / 사고 후 {post_count}개"
    )

    major_overloads = result.get("major_overloads", [])
    if major_overloads:
        lines.append("- 주요 사고 후 과부하:")
        for item in major_overloads:
            equipment_id = item["equipment_id"]
            unit = item.get("unit") or ""
            value = item.get("value")
            limit = item.get("limit")
            loading = item.get("loading_percent")
            violation_amount = item.get("violation_amount")

            parts = [f"  - {equipment_id}"]
            if value is not None and limit is not None:
                parts.append(f"{value:.2f} {unit} / 한계 {limit:.2f} {unit}")
            if loading is not None:
                parts.append(f"부하율 {loading:.2f}%")
            if violation_amount is not None:
                parts.append(f"한계 초과 {violation_amount:.2f} {unit}")
            lines.append(" | ".join(parts))
    else:
        lines.append("- 주요 사고 후 선로 과부하는 확인되지 않았습니다.")

    loss_change = result.get("loss_change", {})
    line_loss_change = loss_change.get("line_active_power_loss_change_mw")
    if line_loss_change is not None:
        if line_loss_change >= 0:
            lines.append(
                f"- 선로 유효전력 손실 변화: 약 {line_loss_change:.2f} MW 증가"
            )
        else:
            lines.append(
                f"- 선로 유효전력 손실 변화: 약 {abs(line_loss_change):.2f} MW 감소"
            )

    lines.extend(
        [
            "- 주의: Single/Distributed slack은 Load Flow balancing 가정이며 운영자 Redispatch가 아닙니다.",
            "- 이 결과는 정적 AC Load Flow / Security Analysis 결과이며 동특성 안정도를 검증한 것은 아닙니다.",
        ]
    )

    if post_count > 0:
        lines.append(
            "- 사고 후 위반 설비가 있으므로 실제 교정제어가 필요하다면 별도 후보 생성과 물리해석 재검증이 필요합니다."
        )

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
        new_ids = ", ".join(item["equipment_id"] for item in new_violations)
        lines.append(f"- 사고로 새로 발생한 위반 설비: {new_ids}")

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
