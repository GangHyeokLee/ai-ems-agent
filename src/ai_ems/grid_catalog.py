"""Read-only grid catalog and validated OSM map preview for K-EMS PoC.

A geographic base network is NOT an operational AC load-flow model.
Keep map availability separate from power-flow capability.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

KPG_GRID_ID = "kr-kpg193"
MONGOLIA_GRID_ID = "mn-osm"
MAX_PREVIEW_BUSES = 50000
MAX_PREVIEW_LINES = 100000
MAX_PREVIEW_PLANTS = 100000


def list_grids(mongolia_map_file: Path) -> list[dict[str, Any]]:
    """Return registered cases without loading an optional PyPSA dependency."""
    map_ready = mongolia_map_file.is_file()
    return [
        {
            "id": KPG_GRID_ID,
            "country": "KR",
            "name": "KPG-193 시험계통",
            "source": "KPG / local MATPOWER case",
            "format": "MATPOWER",
            "map_available": True,
            "analysis_available": True,
            "status": "analysis_ready",
            "note": "기존 KPG 시험계통입니다. 실제 한국 운영계통을 의미하지 않습니다.",
        },
        {
            "id": MONGOLIA_GRID_ID,
            "country": "MN",
            "name": "몽골 OSM 기반 송전망",
            "source": "OpenStreetMap / PyPSA-Earth",
            "format": "PyPSA NetCDF → JSON map preview",
            "map_available": map_ready,
            "analysis_available": False,
            "status": "map_ready" if map_ready else "preparing",
            "note": (
                "OSM 기반 기초 송전망입니다. 발전기·부하 및 운전조건 보완과 "
                "전력조류 수렴 검증 전에는 계통해석을 제공하지 않습니다."
            ),
        },
    ]


def load_mongolia_map(map_file: Path) -> dict[str, Any]:
    """Validate the generated map file before sending it to the browser."""
    if not map_file.is_file():
        raise FileNotFoundError("몽골 지도 데이터가 아직 생성되지 않았습니다.")

    try:
        payload = json.loads(map_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("몽골 지도 JSON을 읽을 수 없습니다.") from exc

    if not isinstance(payload, dict):
        raise ValueError("지도 데이터는 JSON 객체여야 합니다.")

    raw_buses = payload.get("buses")
    raw_lines = payload.get("lines")
    if not isinstance(raw_buses, list) or not isinstance(raw_lines, list):
        raise ValueError("지도에는 buses 및 lines 배열이 필요합니다.")
    if not 0 < len(raw_buses) <= MAX_PREVIEW_BUSES:
        raise ValueError("지도 모선 개수가 허용 범위를 벗어났습니다.")
    if len(raw_lines) > MAX_PREVIEW_LINES:
        raise ValueError("지도 선로 개수가 허용 범위를 벗어났습니다.")

    buses = []
    known_buses: set[str] = set()
    for bus in raw_buses:
        if not isinstance(bus, dict):
            raise ValueError("모선 항목 형식이 잘못되었습니다.")
        identifier = str(bus.get("bus_id", "")).strip()
        if not identifier or identifier in known_buses:
            raise ValueError("모선 ID가 비어 있거나 중복됩니다.")
        try:
            lat = float(bus["latitude"])
            lon = float(bus["longitude"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"모선 {identifier}의 좌표가 올바르지 않습니다.") from exc
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError(f"모선 {identifier}의 좌표 범위가 올바르지 않습니다.")
        known_buses.add(identifier)
        name = str(bus.get("name", identifier))
        buses.append({
            "bus_id": identifier,
            "latitude": lat,
            "longitude": lon,
            "name_korean": name,
            "name_english": name,
        })

    lines = []
    for line in raw_lines:
        if not isinstance(line, dict):
            raise ValueError("선로 항목 형식이 잘못되었습니다.")
        identifier = str(line.get("line_id", "")).strip()
        bus0 = str(line.get("bus1_id", "")).strip()
        bus1 = str(line.get("bus2_id", "")).strip()
        if not identifier or bus0 not in known_buses or bus1 not in known_buses:
            raise ValueError(f"선로 {identifier or '(unknown)'}의 연결 모선이 올바르지 않습니다.")
        lines.append({
            "line_id": identifier,
            "bus1_id": bus0,
            "bus2_id": bus1,
        })

    raw_plants = payload.get("powerplants", [])
    if not isinstance(raw_plants, list) or len(raw_plants) > MAX_PREVIEW_PLANTS:
        raise ValueError("발전소 데이터 개수가 허용 범위를 벗어났습니다.")
    plants = []
    for record in raw_plants:
        if not isinstance(record, dict):
            raise ValueError("발전소 레코드 형식이 잘못되었습니다.")
        record_id = str(record.get("record_id", "")).strip()
        if not record_id:
            raise ValueError("발전소 레코드 ID가 없습니다.")
        try:
            latitude = float(record["latitude"])
            longitude = float(record["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"발전소 {record_id}의 좌표가 올바르지 않습니다.") from exc
        if not (math.isfinite(latitude) and math.isfinite(longitude)
                and -90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError(f"발전소 {record_id}의 좌표 범위가 올바르지 않습니다.")
        raw_capacity = record.get("capacity_mw")
        if raw_capacity is None:
            capacity = None
        else:
            try:
                capacity = float(raw_capacity)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"발전소 {record_id}의 용량이 잘못되었습니다.") from exc
            if not math.isfinite(capacity) or capacity < 0:
                raise ValueError(f"발전소 {record_id}의 용량 범위가 올바르지 않습니다.")
        mapped_bus = record.get("mapped_bus_id")
        if mapped_bus is not None:
            mapped_bus = str(mapped_bus)
            if mapped_bus not in known_buses:
                raise ValueError(f"발전소 {record_id}의 추정 모선이 없습니다.")
        plants.append({
            "record_id": record_id,
            "name": str(record.get("name") or "Unknown"),
            "fueltype": str(record.get("fueltype") or "Unknown"),
            "technology": str(record["technology"]) if record.get("technology") else None,
            "capacity_mw": capacity,
            "latitude": latitude,
            "longitude": longitude,
            "mapped_bus_id": mapped_bus,
        })

    return {"buses": buses, "lines": lines, "generators": [], "powerplants": plants}
