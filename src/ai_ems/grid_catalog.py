"""Read-only grid catalog and validated OSM map preview for K-EMS PoC.

A geographic base network is NOT an operational AC load-flow model.
Keep map availability separate from power-flow capability.

Capabilities:
- analysis_available: the full KPG physics-analysis suite (kept for backward compatibility;
  False for Mongolia even when its Security Analysis is available).
- security_analysis_available: line N-1 Security Analysis can run on a validated model.
- sensitivity_analysis_available / generator_contingency_available: per-feature flags.
- pf_preview_available: a PF preview asset exists for the map (validated on every request).
- chat_available: the AI Assistant may be used. For KPG this is the existing analysis
  agent; Mongolia queries the validated PF preview and calls its own Security and
  Sensitivity services, gated independently by their model validation status.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

KPG_GRID_ID = "kr-kpg193"
MONGOLIA_GRID_ID = "mn-osm-35k"
KOREA_OSM_GRID_ID = "kr-osm-transmission"
KOREA_OSM_NOTE = "공개 OSM 기반 한국 송전망 지도 모델이며, 전기적 모델 검증 전으로 계통해석은 지원하지 않습니다."
MAX_PREVIEW_BUSES = 50000
MAX_PREVIEW_LINES = 100000
MAX_PREVIEW_PLANTS = 100000
MAX_PREVIEW_TRANSFORMERS = 100000
MAX_BOUNDARY_POLYGONS = 5000
MAX_BOUNDARY_VERTICES = 100000
# Korean land layer: generous lon/lat box around the mainland, Jeju, Ulleungdo and Dokdo.
KOREA_LAND_BOUNDS = (124.0, 32.5, 132.5, 39.0)


def list_grids(
    mongolia_map_file: Path,
    mongolia_pf_preview_file: Path | None = None,
    mongolia_security_status: dict[str, Any] | None = None,
    korea_map_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Return supported grids with independently gated map/analysis capabilities."""
    map_ready = mongolia_map_file.is_file()
    pf_preview_ready = bool(mongolia_pf_preview_file and mongolia_pf_preview_file.is_file())
    security_ready = bool(map_ready and mongolia_security_status and mongolia_security_status.get("available") is True)
    sensitivity_ready = bool(security_ready and mongolia_security_status.get("sensitivity_available") is True)
    sensitivity_note = (
        None if sensitivity_ready
        else (mongolia_security_status or {}).get("sensitivity_reason") or security_note_default(mongolia_security_status)
    )
    security_note = (
        None if security_ready
        else (mongolia_security_status or {}).get("reason") or "몽골 PyPowSyBl 모델 검증 정보가 없습니다."
    )
    korea_ready = False
    if korea_map_file is not None:
        try:
            load_korea_map(korea_map_file)
            korea_ready = True
        except (OSError, ValueError):
            pass  # One unavailable map must not break the other grids.
    return [
        {
            "id": KPG_GRID_ID,
            "country": "KR",
            "name": "KPG-193 시험계통",
            "source": "KPG / local MATPOWER case",
            "format": "MATPOWER",
            "map_kind": "test_case",
            "map_available": True,
            "analysis_available": True,
            "pf_preview_available": True,
            "security_analysis_available": True,
            "sensitivity_analysis_available": True,
            "generator_contingency_available": True,
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
            "map_kind": "geographic",
            "map_available": map_ready,
            "analysis_available": False,
            "pf_preview_available": map_ready and pf_preview_ready,
            "security_analysis_available": security_ready,
            "security_analysis_note": security_note,
            "sensitivity_analysis_available": sensitivity_ready,
            "sensitivity_analysis_note": sensitivity_note,
            "generator_contingency_available": False,
            "chat_available": map_ready and pf_preview_ready,
            "chat_mode": "mongolia_analysis",
            "status": "map_ready" if map_ready else "preparing",
            "note": (
                "합성 AC Power Flow 미리보기는 조회 전용이며 웹 요청 시 재계산하지 않습니다. "
                "실제 몽골 운영 데이터가 아니며, 선로·변압기 정수와 발전소 접속은 일부 미검증입니다. "
                "선로 N-1 Security Analysis와 계통 분리 없는 사고의 선로 조류 Sensitivity(합성 발전기 주입 민감도)는 "
                "검증된 PyPowSyBl 합성 모델로 패널과 AI Assistant에서 실행할 수 있습니다. "
                "Security·Sensitivity는 각각 모델 검증 상태에 따라 제공되며, 분석 불가 시에도 PF 조회는 가능합니다. "
                "Redispatch·발전기 탈락 분석은 지원하지 않습니다."
            ),
        },
        {
            "id": KOREA_OSM_GRID_ID, "country": "KR",
            "name": "한국 OSM 송전망 (154/345/765 kV)",
            "source": "OpenStreetMap / Geofabrik; WRI GPPD v1.3.0",
            "format": "validated geographic JSON", "map_kind": "geographic",
            "map_available": korea_ready, "analysis_available": False,
            "pf_preview_available": False, "security_analysis_available": False,
            "sensitivity_analysis_available": False, "generator_contingency_available": False,
            "chat_available": False, "chat_mode": "disabled",
            "status": "map_ready" if korea_ready else "unavailable", "note": KOREA_OSM_NOTE,
        },
    ]


