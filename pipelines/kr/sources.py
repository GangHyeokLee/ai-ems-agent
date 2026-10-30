"""Read-only input adapters for the Korea OSM topology (map JSON, PyPSA NetCDF, OSM extract).

Every adapter returns the same ``Graph`` (buses keyed by ID, lines with both end bus IDs) so the
same analysis runs on the source map, the deployed AI-EMS map and ``base.nc``. Inputs are never
written; each loaded file is recorded with its SHA256.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_ems.grid_catalog import load_korea_map  # noqa: E402  (shared map validator)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe(path: Path, role: str) -> dict[str, Any]:
    path = Path(path).resolve(strict=True)
    return {"role": role, "file": path.name, "path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


@dataclass
class Graph:
    """Undirected bus/line graph. ``lines`` keep the original direction for reporting only."""
    source: str
    buses: dict[str, dict[str, Any]]
    lines: list[dict[str, Any]]
    layers: dict[str, list] = field(default_factory=dict)   # optional OSM layers (substations, HVDC ...)
    metadata: dict[str, Any] = field(default_factory=dict)


def validate_graph(source: str, buses: list[dict], lines: list[dict]) -> Graph:
    """Reject duplicate IDs, unknown line ends and non-finite coordinates instead of guessing."""
    by_id: dict[str, dict] = {}
    for bus in buses:
        bus_id = bus.get("bus_id")
        if not isinstance(bus_id, str) or not bus_id:
            raise ValueError(f"{source}: 모선 ID가 없습니다.")
        if bus_id in by_id:
            raise ValueError(f"{source}: 중복 모선 ID {bus_id}")
        lat, lon, kv = bus.get("latitude"), bus.get("longitude"), bus.get("voltage_kv")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon, kv)) \
                or not (-90 <= lat <= 90 and -180 <= lon <= 180) or kv <= 0:
            raise ValueError(f"{source}: 모선 {bus_id} 좌표 또는 전압 오류")
        by_id[bus_id] = bus
    seen = set()
    for line in lines:
        line_id = line.get("line_id")
        if not isinstance(line_id, str) or not line_id:
            raise ValueError(f"{source}: 선로 ID가 없습니다.")
        if line_id in seen:
            raise ValueError(f"{source}: 중복 선로 ID {line_id}")
        seen.add(line_id)
        for end in ("bus1_id", "bus2_id"):
            if line.get(end) not in by_id:
                raise ValueError(f"{source}: 선로 {line_id}의 {end}={line.get(end)!r} 모선이 없습니다.")
    return Graph(source=source, buses=by_id, lines=list(lines))


def load_map_graph(path: Path) -> Graph:
    """Korea map JSON (source ``kr_grid_preview.json`` or the imported AI-EMS file)."""
    path = Path(path)
    data = load_korea_map(path)                       # schema, voltage, OSM ID and route checks
    raw = json.loads(path.read_text(encoding="utf-8"))  # optional OSM layers the map API does not serve
    graph = validate_graph(path.name, data["buses"], data["lines"])
    graph.layers = {name: raw.get(name) or [] for name in ("substations", "osm_transformers", "hvdc", "unclassified_lines")}
    graph.metadata = data.get("metadata") or {}
    return graph


def _strings(dataset) -> list[str]:
    return [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in dataset[()]]


def load_netcdf_graph(path: Path) -> Graph:
    """PyPSA ``base.nc`` read with h5py (NetCDF4/HDF5); only bus/line topology fields are used."""
    import h5py
    with h5py.File(path, "r") as nc:
        try:
            bus_ids, line_ids = _strings(nc["buses_i"]), _strings(nc["lines_i"])
            xs, ys, kvs = nc["buses_x"][()], nc["buses_y"][()], nc["buses_v_nom"][()]
            bus0, bus1, line_kv = _strings(nc["lines_bus0"]), _strings(nc["lines_bus1"]), nc["lines_v_nom"][()]
            bus_osm = _strings(nc["buses_osm_id"]) if "buses_osm_id" in nc else [None] * len(bus_ids)
            line_osm = _strings(nc["lines_osm_id"]) if "lines_osm_id" in nc else [None] * len(line_ids)
        except KeyError as exc:
            raise ValueError(f"{Path(path).name}: PyPSA bus/line 변수가 없습니다: {exc}") from exc
        meta = nc.attrs.get("meta")
    # zip() would silently drop trailing entries; every per-bus and per-line array must match its index.
    for kind, arrays in (("buses", dict(buses_i=bus_ids, buses_x=xs, buses_y=ys, buses_v_nom=kvs, buses_osm_id=bus_osm)),
                         ("lines", dict(lines_i=line_ids, lines_bus0=bus0, lines_bus1=bus1, lines_v_nom=line_kv,
                                        lines_osm_id=line_osm))):
        lengths = {name: len(values) for name, values in arrays.items()}
        if len(set(lengths.values())) != 1:
            raise ValueError(f"{Path(path).name}: {kind} 배열 길이가 서로 다릅니다: {lengths}")
    buses = [dict(bus_id=b, longitude=float(x), latitude=float(y), voltage_kv=float(v), osm_id=o)
             for b, x, y, v, o in zip(bus_ids, xs, ys, kvs, bus_osm)]
    lines = [dict(line_id=i, bus1_id=a, bus2_id=b, voltage_kv=float(v), osm_id=o)
             for i, a, b, v, o in zip(line_ids, bus0, bus1, line_kv, line_osm)]
    graph = validate_graph(Path(path).name, buses, lines)
    if isinstance(meta, (bytes, str)):
        try:
            graph.metadata = json.loads(meta)
        except ValueError:
            graph.metadata = {"raw": str(meta)}
    return graph


def load_osm_ways(path: Path) -> dict[str, dict[str, Any]]:
    """Processed OSM power objects (``osm_power_objects.json``): way ID -> tags and node references."""
    objects = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(objects, list):
        raise ValueError("OSM 객체 파일은 목록이어야 합니다.")
    return {o["osm_id"]: dict(tags=o.get("tags") or {}, node_refs=o.get("node_refs") or [])
            for o in objects if isinstance(o, dict) and str(o.get("osm_id", "")).startswith("way/")}


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
