"""Export PyPSA-Earth network and public power-plant *records* for a geographic map.

Use the PyPSA-Earth Pixi environment, not the AI-EMS runtime venv.
Matched plant rows and mapped buses are NOT independently verified operating units
or electrical connections. The output is NOT suitable for AC load flow.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def _coordinate(value, *, limit: float) -> float | None:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(coordinate) or abs(coordinate) > limit:
        return None
    return coordinate


def _capacity(value) -> float | None:
    try:
        capacity = float(value)
    except (TypeError, ValueError):
        return None
    return capacity if math.isfinite(capacity) and capacity >= 0 else None


def read_powerplant_records(csv_path: Path, bus_ids: set[str]) -> list[dict]:
    """Keep one marker record per CSV row, including potential duplicate units.

    source_record_id identifies a source row, NOT a unique physical power plant.
    """
    with csv_path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        expected = {"Name", "Fueltype", "Capacity", "lat", "lon", "bus"}
        if not reader.fieldnames or not expected.issubset(reader.fieldnames):
            raise ValueError(
                "powerplants.csv needs Name, Fueltype, Capacity, lat, lon, bus columns"
            )
        records = []
        for row_number, row in enumerate(reader, start=1):
            latitude = _coordinate(row.get("lat"), limit=90)
            longitude = _coordinate(row.get("lon"), limit=180)
            if latitude is None or longitude is None:
                continue  # Without geographic coordinates a row cannot be mapped.
            raw_bus = (row.get("bus") or "").strip()
            name = (row.get("Name") or "").strip()
            fueltype = (row.get("Fueltype") or "").strip()
            technology = (row.get("Technology") or "").strip()
            records.append(
                {
                    "record_id": f"mn-powerplant-row-{row_number}",
                    "name": name if name and name.lower() != "nan" else "Unknown",
                    "fueltype": fueltype if fueltype and fueltype.lower() != "nan" else "Unknown",
                    "technology": technology if technology and technology.lower() != "nan" else None,
                    "capacity_mw": _capacity(row.get("Capacity")),
                    "latitude": latitude,
                    "longitude": longitude,
                    # The bus number is an *inferred mapping*, not a validated connection.
                    "mapped_bus_id": raw_bus if raw_bus in bus_ids else None,
                }
            )
    return records


def build_preview(network, powerplants_csv: Path | None = None) -> dict:
    buses = []
    available: set[str] = set()
    for bus_id, row in network.buses.iterrows():
        lat = _coordinate(row["y"], limit=90)
        lon = _coordinate(row["x"], limit=180)
        if lat is None or lon is None:
            continue
        identifier = str(bus_id)
        available.add(identifier)
        buses.append(
            {
                "bus_id": identifier,
                "latitude": lat,
                "longitude": lon,
                "name": str(row.get("name", identifier) or identifier),
            }
        )

    lines = []
    for line_id, row in network.lines.iterrows():
        start, end = str(row["bus0"]), str(row["bus1"])
        if start in available and end in available:
            lines.append(
                {
                    "line_id": str(line_id),
                    "bus1_id": start,
                    "bus2_id": end,
                }
            )

    if not buses:
        raise ValueError("좌표가 있는 모선이 없습니다. 원본 base.nc를 확인하세요.")

    powerplants = (
        read_powerplant_records(powerplants_csv, available)
        if powerplants_csv is not None
        else []
    )
    return {"buses": buses, "lines": lines, "powerplants": powerplants}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", type=Path, help="PyPSA-Earth networks/MN/base.nc")
    parser.add_argument("output", type=Path, help="Destination JSON map file")
    parser.add_argument(
        "--powerplants",
        type=Path,
        help="Optional PyPSA-Earth resources/MN/powerplants.csv; records only",
    )
    args = parser.parse_args()

    import pypsa  # import from PyPSA-Earth's Pixi environment

    network = pypsa.Network(args.network)
    payload = build_preview(network, args.powerplants)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        f"Map preview: {len(payload['buses'])} buses, "
        f"{len(payload['lines'])} lines, "
        f"{len(payload['powerplants'])} plant data rows (not unique plants)"
    )
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
