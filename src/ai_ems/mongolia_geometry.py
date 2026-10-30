"""Optional, hash-pinned display geometry; no model, GIS or source dependency."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

# Reviewed export from the existing 2026-10-15 OSM snapshot. Updating the
# display asset requires an explicit export/review; never trust its own hash.
DISPLAY_GEOMETRY_SHA256 = "2a75536d6b26c3996d60113538a32562660aff5330c8bfcd8cd7bd0d7b61f75b"


def load_mongolia_display_geometry(geometry_file: Path, map_file: Path) -> dict:
    if not geometry_file.is_file():
        raise FileNotFoundError("몽골 GIS 표시 자료가 없습니다.")
    content = geometry_file.read_bytes()
    if hashlib.sha256(content).hexdigest() != DISPLAY_GEOMETRY_SHA256:
        raise ValueError("몽골 GIS 표시 자료의 승인 해시가 다릅니다.")
    payload = json.loads(content)
    map_bytes = map_file.read_bytes()
    if payload["base_map_sha256"] != hashlib.sha256(map_bytes).hexdigest():
        raise ValueError("몽골 GIS 표시 자료와 현재 지도가 다릅니다.")
    base = json.loads(map_bytes)
    model = {item["line_id"]: item for item in base["lines"]}
    ids = set()
    if payload["schema"] != "mn-display-geometry/v1" or payload["display_only"] is not True:
        raise ValueError("몽골 GIS 표시 자료 형식이 다릅니다.")
    for record in payload["lines"]:
        identifier = record["line_id"]
        line = model.get(identifier)
        if identifier in ids or line is None or any(record[key] != line[key] for key in ("bus1_id", "bus2_id")):
            raise ValueError("몽골 GIS 표시 선로의 ID 또는 모선이 다릅니다.")
        ids.add(identifier)
        if not record["osm_parts"]:
            raise ValueError("몽골 GIS 원본 경로가 비어 있습니다.")
        for part in record["osm_parts"]:
            if len(part) < 2 or any(len(p) != 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p)
                                    or not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90) for p in part):
                raise ValueError("몽골 GIS 원본 경로 좌표가 잘못되었습니다.")
    excluded = {item["line_id"] for item in payload["excluded"]}
    if ids & excluded or ids | excluded != set(model):
        raise ValueError("몽골 GIS 표시 선로 집합이 현재 지도와 다릅니다.")
    return {"schema": payload["schema"], "grid_id": payload["grid_id"], "display_only": True,
            "base_map_sha256": payload["base_map_sha256"], "connectors": "omitted",
            "lines": payload["lines"], "excluded": payload["excluded"]}
