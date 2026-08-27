from __future__ import annotations

from typing import Any

import numpy as np


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


def _is_missing(value: Any) -> bool:
    try:
        return bool(np.isnan(value))
    except (TypeError, ValueError):
        return value is None
