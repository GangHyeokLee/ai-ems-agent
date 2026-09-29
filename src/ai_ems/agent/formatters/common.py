from typing import Any


def most_severe_overload(
    overloads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not overloads:
        return None

    return max(
        overloads,
        key=lambda item: item.get("loading_percent") or float("-inf"),
    )


def format_overload(item: dict[str, Any] | None) -> str:
    if item is None:
        return "없음"

    equipment_id = item.get("equipment_id", "-")
    value = item.get("value")
    limit = item.get("limit")
    unit = item.get("unit") or ""
    loading = item.get("loading_percent")

    parts = [str(equipment_id)]
    if value is not None and limit is not None:
        parts.append(f"{value:.2f} {unit} / 한계 {limit:.2f} {unit}")
    if loading is not None:
        parts.append(f"부하율 {loading:.2f}%")
    return " | ".join(parts)


def format_mw(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f} MW"


def format_signed_change(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 0:
        return f"약 {value:.2f} MW 증가"
    return f"약 {abs(value):.2f} MW 감소"
