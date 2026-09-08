from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pypowsybl as pp

from ai_ems.network import LOADFLOW_PARAMETERS


def get_limit_unit(
    limit_type: str | None,
) -> str | None:
    """Return the physical unit used by a Security Analysis limit type."""
    units = {
        "APPARENT_POWER": "MVA",
        "ACTIVE_POWER": "MW",
        "CURRENT": "A",
        "LOW_VOLTAGE": "kV",
        "HIGH_VOLTAGE": "kV",
        "VOLTAGE": "kV",
    }
    return units.get(limit_type)


def run_line_contingency(
    network,
    outage_line_id: str,
    monitored_line_ids: list[str] | None = None,
    contingency_id: str | None = None,
) -> dict[str, Any]:
    """Run a single line outage using PyPowSyBl Security Analysis."""
    lines = network.get_lines()
    if outage_line_id not in lines.index:
        raise ValueError(f"Unknown outage line: {outage_line_id}")

    monitored = monitored_line_ids or []
    missing = [line_id for line_id in monitored if line_id not in lines.index]
    if missing:
        raise ValueError(f"Unknown monitored line(s): {', '.join(missing)}")

    contingency_id = contingency_id or f"OUT_{outage_line_id}"

    base_result = pp.loadflow.run_ac(
        network,
        parameters=LOADFLOW_PARAMETERS,
    )
    base_converged = bool(base_result) and all(
        component.status.name == "CONVERGED" for component in base_result
    )
    if not base_converged:
        return {
            "contingency_id": contingency_id,
            "outage_line_id": outage_line_id,
            "base_converged": False,
            "post_status": None,
            "violation_count": 0,
            "violated_equipment_count": 0,
            "limit_violations": [],
            "violated_equipment": [],
            "monitored_branches": [],
        }

    base_monitored = {
        line_id: _line_flow_snapshot(network, line_id) for line_id in monitored
    }

    analysis = pp.security.create_analysis()
    analysis.add_single_element_contingency(
        outage_line_id,
        contingency_id,
    )
    if monitored:
        analysis.add_monitored_elements(branch_ids=monitored)

    result = analysis.run_ac(
        network,
        parameters=LOADFLOW_PARAMETERS,
    )

    pre = result.pre_contingency_result
    post = result.find_post_contingency_result(contingency_id)

    pre_violations = [_serialize_violation(item) for item in pre.limit_violations]

    pre_violated_equipment = _summarize_violations(pre_violations)

    violations = [_serialize_violation(item) for item in post.limit_violations]

    violated_equipment = _summarize_violations(violations)

    violation_comparison = _compare_violation_summaries(
        pre_violated_equipment,
        violated_equipment,
    )

    monitored_results = _serialize_branch_results(
        result.branch_results,
        contingency_id,
        base_monitored,
    )

    return {
        "contingency_id": contingency_id,
        "outage_line_id": outage_line_id,
        "base_converged": True,

        "pre_status": pre.status.name,
        "pre_violation_count": len(pre_violations),
        "pre_violated_equipment_count": len(
            pre_violated_equipment
        ),
        "pre_limit_violations": pre_violations,
        "pre_violated_equipment": (
            pre_violated_equipment
        ),

        "post_status": post.status.name,
        "violation_count": len(violations),
        "violated_equipment_count": len(
            violated_equipment
        ),
        "limit_violations": violations,
        "violated_equipment": violated_equipment,

        "violation_comparison": (
            violation_comparison
        ),
        "monitored_branches": monitored_results,
    }


