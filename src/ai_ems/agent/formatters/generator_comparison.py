from typing import Any

from .common import (
    format_mw,
    format_overload,
    format_signed_change,
    most_severe_overload,
)


def format_generator_contingency_comparison(
    result: dict[str, Any],
) -> str:
    generator_id = result["generator_id"]
    single = result["single"]
    distributed = result["distributed"]

    single_post = single.get("post_loadflow", {})
    distributed_post = distributed.get("post_loadflow", {})
    single_security = single.get("security", {})
    distributed_security = distributed.get("security", {})

    single_mismatch = single_post.get("active_power_mismatch_mw")
    distributed_mismatch = distributed_post.get("active_power_mismatch_mw")
    single_balancing = single_post.get("distributed_active_power_mw")
    distributed_balancing = distributed_post.get("distributed_active_power_mw")

    single_loss = single.get("loss_change", {}).get(
        "line_active_power_loss_change_mw"
    )
    distributed_loss = distributed.get("loss_change", {}).get(
        "line_active_power_loss_change_mw"
    )

    single_overload = most_severe_overload(single.get("major_overloads", []))
    distributed_overload = most_severe_overload(
        distributed.get("major_overloads", [])
    )

    outage_generation = single.get("outage_generator", {}).get(
        "actual_generation_mw"
    )

    lines = [
        f"{generator_id} 발전기 탈락의 단일 슬랙 / 분산 슬랙 비교 결과입니다.",
        "",
    ]

    if outage_generation is not None:
        lines.append(f"- 탈락 전 발전 출력: {outage_generation:.2f} MW")

    lines.extend(
        [
            "",
            "[단일 슬랙]",
            (
                f"- AC Load Flow 수렴: 사고 전 {single.get('base_converged')} / "
                f"사고 후 {single.get('post_contingency_converged')}"
            ),
            f"- 분산 슬랙 보상량: {format_mw(single_balancing)}",
            f"- 사고 후 유효전력 mismatch: {format_mw(single_mismatch)}",
            (
                "- Security Analysis 위반 설비: 사고 전 "
                f"{single_security.get('pre_violated_equipment_count', 0)}개 / "
                f"사고 후 {single_security.get('post_violated_equipment_count', 0)}개"
            ),
            f"- 주요 사고 후 과부하: {format_overload(single_overload)}",
            f"- 선로 유효전력 손실 변화: {format_signed_change(single_loss)}",
            "- 단일 슬랙의 mismatch는 Load Flow balancing 가정에서 보고된 값이며, 특정 발전기가 그 양을 실제로 공급했다는 의미가 아닙니다.",
            "",
            "[분산 슬랙]",
            (
                f"- AC Load Flow 수렴: 사고 전 {distributed.get('base_converged')} / "
                f"사고 후 {distributed.get('post_contingency_converged')}"
            ),
        ]
    )

    balance_type = distributed.get("balance_type")
    if balance_type is not None:
        lines.append(f"- 분산 기준: {balance_type}")

    lines.extend(
        [
            f"- 분산 슬랙 보상량: {format_mw(distributed_balancing)}",
            f"- 잔여 유효전력 mismatch: {format_mw(distributed_mismatch)}",
            (
                "- Security Analysis 위반 설비: 사고 전 "
                f"{distributed_security.get('pre_violated_equipment_count', 0)}개 / "
                f"사고 후 {distributed_security.get('post_violated_equipment_count', 0)}개"
            ),
            f"- 주요 사고 후 과부하: {format_overload(distributed_overload)}",
            f"- 선로 유효전력 손실 변화: {format_signed_change(distributed_loss)}",
        ]
    )

    changed_generators = [
        item
        for item in distributed.get("top_generator_changes", [])
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

    lines.extend(
        [
            "",
            "[비교]",
            (
                "- 잔여 mismatch: "
                f"단일 {format_mw(single_mismatch)} / "
                f"분산 {format_mw(distributed_mismatch)}"
            ),
            (
                "- 사고 후 위반 설비: "
                f"단일 {single_security.get('post_violated_equipment_count', 0)}개 / "
                f"분산 {distributed_security.get('post_violated_equipment_count', 0)}개"
            ),
        ]
    )

    if single_overload is not None and distributed_overload is not None:
        single_loading = single_overload.get("loading_percent")
        distributed_loading = distributed_overload.get("loading_percent")
        if single_loading is not None and distributed_loading is not None:
            difference = single_loading - distributed_loading
            lines.append(
                "- 최대 과부하 부하율: "
                f"단일 {single_loading:.2f}% / 분산 {distributed_loading:.2f}% "
                f"(분산 슬랙이 {abs(difference):.2f}%p "
                f"{'낮음' if difference >= 0 else '높음'})"
            )
    elif single_overload is None and distributed_overload is None:
        lines.append("- 두 계산 모두 주요 사고 후 선로 과부하는 확인되지 않았습니다.")
    else:
        lines.append(
            "- 두 slack 가정에서 과부하 발생 여부가 다릅니다. 위의 실제 Security Analysis 결과를 기준으로 해석해야 합니다."
        )

    lines.extend(
        [
            "- 동일한 발전기 사고라도 Load Flow balancing 가정에 따라 사고 후 조류와 위반 정도가 달라질 수 있습니다.",
            "- Single/Distributed slack은 Load Flow balancing 가정이며 운영자 Redispatch가 아닙니다.",
            "- 이 결과는 정적 AC Load Flow / Security Analysis 비교이며 동특성 안정도를 검증한 것은 아닙니다.",
        ]
    )

    return "\n".join(lines)
