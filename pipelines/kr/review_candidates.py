"""Build an offline, read-only Korea OSM candidate review workbench.

Reuses topology CSV evidence; never writes inputs or applies a review to a network.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .candidates import haversine_m, point_in_geojson
from .sources import ROOT, describe, load_json, load_map_graph, sha256
from .topology import analyze

KIND = "same_substation_same_voltage"
SCHEMA = "kr-candidate-review-1"
STATES = {
    "unreviewed": "미검토", "needs_information": "추가 자료 필요",
    "osm_review_needed": "OSM 태그·형상 검토 필요", "electrical_evidence_obtained": "전기적 접속 근거 확보",
    "confirmed_not_connected": "실제 연결 아님으로 확인",
}
SCORE_POLICY = {
    "version": 1,
    "meaning": "검토 순서 점수이며 전기적 접속 확률이 아님",
    "distance": "<=50 m:30, <=100:24, <=200:16, <=500:8, >500:0",
    "both_inside_polygon": 20, "consistent_explicit_station_and_incident_line_voltage_tags": 15,
    "both_degree_one": 10, "station_component_count": "2:10, 3~4:5, >=5:0",
    "also_nearby_bus_same_voltage": 5,
    "missing_or_conflicting_evidence": "각 경고 -10 (누락/충돌 항목을 별도 표시), 최소 0",
    "bands": "A >=65, B >=40, C <40; A도 접속 확인 아님",
    "pair_order": "성분 쌍별 최고 점수 내림차순, 최소 거리 오름차순, 성분 쌍 순; 변전소 근거는 각각 보존",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def review_key(input_hash, identity_key):
    return f"kr-review-v1:{input_hash}:{identity_key}"


def voltage_tag(tags, kv):
    """missing / consistent / conflict; semicolon voltage lists use volts, never infer missing."""
    raw = (tags or {}).get("voltage")
    if not raw:
        return "missing"
    try:
        values = [float(v.strip()) for v in str(raw).split(";")]
        if not values or not all(math.isfinite(v) and v > 0 for v in values):
            return "conflict"
        return "consistent" if kv * 1000 in values else "conflict"
    except ValueError:
        return "conflict"


def covered_by_polygon(lon, lat, geometry):
    """Include polygon edges, as the source build's shapely intersects does.

    Ray casting alone classifies some portal nodes on an OSM boundary as outside.
    The segment tolerance is 1e-10 degrees (about 0.01 mm), not a proximity buffer.
    """
    if not geometry or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return False
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for polygon in polygons:
        for ring in polygon:
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                dx, dy = x2-x1, y2-y1
                length = math.hypot(dx, dy)
                if (min(x1,x2)-1e-10 <= lon <= max(x1,x2)+1e-10
                    and min(y1,y2)-1e-10 <= lat <= max(y1,y2)+1e-10
                    and abs((lon-x1)*dy-(lat-y1)*dx) <= 1e-10*length):
                    return True
    return point_in_geojson(lon, lat, geometry)


def priority(distance, inside, voltage, degrees, count, nearby, warnings):
    parts = dict(distance=next((score for limit, score in [(50, 30), (100, 24), (200, 16), (500, 8)]
                               if distance <= limit), 0),
                 boundary=20 if inside == [True, True] else 0,
                 voltage=15 if voltage == "consistent" else 0,
                 endpoints=10 if degrees == [1, 1] else 0,
                 station_complexity=10 if count == 2 else 5 if count <= 4 else 0,
                 nearby_overlap=5 if nearby else 0, missing_conflict=-10 * len(warnings))
    score = max(0, sum(parts.values()))
    return score, "A" if score >= 65 else "B" if score >= 40 else "C", parts


def build_review(graph, source_rows, input_hash, objects=()):
    topology = analyze(graph)
    bc, degree = topology["bus_component"], topology["degree"]
    lines_by_comp, incident = defaultdict(list), defaultdict(list)
    for line in graph.lines:
        lines_by_comp[bc[line["bus1_id"]]].append(line)
        for bus_id in (line["bus1_id"], line["bus2_id"]):
            incident[bus_id].append(line)
    fingerprints = {i: digest({"buses": members, "lines": sorted(
        (line["line_id"], *sorted((line["bus1_id"], line["bus2_id"]))) for line in lines_by_comp[i])})
                    for i, members in enumerate(topology["components"])}
    station_objects = {s["osm_id"]: s for s in graph.layers.get("substations", [])}
    station_components = defaultdict(set)
    for bid, bus in graph.buses.items():
        for station in bus.get("substation_candidates") or []:
            station_components[station].add(bc[bid])
    selected = [r for r in source_rows if r.get("candidate_type") == KIND]
    overlaps = {tuple(sorted((r["a_bus_id"], r["b_bus_id"]))) for r in source_rows
                if r.get("candidate_type") == "nearby_bus_same_voltage"}
    rows, used_groups = [], set()
    stations = {}
    for raw in selected:
        station_id = raw["substation_osm_id"]
        a, b = (graph.buses[raw[f"{p}_bus_id"]] for p in ("a", "b"))
        pair = sorted((bc[a["bus_id"]], bc[b["bus_id"]]))
        kv = a["voltage_kv"]
        if pair[0] == pair[1] or kv != b["voltage_kv"] or raw["distance_target"] != "bus_to_bus":
            raise ValueError("대상 후보의 성분/전압/거리 의미가 잘못되었습니다.")
        for prefix, bus in (("a", a), ("b", b)):
            if (int(raw[f"{prefix}_component"]) != bc[bus["bus_id"]]
                or float(raw[f"{prefix}_voltage_kv"]) != bus["voltage_kv"]
                or float(raw[f"{prefix}_latitude"]) != bus["latitude"]
                or float(raw[f"{prefix}_longitude"]) != bus["longitude"]
                or raw[f"{prefix}_osm_id"] != bus.get("osm_id")
                or station_id not in (bus.get("substation_candidates") or [])):
                raise ValueError("CSV와 원본 지도 후보의 식별자/좌표/변전소가 다릅니다. 분석을 다시 실행하세요.")
        measured = float(haversine_m(a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
        distance = float(raw["distance_m"])
        if not math.isfinite(distance) or abs(distance - measured) > 0.011:
            raise ValueError("CSV 모선 간 거리가 실제 좌표와 다릅니다.")
        group = (station_id, *pair, kv)
        if group in used_groups:
            raise ValueError("동일 변전소·성분 쌍·전압 후보가 중복되었습니다.")
        used_groups.add(group)
        station = station_objects.get(station_id, {"osm_id": station_id})
        geometry = station.get("geometry")
        polygon = geometry if (geometry or {}).get("type") in {"Polygon", "MultiPolygon"} else None
        inside = [covered_by_polygon(bus["longitude"], bus["latitude"], polygon) if polygon else None for bus in (a, b)]
        related = []
        if polygon:
            for obj in [*graph.layers.get("osm_transformers", []), *objects]:
                if (obj.get("tags") or {}).get("power") not in {"transformer", "busbar", "switch", "switchgear"}:
                    continue
                geo = obj.get("geometry") or {}
                if geo.get("type") == "Point":
                    within = covered_by_polygon(*geo["coordinates"], polygon)
                elif geo.get("type") == "LineString":
                    within = any(covered_by_polygon(*xy, polygon) for xy in geo["coordinates"])
                else:
                    within = False
                if within:
                    related.append({k: obj.get(k) for k in ("osm_id", "tags", "geometry")})
        related = sorted({o["osm_id"]: o for o in related}.values(), key=lambda o: o["osm_id"])
        stations[station_id] = dict(station, component_ids=sorted(station_components[station_id]),
                                    polygon_available=bool(polygon), related_objects=related)
        tags = [voltage_tag(station.get("tags"), kv)]
        connected_lines = sorted({l["line_id"]: l for bus in (a, b) for l in incident[bus["bus_id"]]}.values(),
                                 key=lambda l: l["line_id"])
        tags += [voltage_tag(l.get("tags"), kv) for l in connected_lines]
        voltage = "conflict" if "conflict" in tags else "missing" if "missing" in tags else "consistent"
        warnings = []
        if not polygon:
            warnings.append("substation_polygon_missing")
        elif inside != [True, True]:
            warnings.append("bus_outside_substation_polygon")
        if voltage != "consistent":
            warnings.append(f"voltage_tags_{voltage}")
        if any(bus.get("shared_support_branching_requires_review") for bus in (a, b)):
            warnings.append("shared_support_branching")
        nearby = tuple(sorted((a["bus_id"], b["bus_id"]))) in overlaps
        score, band, parts = priority(distance, inside, voltage, [degree[a["bus_id"]], degree[b["bus_id"]]],
                                      len(station_components[station_id]), nearby, warnings)
        identity = dict(substation_osm_id=station_id, component_fingerprints=sorted(fingerprints[c] for c in pair),
                        bus_ids=sorted([a["bus_id"], b["bus_id"]]), voltage_kv=kv)
        identity_key = digest(identity)
        rows.append(dict(review_key=review_key(input_hash, identity_key), identity_key=identity_key,
                         input_hash=input_hash, identity=identity, candidate_id=raw["candidate_id"],
                         substation_osm_id=station_id, substation_name=station.get("name") or station_id,
                         component_ids=pair, bus_ids=[a["bus_id"], b["bus_id"]], voltage_kv=kv,
                         distance_m=distance, distance_target="bus_to_bus", a_inside_polygon=inside[0],
                         b_inside_polygon=inside[1], endpoint_degrees=[degree[a["bus_id"]], degree[b["bus_id"]]],
                         voltage_tags=voltage, incident_line_ids=[l["line_id"] for l in connected_lines],
                         station_component_count=len(station_components[station_id]), nearby_bus_overlap=nearby,
                         priority_score=score, priority_band=band, score_parts=parts, warnings=warnings,
                         evidence=raw["evidence"], required_information=raw["required_information"],
                         status="unreviewed", re_review_required=False))
    pairs = defaultdict(list)
    for row in rows:
        pairs[tuple(row["component_ids"])].append(row)
    ordered_pairs = sorted(pairs, key=lambda p: (-max(r["priority_score"] for r in pairs[p]),
                                               min(r["distance_m"] for r in pairs[p]), p))
    for rank, pair in enumerate(ordered_pairs, 1):
        for row in pairs[pair]:
            row.update(pair_rank=rank, pair_evidence_count=len(pairs[pair]),
                       pair_substations=sorted({r["substation_osm_id"] for r in pairs[pair]}))
    rows.sort(key=lambda r: (r["pair_rank"], -r["priority_score"], r["distance_m"], r["review_key"]))
    summary = dict(schema_version=SCHEMA, input_hash=input_hash, candidate_type=KIND,
                   candidates=len(rows), distinct_component_pairs=len(pairs), substations=len(stations),
                   priority_bands=dict(sorted(Counter(r["priority_band"] for r in rows).items())),
                   pair_priority_bands=dict(sorted(Counter(max(pairs[p], key=lambda r:r["priority_score"])["priority_band"]
                                                           for p in pairs).items())),
                   warnings=dict(sorted(Counter(w for r in rows for w in r["warnings"]).items())),
                   duplicate_pair_count=sum(len(rs) > 1 for rs in pairs.values()),
                   extra_station_evidence=len(rows)-len(pairs), score_policy=SCORE_POLICY,
                   ac_pf_readiness=graph.metadata.get("ac_pf_readiness"),
                   license=graph.metadata.get("license"), osm_data_timestamp=graph.metadata.get("osm_data_timestamp"))
    # Pool original geometry once, rather than duplicating national components in every candidate.
    payload = dict(summary=summary, states=STATES, candidates=rows, stations=stations,
                   buses=graph.buses, lines=sorted(graph.lines, key=lambda l:l["line_id"]),
                   bus_components=bc)
    return payload


def decision_identity(row):
    return {k: row[k] for k in ("review_key", "identity_key", "input_hash", "substation_osm_id", "component_ids", "bus_ids")}


def export_decisions(payload, decisions, stale_records=()):
    """Strict JSON interchange. Timestamps belong only to human-exported decisions."""
    document = dict(schema_version=SCHEMA, input_hash=payload["summary"]["input_hash"],
                    decisions=list(decisions), stale_records=list(stale_records))
    import_decisions(payload, document)
    return document


def import_decisions(payload, document):
    """Validate atomically; foreign input is quarantined, never applied. No model writes."""
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA:
        raise ValueError("지원하지 않는 검토 기록 스키마")
    valid_hash = lambda v: isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) is not None
    version = document.get("input_hash")
    if not valid_hash(version) or not isinstance(document.get("decisions"), list) or not isinstance(document.get("stale_records"), list):
        raise ValueError("입력 해시/검토 목록 오류")
    known = {r["review_key"]: r for r in payload["candidates"]}
    active, stale, seen = [], [], set()
    for quarantine, records in ((False, document["decisions"]), (True, document["stale_records"])):
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("검토 기록은 객체여야 합니다.")
            rh, identity = record.get("input_hash"), record.get("identity_key")
            if not valid_hash(rh) or not valid_hash(identity) or record.get("review_key") != review_key(rh, identity):
                raise ValueError("검토 키/입력 해시 오류")
            key = record["review_key"]
            if key in seen:
                raise ValueError("중복 검토 키")
            seen.add(key)
            if not quarantine and rh != version:
                raise ValueError("문서와 기록의 입력 버전 불일치")
            if (not isinstance(record.get("status"), str) or record["status"] not in STATES
                or type(record.get("re_review_required")) is not bool
                or record["re_review_required"] != quarantine
                or not isinstance(record.get("substation_osm_id"), str) or not record["substation_osm_id"]
                or not isinstance(record.get("component_ids"), list) or len(record["component_ids"]) != 2
                or any(type(v) is not int or v < 0 for v in record["component_ids"])
                or not isinstance(record.get("bus_ids"), list) or len(record["bus_ids"]) != 2
                or any(not isinstance(v, str) or not v for v in record["bus_ids"])):
                raise ValueError("검토 상태/식별자 오류")
            for field in ("notes", "evidence_sources", "reviewed_at"):
                if not isinstance(record.get(field), str) or len(record[field]) > 20000:
                    raise ValueError("검토 메모/출처/시각 오류")
            try:
                dt = datetime.fromisoformat(record["reviewed_at"].replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    raise ValueError()
            except ValueError as exc:
                raise ValueError("시간대가 있는 검토 시각이 필요합니다.") from exc
            if key in known and decision_identity(record) != decision_identity(known[key]):
                raise ValueError("검토 키의 후보 식별자가 다릅니다.")
            if not quarantine and rh == payload["summary"]["input_hash"]:
                if key not in known:
                    raise ValueError("이 입력에 존재하지 않는 검토 키")
                active.append(dict(record))
            else:
                stale.append(dict(record, re_review_required=True))
    return dict(decisions=active, stale_records=stale)


def render_html(payload):
    # Escape script termination and HTML-sensitive characters even inside application/json.
    data = canonical(payload).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    data = data.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    assets = Path(__file__).with_name("review_assets")
    html = (assets / "index.html").read_text(encoding="utf-8")
    return html.replace("__REVIEW_CSS__", (assets / "review.css").read_text(encoding="utf-8")).replace(
        "__REVIEW_JS__", (assets / "review.js").read_text(encoding="utf-8")).replace("__REVIEW_DATA__", data)


def run(args):
    summary = load_json(args.analysis_dir / "summary.json")
    source_map = describe(args.map, "source_map")
    expected = {i["role"]: i for i in summary["inputs"]}
    if expected.get("source_map", {}).get("sha256") != source_map["sha256"]:
        raise ValueError("원본 지도 버전이 분석 결과와 다릅니다. analyze_topology를 다시 실행하세요.")
    inputs = [source_map, describe(args.analysis_dir / "connection_candidates.csv", "analysis_candidates"),
              describe(args.analysis_dir / "summary.json", "analysis_summary")]
    objects = []
    if args.osm_objects:
        desc = describe(args.osm_objects, "osm_objects")
        if "osm_objects" in expected and expected["osm_objects"]["sha256"] != desc["sha256"]:
            raise ValueError("OSM 객체 버전이 분석 결과와 다릅니다.")
        inputs.append(desc)
        objects = load_json(args.osm_objects)
    with (args.analysis_dir / "connection_candidates.csv").open(encoding="utf-8", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    if (len(source_rows) != summary["candidates"]["total"]
        or dict(Counter(r["candidate_type"] for r in source_rows)) != summary["candidates"]["by_type"]):
        raise ValueError("후보 CSV 건수가 분석 요약과 다릅니다. 분석 결과 파일들을 함께 다시 생성하세요.")
    # Candidate display IDs and row order are not permanent identity; changes to evidence are versioned.
    normalized = sorted((dict((k, v) for k, v in r.items() if k != "candidate_id") for r in source_rows), key=canonical)
    input_hash = digest(dict(map=source_map["sha256"], objects=sha256(args.osm_objects) if args.osm_objects else None,
                             candidates=normalized))
    payload = build_review(load_map_graph(args.map), source_rows, input_hash, objects)
    if payload["summary"]["distinct_component_pairs"] != summary["candidates"]["component_pairs_by_type"].get(KIND, 0):
        raise ValueError("후보 CSV 성분 쌍 수가 분석 요약과 다릅니다.")
    payload["summary"]["inputs"] = inputs
    payload["summary"]["objects_supplied"] = bool(args.osm_objects)
    protected = {Path(i["path"]).resolve() for i in [*inputs, *summary["inputs"]]}
    targets = [args.output_dir / n for n in ("index.html", "review_candidates.csv", "review_summary.json")]
    for target in targets:
        if target.resolve() in protected:
            raise ValueError(f"입력 파일 덮어쓰기 거부: {target}")
        if target.is_symlink():
            raise ValueError(f"출력 심볼릭 링크 거부: {target}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # No decisions file is created or touched by generation.
    (args.output_dir / "index.html").write_text(render_html(payload), encoding="utf-8")
    (args.output_dir / "review_summary.json").write_text(json.dumps(payload["summary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = payload["candidates"]
    columns = list(rows[0]) if rows else ["review_key", "input_hash", "status"]
    with (args.output_dir / "review_candidates.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            values = {k: canonical(v) if isinstance(v, (list, dict)) else v for k, v in row.items()}
            # CSV spreadsheet formula injection protection; machine identifiers remain unmodified.
            values = {k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v
                      for k, v in values.items()}
            writer.writerow(values)
    return payload["summary"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--korea-project", type=Path)
    parser.add_argument("--map", type=Path)
    parser.add_argument("--osm-objects", type=Path)
    parser.add_argument("--analysis-dir", type=Path, default=ROOT / "outputs/kr_topology")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/kr_topology/review")
    args = parser.parse_args(argv)
    if args.korea_project:
        args.map = args.map or args.korea_project / "outputs/map/kr_grid_preview.json"
        objects = args.korea_project / "data/processed/osm_power_objects.json"
        if args.osm_objects is None and objects.is_file():
            args.osm_objects = objects
    if args.map is None:
        parser.error("--map 또는 --korea-project가 필요합니다.")
    try:
        summary = run(args)
        print(f"후보 {summary['candidates']}건 / 고유 성분 쌍 {summary['distinct_component_pairs']}개 / "
              f"변전소 {summary['substations']}곳 → {args.output_dir / 'index.html'}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"검토 데이터 생성 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
