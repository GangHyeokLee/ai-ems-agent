"""Read-only grid catalog and validated OSM map preview for K-EMS PoC.

A geographic base network is NOT an operational AC load-flow model.
Keep map availability separate from power-flow capability.

Capabilities:
- analysis_available: physics analysis (N-1, sensitivity, generator contingency) can run.
- pf_preview_available: a PF preview asset exists for the map (validated on every request).
- chat_available: the AI Assistant may be used. For KPG this is the existing analysis
  agent; for Mongolia it is a read-only agent over the validated synthetic PF preview,
  so it requires the PF preview asset and never enables physics analysis.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

KPG_GRID_ID = "kr-kpg193"
MONGOLIA_GRID_ID = "mn-osm-35k"
MAX_PREVIEW_BUSES = 50000
MAX_PREVIEW_LINES = 100000
MAX_PREVIEW_PLANTS = 100000
MAX_PREVIEW_TRANSFORMERS = 100000


def list_grids(
    mongolia_map_file: Path,
    mongolia_pf_preview_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the KPG test case and the active Mongolia 35 kV map preview."""
    map_ready = mongolia_map_file.is_file()
    pf_preview_ready = bool(mongolia_pf_preview_file and mongolia_pf_preview_file.is_file())
    return [
        {
            "id": KPG_GRID_ID,
            "country": "KR",
            "name": "KPG-193 시험계통",
            "source": "KPG / local MATPOWER case",
            "format": "MATPOWER",
            "map_available": True,
            "analysis_available": True,
            "pf_preview_available": True,
            "chat_available": True,
            "chat_mode": "analysis",
            "status": "analysis_ready",
            "note": (
                "KPG-193 합성 시험계통의 정상상태 PF 미리보기입니다. "
                "실제 한국 운영계통 결과가 아니며, 기존 KPG 분석 기능은 유지됩니다."
            ),
        },
        {
            "id": MONGOLIA_GRID_ID,
            "country": "MN",
            "name": "몽골 OSM 송전망 (35/110/220 kV)",
            "source": "OpenStreetMap / PyPSA-Earth MN_35k",
            "format": "PyPSA NetCDF → JSON map preview",
            "map_available": map_ready,
            "analysis_available": False,
            "pf_preview_available": map_ready and pf_preview_ready,
            "chat_available": map_ready and pf_preview_ready,
            "chat_mode": "pf_read_only",
            "status": "map_ready" if map_ready else "preparing",
            "note": (
                "합성 AC Power Flow 미리보기는 조회 전용이며 웹 요청 시 재계산하지 않습니다. "
                "실제 몽골 운영 데이터가 아니며, 선로·변압기 정수와 발전소 접속은 일부 미검증입니다. "
                "N-1·Redispatch는 지원하지 않으며, AI Assistant는 합성 PF 결과 조회·설명만 지원합니다."
            ),
        },
    ]