def select_primary_violation(
    security_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Select one representative violation for compact Agent summaries."""
    violations = security_result.get("violated_equipment", [])
    if not violations:
        return None

    def severity(item: dict[str, Any]) -> float:
        loading_percent = item.get("loading_percent")
        if loading_percent is not None:
            return float(loading_percent) / 100.0 - 1.0

        violation_amount = item.get("violation_amount")
        limit = item.get("limit")
        if violation_amount is None:
            return float("-inf")
        if limit not in (None, 0):
            return float(violation_amount) / abs(float(limit))
        return float(violation_amount)

    return max(violations, key=severity)


def select_most_severe_violated_line(
    network,
    security_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Select the most overloaded transmission line for Sensitivity Analysis."""
    lines = network.get_lines()

    candidates = [
        item
        for item in security_result.get("violated_equipment", [])
        if (
            item["equipment_id"] in lines.index
            and item.get("loading_percent") is not None
        )
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: float(item["loading_percent"]),
    )


def _summarize_violations(
    violations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group side-specific violation records into equipment-level summaries."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)

    for item in violations:
        key = (
            str(item.get("subject_id", "")),
            str(item.get("limit_type", "")),
            str(item.get("limit_name", "")),
        )
        grouped[key].append(item)

    summaries: list[dict[str, Any]] = []
    for (equipment_id, limit_type, limit_name), items in grouped.items():
        values = [
            float(item["value"])
            for item in items
            if isinstance(item.get("value"), (int, float))
        ]
        limits = [
            float(item["limit"])
            for item in items
            if isinstance(item.get("limit"), (int, float))
        ]
        sides = [str(item["side"]) for item in items if item.get("side") is not None]

        summary: dict[str, Any] = {
            "equipment_id": equipment_id,
            "limit_type": limit_type,
            "limit_name": limit_name,
            "unit": get_limit_unit(limit_type),
            "sides": sorted(set(sides)),
            "record_count": len(items),
        }

        if limits and values:
            if limit_type == "LOW_VOLTAGE":
                limit = max(limits)
                value = min(values)
                violation_amount = limit - value
                violation_direction = "below_minimum"
            else:
                limit = min(limits)
                value = max(values)
                violation_amount = value - limit
                violation_direction = "above_maximum"

            summary["limit"] = limit
            summary["value"] = value
            summary["violation_amount"] = violation_amount
            summary["violation_direction"] = violation_direction

            if limit_type in {
                "CURRENT",
                "ACTIVE_POWER",
                "APPARENT_POWER",
            }:
                summary["loading_percent"] = (
                    value / limit * 100.0 if limit != 0 else None
                )

        summaries.append(summary)

    return summaries

def _violation_summary_key(
    item:  dict[str, Any],
) -> tuple[str, str, str]:
    return (
        str(item.get("equipment_id", "")),
        str(item.get("limit_type", "")),
        str(item.get("limit_name", "")),
    )

def _compare_violation_summarize(
    pre: list[dict[str, Any]],
    post: list[dict[str, Any]],
) -> dict[str, Any]:
    pre_map = {
        _violation_summary_key(item): item
        for  item in pre
    }

def _line_flow_snapshot(network, line_id: str) -> dict[str, float]:
    row = network.get_lines().loc[line_id]
    p1 = float(row["p1"])
    q1 = float(row["q1"])
    p2 = float(row["p2"])
    q2 = float(row["q2"])

    return {
        "p1_mw": p1,
        "q1_mvar": q1,
        "p2_mw": p2,
        "q2_mvar": q2,
        "apparent_power_mva": max(
            float(np.hypot(p1, q1)),
            float(np.hypot(p2, q2)),
        ),
    }


def _serialize_violation(violation) -> dict[str, Any]:
    fields = [
        "subject_id",
        "subject_name",
        "limit_type",
        "limit_name",
        "acceptable_duration",
        "limit",
        "limit_reduction",
        "value",
        "side",
    ]
    output: dict[str, Any] = {}

    for field in fields:
        if not hasattr(violation, field):
            continue
        value = getattr(violation, field)
        if value is None:
            output[field] = None
        elif hasattr(value, "name"):
            output[field] = value.name
        elif isinstance(value, (np.floating, float)):
            output[field] = float(value)
        elif isinstance(value, (np.integer, int)):
            output[field] = int(value)
        else:
            output[field] = str(value)

    if not output:
        output["raw"] = str(violation)

    return output


def _serialize_branch_results(
    branch_results,
    contingency_id: str,
    base_monitored: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    if branch_results is None or getattr(branch_results, "empty", True):
        return []

    rows = branch_results.reset_index()
    if "contingency_id" in rows.columns:
        rows = rows[rows["contingency_id"] == contingency_id]

    output: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        line_id = str(row.get("branch_id", ""))
        item: dict[str, Any] = {
            "line_id": line_id,
            "base": base_monitored.get(line_id),
        }

        for source, target in [
            ("p1", "p1_mw"),
            ("q1", "q1_mvar"),
            ("i1", "i1_a"),
            ("p2", "p2_mw"),
            ("q2", "q2_mvar"),
            ("i2", "i2_a"),
            ("flow_transfer", "flow_transfer"),
        ]:
            if source in row.index and not _is_missing(row[source]):
                item[target] = float(row[source])

        if all(key in item for key in ["p1_mw", "q1_mvar", "p2_mw", "q2_mvar"]):
            item["apparent_power_mva"] = max(
                float(np.hypot(item["p1_mw"], item["q1_mvar"])),
                float(np.hypot(item["p2_mw"], item["q2_mvar"])),
            )

        output.append(item)

    return output


def _is_missing(value: Any) -> bool:
    try:
        return bool(np.isnan(value))
    except (TypeError, ValueError):
        return value is None
