from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Iterable

import pandas as pd


CLASS_PRIORITY = {
    "ISLANDED": 0,
    "NON_CONVERGED": 1,
    "CONVERGED_VIOLATION": 2,
    "CONVERGED_CLEAN": 3,
    "EXECUTION_ERROR": 99,
}


def classify_result(
    raw_status: str | None,
    violation_count: int,
    connectivity: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    """Normalize engine-specific outcomes without discarding the raw status."""
    if error:
        return "EXECUTION_ERROR"

    status = (raw_status or "").upper()
    connectivity = connectivity or {}
    created_components = max(
        _as_int(connectivity.get("created_connected_component_count")),
        _as_int(connectivity.get("created_synchronous_component_count")),
    )
    disconnected = connectivity.get("disconnected_element_ids") or []

    # With OpenLoadFlow's result extension enabled, this list contains
    # additional elements disconnected by contingency propagation, not merely
    # the requested outage element.
    if "ISLAND" in status or created_components > 0 or bool(disconnected):
        return "ISLANDED"
    if status not in {"CONVERGED", "NO_IMPACT"}:
        return "NON_CONVERGED"
    if violation_count > 0:
        return "CONVERGED_VIOLATION"
    return "CONVERGED_CLEAN"


def rank_contingencies(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply a deterministic, physics-first lexicographic review order.

    Execution errors are retained for auditability but do not receive a
    physical-risk rank.
    """
    records = [dict(row) for row in rows]
    physical = [
        row for row in records if row.get("classification") != "EXECUTION_ERROR"
    ]
    errors = [
        row for row in records if row.get("classification") == "EXECUTION_ERROR"
    ]

    physical.sort(key=_ranking_key)
    errors.sort(key=lambda row: str(row.get("contingency_id", "")))

    for rank, row in enumerate(physical, start=1):
        row["review_rank"] = rank
        row["review_priority"] = CLASS_PRIORITY.get(
            str(row.get("classification")), 98
        )
    for row in errors:
        row["review_rank"] = None
        row["review_priority"] = CLASS_PRIORITY["EXECUTION_ERROR"]

    return physical + errors


def compare_with_legacy(
    study_rows: Iterable[dict[str, Any]],
    legacy: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare batch Security results with the earlier direct AC-PF N-1 CSV.

    Mapping is based on bus-pair occurrence order. This is deterministic and
    makes parallel circuits explicit instead of silently joining only by a bus
    pair. Unmatched rows remain visible in the mapping output.
    """
    study = pd.DataFrame(list(study_rows))
    required = {"outage_branch", "outage_from_bus", "outage_to_bus", "status"}
    missing = sorted(required - set(legacy.columns))
    if missing:
        raise ValueError(f"Legacy CSV missing column(s): {', '.join(missing)}")

    line_rows = study[study["element_type"] == "LINE"].copy()
    line_rows["_pair"] = line_rows.apply(
        lambda row: _canonical_pair(row.get("from_bus"), row.get("to_bus")),
        axis=1,
    )
    legacy_rows = legacy.copy()
    legacy_rows["_pair"] = legacy_rows.apply(
        lambda row: _canonical_pair(
            row.get("outage_from_bus"), row.get("outage_to_bus")
        ),
        axis=1,
    )

    study_groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for item in line_rows.to_dict("records"):
        study_groups[item["_pair"]].append(item)
    for items in study_groups.values():
        items.sort(key=lambda item: _natural_key(str(item["element_id"])))

    occurrence: dict[tuple[int, int], int] = defaultdict(int)
    mappings: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []

    for legacy_row in legacy_rows.sort_values("outage_branch").to_dict("records"):
        pair = legacy_row["_pair"]
        ordinal = occurrence[pair]
        occurrence[pair] += 1
        candidates = study_groups.get(pair, [])
        matched = candidates[ordinal] if ordinal < len(candidates) else None

        mapping = {
            "outage_branch": int(legacy_row["outage_branch"]),
            "from_bus": pair[0],
            "to_bus": pair[1],
            "parallel_ordinal": ordinal + 1,
            "element_id": matched.get("element_id") if matched else None,
            "contingency_id": matched.get("contingency_id") if matched else None,
            "mapping_status": "MATCHED" if matched else "UNMATCHED",
            "mapping_method": "unordered_bus_pair_and_parallel_ordinal",
        }
        mappings.append(mapping)
        if matched is None:
            continue

        legacy_class = _legacy_classification(legacy_row)
        study_class = str(matched.get("classification"))
        legacy_has_violation = legacy_class == "CONVERGED_VIOLATION"
        study_has_violation = study_class == "CONVERGED_VIOLATION"
        comparisons.append(
            {
                **mapping,
                "legacy_status": legacy_row.get("status"),
                "legacy_classification": legacy_class,
                "study_raw_status": matched.get("raw_status"),
                "study_classification": study_class,
                "classification_match": legacy_class == study_class,
                "violation_presence_match": (
                    legacy_has_violation == study_has_violation
                ),
                "legacy_max_loading_percent": _optional_float(
                    legacy_row.get("max_loading_pct")
                ),
                "study_max_loading_percent": _optional_float(
                    matched.get("max_loading_percent")
                ),
                "legacy_min_voltage_pu": _optional_float(
                    legacy_row.get("min_voltage_pu")
                ),
                "study_min_voltage_pu": _optional_float(
                    matched.get("min_voltage_pu")
                ),
                "legacy_worst_branch": _optional_int(
                    legacy_row.get("worst_branch")
                ),
                "study_worst_branch_id": matched.get("worst_branch_id"),
                "legacy_overloaded_count": _optional_int(
                    legacy_row.get("overloaded_branch_count")
                ),
                "study_violated_equipment_count": _optional_int(
                    matched.get("violated_equipment_count")
                ),
            }
        )

    mapped_ids = {item["element_id"] for item in mappings if item["element_id"]}
    for item in line_rows.to_dict("records"):
        if item["element_id"] not in mapped_ids:
            mappings.append(
                {
                    "outage_branch": None,
                    "from_bus": item.get("from_bus"),
                    "to_bus": item.get("to_bus"),
                    "parallel_ordinal": None,
                    "element_id": item["element_id"],
                    "contingency_id": item["contingency_id"],
                    "mapping_status": "STUDY_ONLY",
                    "mapping_method": "unordered_bus_pair_and_parallel_ordinal",
                }
            )

    branch_to_line = {
        item["outage_branch"]: item["element_id"]
        for item in mappings
        if item.get("outage_branch") is not None and item.get("element_id")
    }
    for item in comparisons:
        legacy_worst_id = branch_to_line.get(item["legacy_worst_branch"])
        item["legacy_worst_line_id"] = legacy_worst_id
        item["worst_branch_match"] = (
            legacy_worst_id == item["study_worst_branch_id"]
            if legacy_worst_id is not None
            and item["study_worst_branch_id"] is not None
            else None
        )
        item["max_loading_difference_percent_point"] = _difference(
            item["study_max_loading_percent"],
            item["legacy_max_loading_percent"],
        )
        item["min_voltage_difference_pu"] = _difference(
            item["study_min_voltage_pu"],
            item["legacy_min_voltage_pu"],
        )

    return pd.DataFrame(comparisons), pd.DataFrame(mappings)


def summarize_violations(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in violations:
        key = (
            str(item.get("subject_id", "")),
            str(item.get("limit_type", "")),
            str(item.get("limit_name", "")),
        )
        grouped[key].append(item)

    output: list[dict[str, Any]] = []
    for (equipment_id, limit_type, limit_name), items in sorted(grouped.items()):
        values = [_optional_float(item.get("value")) for item in items]
        limits = [_optional_float(item.get("limit")) for item in items]
        values = [value for value in values if value is not None]
        limits = [value for value in limits if value is not None]
        summary: dict[str, Any] = {
            "equipment_id": equipment_id,
            "limit_type": limit_type,
            "limit_name": limit_name,
            "sides": sorted(
                {str(item["side"]) for item in items if item.get("side") is not None}
            ),
            "record_count": len(items),
        }
        if values and limits:
            if limit_type == "LOW_VOLTAGE":
                limit, value = max(limits), min(values)
                amount = limit - value
            else:
                limit, value = min(limits), max(values)
                amount = value - limit
            summary.update(
                limit=limit,
                value=value,
                violation_amount=amount,
                relative_violation=(amount / abs(limit) if limit else None),
            )
            if limit_type in {"CURRENT", "ACTIVE_POWER", "APPARENT_POWER"}:
                summary["loading_percent"] = value / limit * 100.0 if limit else None
        output.append(summary)
    return output


def primary_violation(violations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not violations:
        return None
    return max(
        violations,
        key=lambda item: (
            _number_or(item.get("relative_violation"), float("-inf")),
            _number_or(item.get("violation_amount"), float("-inf")),
            str(item.get("equipment_id", "")),
        ),
    )


def parse_line_buses(line_id: str, row: pd.Series | dict[str, Any]) -> tuple[int | None, int | None]:
    match = re.match(r"^LINE-(\d+)-(\d+)(?:#\d+)?$", str(line_id))
    if match:
        return int(match.group(1)), int(match.group(2))
    return _extract_bus_number(row.get("bus1_id")), _extract_bus_number(
        row.get("bus2_id")
    )


def _ranking_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        CLASS_PRIORITY.get(str(row.get("classification")), 98),
        -_number_or(row.get("disconnected_element_count"), 0.0),
        -_number_or(row.get("violated_equipment_count"), 0.0),
        -_number_or(row.get("violation_count"), 0.0),
        -_number_or(row.get("max_loading_percent"), float("-inf")),
        -_number_or(row.get("max_relative_violation"), float("-inf")),
        -_number_or(row.get("max_voltage_violation"), float("-inf")),
        _number_or(row.get("min_voltage_pu"), float("inf")),
        str(row.get("contingency_id", "")),
    )


def _legacy_classification(row: dict[str, Any]) -> str:
    status = str(row.get("status", "")).upper()
    if status == "ISLANDING" or _as_int(row.get("component_count")) > 1:
        return "ISLANDED"
    if not bool(row.get("converged", False)):
        return "NON_CONVERGED"
    if status == "LIMIT_VIOLATION":
        return "CONVERGED_VIOLATION"
    return "CONVERGED_CLEAN"


def _canonical_pair(first: Any, second: Any) -> tuple[int, int]:
    a, b = int(first), int(second)
    return (a, b) if a <= b else (b, a)


def _extract_bus_number(value: Any) -> int | None:
    if value is None:
        return None
    matches = re.findall(r"\d+", str(value))
    return int(matches[-1]) if matches else None


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def _as_int(value: Any) -> int:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _number_or(value: Any, default: float) -> float:
    try:
        number = float(value)
        return default if math.isnan(number) else number
    except (TypeError, ValueError):
        return default


def _difference(first: Any, second: Any) -> float | None:
    first_value = _optional_float(first)
    second_value = _optional_float(second)
    if first_value is None or second_value is None:
        return None
    return first_value - second_value