def security_note_default(status: dict[str, Any] | None) -> str:
    return (status or {}).get("reason") or "몽골 PyPowSyBl 모델 검증 정보가 없습니다."


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
    """Keep the existing Mongolia response contract unchanged."""
    return load_geographic_map(map_file)


def load_geographic_map(map_file: Path, *, preserve_provenance: bool = False) -> dict[str, Any]:
    """Validate the generated map file before sending it to the browser."""
    if not map_file.is_file():
        raise FileNotFoundError("몽골 지도 데이터가 아직 생성되지 않았습니다.")

    if map_file.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("지도 파일이 허용 크기를 초과했습니다.")
    try:
        payload = json.loads(map_file.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite_json)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
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
    if not isinstance(raw_polygons, list) or len(raw_polygons) > MAX_BOUNDARY_POLYGONS:
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
                if total_vertices > MAX_BOUNDARY_VERTICES:
                    raise ValueError("국경 GeoJSON 좌표 개수가 너무 많습니다.")
            if cleaned_points[0] != cleaned_points[-1]:
                raise ValueError("국경 GeoJSON 경계선이 닫혀 있지 않습니다.")
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
                    if total_vertices > MAX_BOUNDARY_VERTICES + 70000:
                        raise ValueError("행정구역 경계 좌표가 너무 많습니다.")
                    points.append([lon, lat])
                if points[0] != points[-1]:
                    raise ValueError("행정구역 경계선이 닫혀 있지 않습니다.")
                rings.append(points)
            cleaned.append(rings)
        regions.append({"name": name, "polygons": cleaned})

    result = {"buses": buses, "lines": lines, "transformers": transformers,
            "generators": [], "powerplants": plants, "country_polygons": polygons,
            "admin_regions": regions}
    if preserve_provenance:
        _preserve_geographic_provenance(payload, result)
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"비유한 JSON 숫자: {value}")


