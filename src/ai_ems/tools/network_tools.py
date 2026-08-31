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

    return [
        _serialize_indexed_row("line_id", line_id, row, columns)
        for line_id, row in lines[columns].iterrows()
    ]


def get_line(network, line_id: str) -> dict[str, Any]:
    """Return detailed information for one transmission line."""
    lines = network.get_lines(all_attributes=True)
    if line_id not in lines.index:
        raise ValueError(f"Unknown line: {line_id}")

    columns = [
        column
        for column in [
            "name",
            "voltage_level1_id",
            "voltage_level2_id",
            "bus1_id",
            "bus2_id",
            "r",
            "x",
            "g1",
            "b1",
            "g2",
            "b2",
            "p1",
            "q1",
            "p2",
            "q2",
            "connected1",
            "connected2",
        ]
        if column in lines.columns
    ]

    return _serialize_indexed_row(
        "line_id",
        line_id,
        lines.loc[line_id],
        columns,
    )


def list_generators(
    network,
    limit: int | None = None,
    connected_only: bool = False,
) -> list[dict[str, Any]]:
    """Return generator identifiers and key operating attributes."""
    generators = network.get_generators(all_attributes=True)
    columns = [
        column
        for column in [
            "name",
            "energy_source",
            "voltage_level_id",
            "bus_id",
            "target_p",
            "min_p",
            "max_p",
            "p",
            "q",
            "connected",
        ]
        if column in generators.columns
    ]

    if connected_only and "connected" in generators.columns:
        generators = generators[generators["connected"]]

    if limit is not None:
        generators = generators.head(limit)

    return [
        _serialize_indexed_row("generator_id", gen_id, row, columns)
        for gen_id, row in generators[columns].iterrows()
    ]


def _serialize_indexed_row(
    id_field: str,
    row_id: Any,
    row,
    columns: list[str],
) -> dict[str, Any]:
    item: dict[str, Any] = {id_field: str(row_id)}
    for column in columns:
        item[column] = _serialize_value(row[column])
    return item


def _serialize_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    return str(value)


def _is_missing(value: Any) -> bool:
    try:
        return bool(np.isnan(value))
    except (TypeError, ValueError):
        return value is None
