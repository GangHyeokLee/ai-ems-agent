from typing import Any

from .common import format_mw, format_signed_change


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
        lines.append(f"- 탈락 전 발전 출력: {format_mw(outage_p)}")

    components = post_loadflow.get("components", [])
    reference_bus_id = components[0].get("reference_bus_id") if components else None
    distributed_mw = post_loadflow.get("distributed_active_power_mw")
    mismatch_mw = post_loadflow.get("active_power_mismatch_mw")

    if slack_mode == "distributed":
        lines.append("- Load Flow balancing 가정: 분산 슬랙")
        if result.get("balance_type") is not None:
            lines.append(f"- 분산 기준: {result['balance_type']}")
        if distributed_mw is not None:
            lines.append(f"- 분산 슬랙 보상량: {format_mw(distributed_mw)}")
        if mismatch_mw is not None:
            lines.append(f"- 잔여 유효전력 mismatch: {format_mw(mismatch_mw)}")

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
            lines.append(f"- 분산 슬랙 보상량: {format_mw(distributed_mw)}")
        if mismatch_mw is not None:
            lines.append(f"- 사고 후 유효전력 mismatch: {format_mw(mismatch_mw)}")
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

    line_loss_change = result.get("loss_change", {}).get(
        "line_active_power_loss_change_mw"
    )
    if line_loss_change is not None:
        lines.append(
            f"- 선로 유효전력 손실 변화: {format_signed_change(line_loss_change)}"
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