def _preserve_geographic_provenance(payload: dict, result: dict) -> None:
    """Shared extension for geographic maps; never infer absent properties."""
    fields = {
        "buses": ("osm_id", "source", "inferred", "inference", "substation_candidates", "component_id",
                  "osm_node_tags", "shared_support_branching_requires_review"),
        "lines": ("osm_id", "source", "inferred", "classification_evidence", "electrical_parameters_source",
                  "tags", "power", "component_id", "circuits_modelled", "circuits_source"),
        "powerplants": ("source", "original_source", "original_url", "capacity_year", "geolocation_source",
                        "license", "current_operational_status", "matching_status", "inferred"),
    }
    vertices = 0
    for collection, names in fields.items():
        seen = set()
        id_key = {"buses": "bus_id", "lines": "line_id", "powerplants": "record_id"}[collection]
        for raw, normalized in zip(payload.get(collection, []), result[collection]):
            identifier = normalized[id_key]
            if identifier in seen or len(identifier) > 256:
                raise ValueError(f"{collection} ID가 중복되거나 너무 깁니다.")
            seen.add(identifier)
            normalized.update({key: raw[key] for key in names if key in raw})
            if "inferred" in raw and not isinstance(raw["inferred"], bool):
                raise ValueError("설비 추정 여부는 boolean이어야 합니다.")
            if "source" in raw and not isinstance(raw["source"], str):
                raise ValueError("설비 출처 형식 오류")
            if "coordinates" in raw and collection == "lines":
                coords = raw["coordinates"]
                if not isinstance(coords, list) or len(coords) < 2:
                    raise ValueError("선로 경로 좌표가 올바르지 않습니다.")
                cleaned = []
                for p in coords:
                    if not isinstance(p, list) or len(p) != 2 or any(isinstance(v, bool) for v in p):
                        raise ValueError("선로 경로 좌표 형식 오류")
                    try:
                        lon, lat = map(float, p)
                    except (ValueError, TypeError) as exc:
                        raise ValueError("선로 경로 좌표 형식 오류") from exc
                    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        raise ValueError("선로 경로 좌표 범위 오류")
                    cleaned.append([lon, lat])
                    vertices += 1
                    if vertices > 500000:
                        raise ValueError("선로 경로 좌표가 너무 많습니다.")
                normalized["coordinates"] = cleaned
    for name in ("metadata", "schema_version", "grid_id", "country"):
        if name in payload:
            result[name] = payload[name]


def load_korea_map(map_file: Path) -> dict[str, Any]:
    """Load only the operator-configured file; revalidate whenever its identity changes."""
    path = Path(map_file).resolve(strict=True)
    stat = path.stat()
    return _cached_korea_map(str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=2)
def _cached_korea_map(path: str, *file_identity: int) -> dict[str, Any]:
    data = load_geographic_map(Path(path), preserve_provenance=True)
    meta = data.get("metadata")
    if (data.get("schema_version") != "kr-grid-preview-1.0" or data.get("grid_id") != KOREA_OSM_GRID_ID
            or data.get("country") != "KR" or not isinstance(meta, dict)
            or meta.get("model_kind") != "geographic_topology_only"
            or meta.get("ac_pf_readiness") != "NOT_READY" or meta.get("operational") is not False):
        raise ValueError("한국 OSM 지도 메타데이터가 올바르지 않습니다.")
    buses = {b["bus_id"]: b for b in data["buses"]}
    for b in buses.values():
        if b["voltage_kv"] not in {154, 345, 765} or not re.fullmatch(r"node/[1-9]\d*", str(b.get("osm_id", ""))):
            raise ValueError("한국 모선 전압 또는 OSM ID 오류")
    for line in data["lines"]:
        if not re.fullmatch(r"way/[1-9]\d*", str(line.get("osm_id", ""))) or line["bus1_id"] == line["bus2_id"]:
            raise ValueError("한국 선로 OSM ID 또는 양단 오류")
        a, b = buses[line["bus1_id"]], buses[line["bus2_id"]]
        if a["voltage_kv"] != line["voltage_kv"] or b["voltage_kv"] != line["voltage_kv"]:
            raise ValueError("한국 선로와 모선의 전압 불일치")
        coords = line.get("coordinates")
        if coords and (coords[0] != [a["longitude"], a["latitude"]] or coords[-1] != [b["longitude"], b["latitude"]]):
            raise ValueError("한국 선로 경로와 양단 모선 불일치")
    if data["transformers"] or any(p["mapped_bus_id"] is not None for p in data["powerplants"]):
        raise ValueError("한국 지도에 검증되지 않은 전기적 접속이 포함되었습니다.")
    # The OSM country relation includes the territorial sea; only an imported land layer is drawn.
    land = meta.get("land_boundary")
    if not (isinstance(land, dict) and land.get("kind") == "land_polygons"):
        data["country_polygons"] = []
    west, south, east, north = KOREA_LAND_BOUNDS
    if any(not (west <= lon <= east and south <= lat <= north)
           for polygon in data["country_polygons"] for ring in polygon for lon, lat in ring):
        raise ValueError("한국 육지 경계 좌표가 범위를 벗어났습니다.")
    return data
