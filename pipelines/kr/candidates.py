"""Review candidates for possible missing connections between components. Never modifies the graph.

Each candidate records both sides, the evidence found in the data and what would be needed to
confirm it. Candidates are hypotheses for review (``status=unverified``), not electrical facts:
distance, a shared substation polygon, a crossing or a shared node does not prove a connection,
and no transformer is inferred between voltages.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np

from .sources import Graph
from .topology import UnionFind

EARTH_RADIUS_M = 6371008.8   # IUGG mean radius; haversine error vs. WGS84 geodesic < 0.5 %
HISTOGRAM_M = (10, 50, 100, 200, 500)

NEEDS = {
    "same_substation_same_voltage": "변전소 단선도·모선(busbar) 구성 및 개폐 상태",
    "same_substation_cross_voltage": "변전소 변압기 목록·권선별 접속 모선·임피던스·탭",
    "shared_osm_node_cross_voltage": "해당 지지물이 병가 철탑인지 변전 설비인지 현장·설비 대장 확인",
    "nearby_bus_same_voltage": "두 선로 끝점이 같은 설비(인출구·모선)인지 OSM·위성영상·설비 대장 확인",
    "nearby_bus_cross_voltage": "인근 변전소의 변압 설비 존재 여부와 접속 모선 확인",
    "endpoint_near_line_same_voltage": "T 분기 접속인지 단순 근접·교차인지 OSM 노드 및 설비 대장 확인",
    "excluded_way_bridge": "제외된 OSM 선로의 전압·주파수·운전 상태 확인(태그 보완 후 재구성 여부 판단)",
}
REASONS = {
    "same_substation_same_voltage": "같은 OSM 변전소 영역 안에 같은 전압의 서로 다른 연결 성분 끝점이 있음(변전소 내부 모선 연결이 OSM에 없음)",
    "same_substation_cross_voltage": "같은 OSM 변전소 영역 안에 다른 전압 연결 성분이 있음(변압기 접속은 모델에 없음)",
    "shared_osm_node_cross_voltage": "하나의 OSM 노드를 다른 전압 선로가 함께 사용함(병가 지지물 또는 변전 설비)",
    "nearby_bus_same_voltage": "같은 전압의 서로 다른 성분 모선이 기준 거리 이내이나 OSM 노드가 다름",
    "nearby_bus_cross_voltage": "다른 전압의 서로 다른 성분 모선이 기준 거리 이내",
    "endpoint_near_line_same_voltage": "선로 끝점(차수 1)이 같은 전압의 다른 성분 선로 경로에 기준 거리 이내로 근접",
    "excluded_way_bridge": "전처리에서 제외된 OSM 선로가 서로 다른 연결 성분의 OSM 노드를 공유함",
}


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres; accepts scalars or numpy arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def nearest_on_segment(lat, lon, lat1, lon1, lat2, lon2):
    """Nearest point on each segment to the point, in a local equirectangular plane around the point.

    Returns (distance m, latitude, longitude) arrays of that nearest point on the segment.
    """
    k = math.radians(1) * EARTH_RADIUS_M
    c = math.cos(math.radians(lat))
    ax, ay = (np.asarray(lon1) - lon) * k * c, (np.asarray(lat1) - lat) * k
    bx, by = (np.asarray(lon2) - lon) * k * c, (np.asarray(lat2) - lat) * k
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = np.where(length2 > 0, np.clip(-(ax * dx + ay * dy) / np.where(length2 > 0, length2, 1), 0, 1), 0)
    px, py = ax + t * dx, ay + t * dy
    return np.hypot(px, py), lat + py / k, lon + px / (k * c)


def point_segment_m(lat, lon, lat1, lon1, lat2, lon2):
    """Point-to-segment distance (m) in a local equirectangular plane around the point (arrays)."""
    return nearest_on_segment(lat, lon, lat1, lon1, lat2, lon2)[0]


def point_in_geojson(lon: float, lat: float, geometry: dict | None) -> bool:
    """Even-odd test for GeoJSON Polygon/MultiPolygon (outer ring and holes)."""
    if not geometry or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return False
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for polygon in polygons:
        inside = False
        for ring in polygon:
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
        if inside:
            return True
    return False


def _side(prefix: str, graph: Graph, bus_id: str, component: int) -> dict[str, Any]:
    bus = graph.buses[bus_id]
    return {f"{prefix}_bus_id": bus_id, f"{prefix}_osm_id": bus.get("osm_id"), f"{prefix}_component": component,
            f"{prefix}_voltage_kv": bus["voltage_kv"], f"{prefix}_latitude": bus["latitude"],
            f"{prefix}_longitude": bus["longitude"],
            f"{prefix}_osm_tags": _tags(bus.get("osm_node_tags"))}


def _tags(tags: dict | None) -> str:
    keep = ("power", "name", "ref", "operator", "voltage", "substation", "structure", "design")
    return ";".join(f"{k}={tags[k]}" for k in keep if tags and tags.get(k)) if tags else ""


def _candidate(kind: str, graph: Graph, a: str, ca: int, b: str, cb: int, distance: float | None,
               evidence: str, distance_target: str = "bus_to_bus", roles=("measured_bus", "measured_bus"),
               keep_order: bool = False) -> dict[str, Any]:
    """``distance_m`` is measured as ``distance_target`` says; ``*_bus_role`` tells what each bus is."""
    if not keep_order and (ca, a) > (cb, b):
        a, ca, b, cb = b, cb, a, ca
        roles = roles[::-1]
    return dict(candidate_type=kind, **_side("a", graph, a, ca), **_side("b", graph, b, cb),
                a_bus_role=roles[0], b_bus_role=roles[1], distance_target=distance_target,
                distance_m=None if distance is None else round(float(distance), 2), evidence=evidence, suspected_reason=REASONS[kind],
                required_information=NEEDS[kind], status="unverified")


def substation_candidates(graph: Graph, bus_component: dict[str, int]) -> list[dict[str, Any]]:
    """Buses whose point lies in the same OSM substation geometry (``substation_candidates``)."""
    members = defaultdict(list)
    for bus_id in sorted(graph.buses):
        for station in graph.buses[bus_id].get("substation_candidates") or []:
            members[station].append(bus_id)
    stations = {s["osm_id"]: s for s in graph.layers.get("substations", []) if isinstance(s, dict)}
    transformers = [t for t in graph.layers.get("osm_transformers", []) if isinstance(t, dict)]
    out = []
    for station, buses in sorted(members.items()):
        by_component = defaultdict(list)
        for bus_id in buses:
            by_component[bus_component[bus_id]].append(bus_id)
        if len(by_component) < 2:
            continue
        geometry = (stations.get(station) or {}).get("geometry")
        name = (stations.get(station) or {}).get("name") or ""
        inside = None
        if geometry and transformers:
            inside = sum(point_in_geojson(*t["geometry"]["coordinates"], geometry) for t in transformers
                         if (t.get("geometry") or {}).get("type") == "Point")
        comps = sorted(by_component)
        for i, ca in enumerate(comps):
            for cb in comps[i + 1:]:
                pairs = [(haversine_m(graph.buses[a]["latitude"], graph.buses[a]["longitude"],
                                      graph.buses[b]["latitude"], graph.buses[b]["longitude"]), a, b)
                         for a in by_component[ca] for b in by_component[cb]]
                distance, a, b = min(pairs)
                same = graph.buses[a]["voltage_kv"] == graph.buses[b]["voltage_kv"]
                kind = "same_substation_same_voltage" if same else "same_substation_cross_voltage"
                evidence = f"substation={station}" + (f" name={name}" if name else "")
                if not same and inside is not None:
                    evidence += f" osm_transformers_inside={inside}"
                out.append(_candidate(kind, graph, a, ca, b, cb, distance, evidence) | {"substation_osm_id": station})
    return out


def shared_node_candidates(graph: Graph, bus_component: dict[str, int]) -> list[dict[str, Any]]:
    by_node = defaultdict(list)
    for bus_id in sorted(graph.buses):
        if graph.buses[bus_id].get("osm_id"):
            by_node[graph.buses[bus_id]["osm_id"]].append(bus_id)
    out = []
    for node, buses in sorted(by_node.items()):
        for i, a in enumerate(buses):
            for b in buses[i + 1:]:
                if bus_component[a] != bus_component[b]:
                    out.append(_candidate("shared_osm_node_cross_voltage", graph, a, bus_component[a], b,
                                          bus_component[b], 0.0, f"osm_node={node}"))
    return out


def nearby_bus_candidates(graph: Graph, bus_component: dict[str, int], max_m: float):
    """Nearest bus pair per component pair within ``max_m``; histogram of nearest distances."""
    ids = sorted(graph.buses)
    lat = np.array([graph.buses[b]["latitude"] for b in ids])
    lon = np.array([graph.buses[b]["longitude"] for b in ids])
    comp = np.array([bus_component[b] for b in ids])
    kv = np.array([graph.buses[b]["voltage_kv"] for b in ids])
    node = [graph.buses[b].get("osm_id") for b in ids]
    radius = max(max_m, max(HISTOGRAM_M))
    order = np.argsort(lat, kind="stable")
    sorted_lat = lat[order]
    dlat = math.degrees(radius / EARTH_RADIUS_M)
    best: dict[tuple, tuple] = {}
    for i in range(len(ids)):
        lo, hi = np.searchsorted(sorted_lat, [lat[i] - dlat, lat[i] + dlat])
        js = order[lo:hi]
        js = js[(js > i) & (comp[js] != comp[i])]
        if not len(js):
            continue
        d = haversine_m(lat[i], lon[i], lat[js], lon[js])
        for j, dist in zip(js[d <= radius], d[d <= radius]):
            if node[i] is not None and node[i] == node[j]:
                continue  # same OSM node: reported as shared_osm_node_cross_voltage
            same = bool(kv[i] == kv[j])
            key = (min(comp[i], comp[j]), max(comp[i], comp[j]), same)
            if key not in best or (dist, ids[i], ids[j]) < best[key]:
                best[key] = (float(dist), ids[i], ids[j])
    histogram = {kind: {f"<= {m} m": sum(1 for (_, _, same), (d, *_) in best.items()
                                         if same == (kind == "same_voltage") and d <= m) for m in HISTOGRAM_M}
                 for kind in ("same_voltage", "cross_voltage")}
    out = []
    for (ca, cb, same), (dist, a, b) in sorted(best.items()):
        if dist <= max_m:
            kind = "nearby_bus_same_voltage" if same else "nearby_bus_cross_voltage"
            out.append(_candidate(kind, graph, a, bus_component[a], b, bus_component[b], dist,
                                  f"nearest bus pair between components {ca} and {cb}"))
    return out, histogram


def endpoint_line_candidates(graph: Graph, bus_component: dict[str, int], degree, max_m: float):
    """Dangling buses near a same-voltage route segment of another component (T-junction without node)."""
    seg = defaultdict(list)
    for line in graph.lines:
        coords = line.get("coordinates") or [
            [graph.buses[line["bus1_id"]]["longitude"], graph.buses[line["bus1_id"]]["latitude"]],
            [graph.buses[line["bus2_id"]]["longitude"], graph.buses[line["bus2_id"]]["latitude"]]]
        cid = bus_component[line["bus1_id"]]
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            seg[line["voltage_kv"]].append((x1, y1, x2, y2, cid, line["line_id"]))
    arrays = {kv: (np.array([s[:4] for s in rows], dtype=float), np.array([s[4] for s in rows]),
                   [s[5] for s in rows]) for kv, rows in seg.items()}
    dlat = math.degrees(max_m / EARTH_RADIUS_M)
    line_by_id = {line["line_id"]: line for line in graph.lines}
    out = []
    for bus_id in sorted(graph.buses):
        if degree[bus_id] != 1:
            continue
        bus = graph.buses[bus_id]
        if bus["voltage_kv"] not in arrays:
            continue
        xy, comps, line_ids = arrays[bus["voltage_kv"]]
        dlon = dlat / max(0.1, math.cos(math.radians(bus["latitude"])))
        mask = ((np.minimum(xy[:, 1], xy[:, 3]) <= bus["latitude"] + dlat) & (np.maximum(xy[:, 1], xy[:, 3]) >= bus["latitude"] - dlat)
                & (np.minimum(xy[:, 0], xy[:, 2]) <= bus["longitude"] + dlon) & (np.maximum(xy[:, 0], xy[:, 2]) >= bus["longitude"] - dlon)
                & (comps != bus_component[bus_id]))
        idx = np.nonzero(mask)[0]
        if not len(idx):
            continue
        d, near_lat, near_lon = nearest_on_segment(bus["latitude"], bus["longitude"],
                                                   xy[idx, 1], xy[idx, 0], xy[idx, 3], xy[idx, 2])
        nearest = {}
        for k, dist, nlat, nlon in zip(idx, d, near_lat, near_lon):
            if dist <= max_m and (comps[k] not in nearest or dist < nearest[comps[k]][0]):
                nearest[comps[k]] = (float(dist), line_ids[k], float(nlat), float(nlon))
        for cid, (dist, line_id, nlat, nlon) in sorted(nearest.items()):
            line = line_by_id[line_id]
            other = min((line["bus1_id"], line["bus2_id"]),
                        key=lambda b: haversine_m(bus["latitude"], bus["longitude"], graph.buses[b]["latitude"], graph.buses[b]["longitude"]))
            # a = the dangling bus (measured); b = an end bus of the approached line, for reference only.
            row = _candidate("endpoint_near_line_same_voltage", graph, bus_id, bus_component[bus_id], other, int(cid), dist,
                             f"distance_m = dangling bus a to the nearest point on the route of line {line_id}; "
                             f"b is the nearer end bus of that line, not the measured point",
                             distance_target="a_bus_to_line_route",
                             roles=("measured_bus", "end_bus_of_approached_line"), keep_order=True)
            out.append(row | {"line_id": line_id, "route_nearest_latitude": round(nlat, 7),
                              "route_nearest_longitude": round(nlon, 7),
                              "a_to_b_bus_distance_m": round(float(haversine_m(
                                  bus["latitude"], bus["longitude"],
                                  graph.buses[other]["latitude"], graph.buses[other]["longitude"])), 2)})
    return out


def excluded_way_candidates(graph: Graph, bus_component: dict[str, int], ways: dict, excluded: list[dict]):
    """Excluded OSM ways that share OSM nodes with two or more components (exact node identity)."""
    reasons = {e["osm_id"]: e.get("reason") for e in excluded if isinstance(e, dict) and e.get("osm_id")}
    node_components = defaultdict(set)
    node_bus = {}
    for bus_id, bus in graph.buses.items():
        if bus.get("osm_id"):
            node_bus.setdefault(bus["osm_id"], bus_id)
    for line in graph.lines:
        way = ways.get(line.get("osm_id"))
        if way is None:
            continue
        for ref in way["node_refs"]:
            node_components[f"node/{ref}"].add(bus_component[line["bus1_id"]])
    out, touching, bridging = [], 0, []
    for way_id in sorted(reasons):
        way = ways.get(way_id)
        if way is None:
            continue
        touched = defaultdict(set)
        for ref in way["node_refs"]:
            for cid in node_components.get(f"node/{ref}", ()):
                touched[cid].add(f"node/{ref}")
        if touched:
            touching += 1
        if len(touched) < 2:
            continue
        bridging.append(len(touched))
        comps = sorted(touched)
        rep, role = {}, {}
        for cid in comps:
            nodes = sorted(touched[cid])
            bus = next((node_bus[n] for n in nodes if n in node_bus and bus_component[node_bus[n]] == cid), None)
            role[cid] = "bus_on_excluded_way_node"
            if bus is None:  # shared interior route node: report the component's lowest bus ID for location
                bus, role[cid] = min(b for b, c in bus_component.items() if c == cid), "component_representative_bus"
            rep[cid] = bus
        tags = way["tags"]
        evidence = (f"way={way_id} reason={reasons[way_id]} power={tags.get('power', '')} "
                    f"voltage={tags.get('voltage', '')} components={comps}")
        # One candidate per component pair, so a way touching three or more components loses none.
        for i, ca in enumerate(comps):
            for cb in comps[i + 1:]:
                out.append(_candidate("excluded_way_bridge", graph, rep[ca], ca, rep[cb], cb, None, evidence,
                                      distance_target="not_applicable", roles=(role[ca], role[cb]))
                           | {"excluded_way_osm_id": way_id, "excluded_reason": reasons[way_id],
                              "excluded_way_voltage": tags.get("voltage", ""),
                              "excluded_way_components": ";".join(map(str, comps))})
    return out, dict(excluded_ways=len(reasons), excluded_ways_found_in_osm_objects=sum(w in ways for w in reasons),
                     excluded_ways_touching_model_nodes=touching, excluded_ways_bridging_components=len(bridging),
                     excluded_ways_touching_three_or_more_components=sum(n >= 3 for n in bridging),
                     excluded_way_bridge_pair_candidates=len(out))


def landmass_labels(graph: Graph, land: dict | None) -> dict[str, str] | None:
    """Land polygon containing each bus (display land layer); ``mainland`` = largest polygon."""
    if not land:
        return None
    polygons = land.get("polygons") or []
    known = [(name.split("_")[0], info["bounds"]) for name, info in
             sorted(((land.get("metadata") or {}).get("check_islands") or {}).items()) if info and info.get("bounds")]
    def area(ring):
        xs, ys = np.array([p[0] for p in ring]), np.array([p[1] for p in ring])
        return abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1))) / 2
    areas = [area(p[0]) for p in polygons]
    mainland = int(np.argmax(areas)) if areas else None
    ids = sorted(graph.buses)
    lat = np.array([graph.buses[b]["latitude"] for b in ids])
    lon = np.array([graph.buses[b]["longitude"] for b in ids])
    label = np.array(["outside_land_layer"] * len(ids), dtype=object)
    for index, polygon in enumerate(polygons):
        outer = np.array(polygon[0])
        box = (lon >= outer[:, 0].min()) & (lon <= outer[:, 0].max()) & (lat >= outer[:, 1].min()) & (lat <= outer[:, 1].max())
        candidates = np.nonzero(box & (label == "outside_land_layer"))[0]
        if not len(candidates):
            continue
        inside = np.zeros(len(candidates), dtype=bool)
        for ring in polygon:
            r = np.array(ring)
            x1, y1, x2, y2 = r[:-1, 0], r[:-1, 1], r[1:, 0], r[1:, 1]
            for start in range(0, len(candidates), 256):
                chunk = candidates[start:start + 256]
                py, px = lat[chunk][:, None], lon[chunk][:, None]
                with np.errstate(divide="ignore", invalid="ignore"):
                    hit = ((y1 > py) != (y2 > py)) & (px < x1 + (py - y1) * (x2 - x1) / (y2 - y1))
                inside[start:start + 256] ^= (hit.sum(axis=1) % 2).astype(bool)
        if index == mainland:
            name = "mainland"
        else:
            bounds = (outer[:, 0].min(), outer[:, 1].min(), outer[:, 0].max(), outer[:, 1].max())
            # Display polygons are simplified (50 m), so match the build's island bounds within 0.01 deg.
            name = next((n for n, b in known if max(abs(x - y) for x, y in zip(bounds, b)) < 0.01), None) \
                or f"island_{index}_{areas[index] * 111.32 ** 2 * math.cos(math.radians(outer[:, 1].mean())):.1f}km2"
        label[candidates[inside]] = name
    return dict(zip(ids, label.tolist()))


def hypothetical_merge(components: list[list[str]], candidates: list[dict], kinds: set[str]) -> int:
    """Component count if the given candidate types were all real connections (sensitivity only)."""
    uf = UnionFind(str(i) for i in range(len(components)))
    for c in candidates:
        if c["candidate_type"] in kinds:
            uf.union(str(c["a_component"]), str(c["b_component"]))
    return len(uf.groups())
