"""Validated loader for the Mongolia reference-only map layers (110 kV lines, substations).

Reference equipment is display data only. It is kept in its own JSON file so the base map
(``mn-osm-35k-map.json``) and its ``map_json_sha256`` binding to the PF preview stay unchanged.
The file pins the SHA-256 of the base map it was built against; a mismatch rejects the layers
(the base map and every analysis feature keep working). Reference IDs never collide with map
line or bus IDs, and nothing here is reachable from Security/Sensitivity code paths.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

SCHEMA = "mn-reference-layers/v1"
GRID_ID = "mn-osm-35k"
MAX_REFERENCE_LINES = 5000
MAX_REFERENCE_SUBSTATIONS = 5000


def _lon_lat(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} 좌표 형식이 잘못되었습니다.")
    try:
        lon, lat = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 좌표가 올바르지 않습니다.") from exc
    if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"{label} 좌표 범위가 올바르지 않습니다.")
    return [lon, lat]


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 값이 올바르지 않습니다.") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} 범위가 올바르지 않습니다.")
    return number


def load_mongolia_reference_layers(reference_file: Path, map_file: Path) -> dict[str, Any]:
    """Return only displayable reference equipment after checking it against the active base map."""
    if not reference_file.is_file():
        raise FileNotFoundError("몽골 참조 레이어 데이터가 없습니다.")
    if not map_file.is_file():
        raise FileNotFoundError("몽골 지도 데이터가 아직 생성되지 않았습니다.")
    map_bytes = map_file.read_bytes()
    try:
        payload = json.loads(reference_file.read_text(encoding="utf-8"))
        base_map = json.loads(map_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("몽골 참조 레이어 JSON을 읽을 수 없습니다.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or payload.get("grid_id") != GRID_ID:
        raise ValueError("몽골 참조 레이어 형식이 올바르지 않습니다.")
    if payload.get("reference_only") is not True:
        raise ValueError("참조 레이어는 reference_only 데이터여야 합니다.")
    if payload.get("base_map_sha256") != hashlib.sha256(map_bytes).hexdigest():
        raise ValueError("참조 레이어가 현재 몽골 지도와 다른 지도 기준으로 생성되었습니다.")

    model_ids = {str(line.get("line_id")) for line in base_map.get("lines", [])}
    model_ids |= {str(bus.get("bus_id")) for bus in base_map.get("buses", [])}
    model_ids |= {str(item.get("transformer_id")) for item in base_map.get("transformers", [])}
    seen: set[str] = set()

    def ref_id(record: dict, prefix: str) -> str:
        identifier = str(record.get("ref_id", "")).strip()
        if not identifier.startswith(prefix) or identifier in seen or identifier in model_ids:
            raise ValueError(f"참조 설비 ID가 올바르지 않습니다: {identifier or '(empty)'}")
        seen.add(identifier)
        return identifier

    raw_lines = payload.get("lines")
    raw_subs = payload.get("substations")
    if not isinstance(raw_lines, list) or not isinstance(raw_subs, list):
        raise ValueError("참조 레이어에는 lines 및 substations 배열이 필요합니다.")
    if len(raw_lines) > MAX_REFERENCE_LINES or len(raw_subs) > MAX_REFERENCE_SUBSTATIONS:
        raise ValueError("참조 설비 개수가 허용 범위를 벗어났습니다.")

    lines = []
    for record in raw_lines:
        if not isinstance(record, dict):
            raise ValueError("참조 선로 항목 형식이 잘못되었습니다.")
        identifier = ref_id(record, "REF-L-")
        if record.get("display") is not True:
            continue
        duplicate = record.get("duplicate_check") or {}
        lines.append({
            "ref_id": identifier,
            "osm_id": str(record.get("osm_id", "")),
            "osm_url": _text(record.get("osm_url")),
            "voltage_kv": _number(record.get("voltage_kv"), "참조 선로 전압"),
            "circuits_tag": _text(record.get("circuits_tag")),
            "name": _text(record.get("name")),
            "from": _lon_lat(record.get("from"), f"참조 선로 {identifier}"),
            "to": _lon_lat(record.get("to"), f"참조 선로 {identifier}"),
            "raw_path_km": _number(record.get("raw_path_km"), "참조 선로 길이"),
            "collapsed_bus_ids": [str(x) for x in record.get("collapsed_bus_ids") or []],
            "merged_osm_ids": [str(x) for x in duplicate.get("merged_osm_ids") or []],
            "region": _text(record.get("region")),
        })

    substations = []
    for record in raw_subs:
        if not isinstance(record, dict):
            raise ValueError("참조 변전소 항목 형식이 잘못되었습니다.")
        identifier = ref_id(record, "REF-S-")
        if record.get("display") is not True:
            continue
        longitude, latitude = _lon_lat([record.get("longitude"), record.get("latitude")], f"참조 변전소 {identifier}")
        volts = record.get("voltage_kv") or []
        if not isinstance(volts, list):
            raise ValueError(f"참조 변전소 {identifier}의 전압 형식이 잘못되었습니다.")
        nearest = record.get("nearest_map_bus") or {}
        substations.append({
            "ref_id": identifier,
            "osm_id": str(record.get("osm_id", "")),
            "osm_url": _text(record.get("osm_url")),
            "voltage_kv": [_number(v, "참조 변전소 전압") for v in volts],
            "substation_tag": _text(record.get("substation_tag")),
            "name": _text(record.get("name")),
            "longitude": longitude,
            "latitude": latitude,
            "nearest_map_bus_id": _text(nearest.get("bus_id")),
            "nearest_map_bus_km": _number(nearest.get("distance_km"), "참조 변전소 거리"),
            "model_bus_ids": [str(x) for x in record.get("model_bus_ids") or []],
            "merged_osm_ids": [str(x) for x in (record.get("duplicate_check") or {}).get("merged_osm_ids") or []],
            "region": _text(record.get("region")),
        })

    return {
        "grid_id": GRID_ID,
        "reference_only": True,
        "source": (payload.get("source") or {}).get("osm_extract"),
        "lines": lines,
        "substations": substations,
    }
