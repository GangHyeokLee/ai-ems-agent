"""Export geographic buses/lines from a PyPSA-Earth base.nc for K-EMS map preview.

Run this with PyPSA-Earth's Pixi Python (not the ai-ems-agent venv).
This exports topology coordinates only; it does NOT create a solved EMS case.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def build_preview(network):
    buses = []
    available = set()
    for bus_id, row in network.buses.iterrows():
        lat, lon = float(row["y"]), float(row["x"])
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        identifier = str(bus_id)
        available.add(identifier)
        buses.append({
            "bus_id": identifier,
            "latitude": lat,
            "longitude": lon,
            "name": str(row.get("name", identifier) or identifier),
        })

    lines = []
    for line_id, row in network.lines.iterrows():
        start, end = str(row["bus0"]), str(row["bus1"])
        if start in available and end in available:
            lines.append({
                "line_id": str(line_id),
                "bus1_id": start,
                "bus2_id": end,
            })

    if not buses:
        raise ValueError("좌표가 있는 모선이 없습니다. 원본 base.nc를 확인하세요.")

    return {"buses": buses, "lines": lines}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", type=Path, help="PyPSA-Earth networks/MN/base.nc")
    parser.add_argument("output", type=Path, help="Destination JSON map file")
    args = parser.parse_args()

    import pypsa  # install/run in PyPSA-Earth's Pixi environment

    network = pypsa.Network(args.network)
    payload = build_preview(network)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Map preview: {len(payload['buses'])} buses, {len(payload['lines'])} lines")
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