def _optional_nonnegative_number(raw: Any, name: str, *, strictly_positive: bool = False) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} 값이 올바르지 않습니다.") from exc
    if not math.isfinite(value) or value < 0 or (strictly_positive and value == 0):
        raise ValueError(f"{name} 범위가 올바르지 않습니다.")
    return value


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
            "voltage_kv": _optional_nonnegative_number(bus.get("voltage_kv"), "모선 전압", strictly_positive=True),
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
            "voltage_kv": _optional_nonnegative_number(line.get("voltage_kv"), "선로 전압", strictly_positive=True),
            "length_km": _optional_nonnegative_number(line.get("length_km"), "선로 길이"),
        })

    raw_transformers = payload.get("transformers", [])
    if not isinstance(raw_transformers, list) or len(raw_transformers) > MAX_PREVIEW_TRANSFORMERS:
        raise ValueError("변압기 목록 형식이 잘못되었습니다.")
    transformers = []
    transformer_ids: set[str] = set()
    for record in raw_transformers:
        if not isinstance(record, dict):
            raise ValueError("변압기 항목 형식이 잘못되었습니다.")
        identifier = str(record.get("transformer_id", "")).strip()
        bus0 = str(record.get("bus1_id", "")).strip()
        bus1 = str(record.get("bus2_id", "")).strip()
        if not identifier or identifier in transformer_ids or bus0 not in known_buses or bus1 not in known_buses or bus0 == bus1:
            raise ValueError("변압기 ID 또는 연결 모선이 올바르지 않습니다.")
        transformer_ids.add(identifier)
        transformers.append({
            "transformer_id": identifier,
            "bus1_id": bus0,
            "bus2_id": bus1,
            "inferred": True,
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

    raw_polygons = payload.get("country_polygons", [])
    if not isinstance(raw_polygons, list) or len(raw_polygons) > 100:
        raise ValueError("국경 GeoJSON 폴리곤 개수가 올바르지 않습니다.")
    polygons = []
    total_vertices = 0
    for polygon in raw_polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("국경 GeoJSON 폴리곤이 올바르지 않습니다.")
        cleaned_rings = []
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                raise ValueError("국경 GeoJSON 경계선이 올바르지 않습니다.")
            cleaned_points = []
            for point in ring:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError("국경 GeoJSON 좌표가 올바르지 않습니다.")
                try:
                    lon, lat = float(point[0]), float(point[1])
                except (TypeError, ValueError) as exc:
                    raise ValueError("국경 GeoJSON 좌표가 올바르지 않습니다.") from exc
                if not (math.isfinite(lon) and math.isfinite(lat)
                        and -180 <= lon <= 180 and -90 <= lat <= 90):
                    raise ValueError("국경 GeoJSON 좌표 범위가 올바르지 않습니다.")
                cleaned_points.append([lon, lat])
                total_vertices += 1
                if total_vertices > 50000:
                    raise ValueError("국경 GeoJSON 좌표 개수가 너무 많습니다.")
            cleaned_rings.append(cleaned_points)
        polygons.append(cleaned_rings)

    raw_regions = payload.get("admin_regions", [])
    if not isinstance(raw_regions, list) or len(raw_regions) > 200:
        raise ValueError("행정구역 목록이 올바르지 않습니다.")
    regions = []
    for region in raw_regions:
        if not isinstance(region, dict):
            raise ValueError("행정구역 항목이 올바르지 않습니다.")
        name = str(region.get("name") or "Unknown")
        raw_region_polygons = region.get("polygons")
        if not isinstance(raw_region_polygons, list):
            raise ValueError("행정구역 경계 데이터가 올바르지 않습니다.")
        cleaned = []
        for polygon in raw_region_polygons:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError("행정구역 폴리곤이 올바르지 않습니다.")
            rings = []
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4:
                    raise ValueError("행정구역 경계선이 올바르지 않습니다.")
                points = []
                for point in ring:
                    if not isinstance(point, list) or len(point) != 2:
                        raise ValueError("행정구역 좌표가 올바르지 않습니다.")
                    try:
                        lon, lat = float(point[0]), float(point[1])
                    except (TypeError, ValueError) as exc:
                        raise ValueError("행정구역 좌표가 올바르지 않습니다.") from exc
                    if not (math.isfinite(lon) and math.isfinite(lat)
                            and -180 <= lon <= 180 and -90 <= lat <= 90):
                        raise ValueError("행정구역 좌표 범위가 올바르지 않습니다.")
                    total_vertices += 1
                    if total_vertices > 120000:
                        raise ValueError("행정구역 경계 좌표가 너무 많습니다.")
                    points.append([lon, lat])
                rings.append(points)
            cleaned.append(rings)
        regions.append({"name": name, "polygons": cleaned})

    return {"buses": buses, "lines": lines, "transformers": transformers,
            "generators": [], "powerplants": plants, "country_polygons": polygons,
            "admin_regions": regions}
