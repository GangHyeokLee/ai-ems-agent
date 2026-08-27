from __future__ import annotations

from typing import Any

import numpy as np
import pypowsybl as pp

from ai_ems.network import LOADFLOW_PARAMETERS


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
            "limit_violations": [],
            "monitored_branches": [],
        }

    base_monitored = {
        line_id: _line_flow_snapshot(network, line_id)
        for line_id in monitored
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
    post = result.find_post_contingency_result(contingency_id)

    violations = [
        _serialize_violation(item)
        for item in post.limit_violations
    ]
    monitored_results = _serialize_branch_results(
        result.branch_results,
        contingency_id,
        base_monitored,
    )

    return {
        "contingency_id": contingency_id,
        "outage_line_id": outage_line_id,
        "base_converged": True,
        "post_status": post.status.name,
        "violation_count": len(violations),
        "limit_violations": violations,
        "monitored_branches": monitored_results,
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

        if all(
            key in item
            for key in ["p1_mw", "q1_mvar", "p2_mw", "q2_mvar"]
        ):
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
