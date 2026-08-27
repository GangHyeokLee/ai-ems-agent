from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pypowsybl as pp


IMPORT_PARAMETERS = {
    "matpower.import.ignore-base-voltage": "false",
}


def load_network(case_path: str | Path):
    """Load a PyPowSyBl-compatible network from a MATPOWER file."""
    path = Path(case_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Network case file not found: {path}")

    return pp.network.load(
        path,
        parameters=IMPORT_PARAMETERS,
    )


def get_network_summary(network) -> dict[str, Any]:
    """Return a compact, JSON-serializable network summary."""
    voltage_levels = network.get_voltage_levels()

    return {
        "buses": len(network.get_buses()),
        "generators": len(network.get_generators()),
        "loads": len(network.get_loads()),
        "lines": len(network.get_lines()),
        "two_winding_transformers": len(
            network.get_2_windings_transformers()
        ),
        "hvdc_lines": len(network.get_hvdc_lines()),
        "nominal_voltages_kv": sorted(
            float(value)
            for value in voltage_levels["nominal_v"].dropna().unique()
        ),
    }


def list_lines(network, limit: int | None = None) -> list[dict[str, Any]]:
    """Return line identifiers and key endpoint/flow attributes."""
    lines = network.get_lines(all_attributes=True)
    columns = [
        column
        for column in [
            "voltage_level1_id",
            "voltage_level2_id",
            "bus1_id",
            "bus2_id",
            "p1",
            "q1",
            "p2",
            "q2",
        ]
        if column in lines.columns
    ]

    if limit is not None:
        lines = lines.head(limit)

    records: list[dict[str, Any]] = []
    for line_id, row in lines[columns].iterrows():
        item: dict[str, Any] = {"line_id": str(line_id)}
        for column in columns:
            value = row[column]
            if _is_missing(value):
                item[column] = None
            elif isinstance(value, (np.floating, float)):
                item[column] = float(value)
            elif isinstance(value, (np.integer, int)):
                item[column] = int(value)
            else:
                item[column] = str(value)
        records.append(item)

    return records


def run_ac_load_flow(network) -> dict[str, Any]:
    """Run AC load flow and return convergence plus basic operating ranges."""
    parameters = pp.loadflow.Parameters(distributed_slack=False)
    result = pp.loadflow.run_ac(network, parameters=parameters)

    components = [
        {
            "status": component.status.name,
            "iteration_count": _optional_int(component, "iteration_count"),
            "distributed_active_power_mw": _optional_float(
                component, "distributed_active_power"
            ),
        }
        for component in result
    ]

    converged = bool(result) and all(
        component.status.name == "CONVERGED" for component in result
    )

    response: dict[str, Any] = {
        "converged": converged,
        "components": components,
    }

    if converged:
        buses = network.get_buses()
        voltage_levels = network.get_voltage_levels()
        bus_check = buses.join(
            voltage_levels[["nominal_v"]],
            on="voltage_level_id",
        )
        bus_check["v_pu"] = bus_check["v_mag"] / bus_check["nominal_v"]

        response["bus_voltage_pu"] = {
            "min": float(bus_check["v_pu"].min()),
            "max": float(bus_check["v_pu"].max()),
        }

    return response


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
    parameters = pp.loadflow.Parameters(distributed_slack=False)

    base_result = pp.loadflow.run_ac(network, parameters=parameters)
    base_converged = bool(base_result) and all(
        component.status.name == "CONVERGED" for component in base_result
    )
    if not base_converged:
        return {
            "contingency_id": contingency_id,
            "outage_line_id": outage_line_id,
            "base_converged": False,
            "post_status": None,
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

    result = analysis.run_ac(network, parameters=parameters)
    post = result.find_post_contingency_result(contingency_id)

    violations = [_serialize_violation(item) for item in post.limit_violations]
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


def _optional_float(obj: Any, name: str) -> float | None:
    if not hasattr(obj, name):
        return None
    value = getattr(obj, name)
    if value is None:
        return None
    return float(value)


def _optional_int(obj: Any, name: str) -> int | None:
    if not hasattr(obj, name):
        return None
    value = getattr(obj, name)
    if value is None:
        return None
    return int(value)
