"""Korea OSM transmission topology analysis: components, cause evidence and review candidates.

Reads the existing Korea data (read-only) and writes a deterministic report. It does not connect
buses, infer transformers or change the AC PF readiness (``NOT_READY``).

    .venv/bin/python -m pipelines.kr.analyze_topology --korea-project ../korea-grid-poc \\
        --output-dir outputs/kr_topology

``--korea-project`` fills the default input paths of that directory layout; any input can be
given explicitly instead (``--map`` is the only required one).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import candidates as cand
from .sources import ROOT, Graph, describe, load_json, load_map_graph, load_netcdf_graph, load_osm_ways
from .topology import analyze, compare_partitions, stored_component_ids_match

KOREA_LAYOUT = {
    "map": "outputs/map/kr_grid_preview.json",
    "network_nc": "networks/KR/base.nc",
    "osm_objects": "data/processed/osm_power_objects.json",
    "excluded": "reports/excluded_objects.json",
    "topology_suspects": "reports/topology_suspects.json",
    "baseline_report": "reports/kr_network_validation.json",
}
COMPONENT_COLUMNS = ["component_id", "voltage_kv", "buses", "lines", "length_km", "size_bin", "dangling_buses",
                     "dangling_buses_in_substation", "substations_touched", "landmass", "min_latitude", "min_longitude",
                     "max_latitude", "max_longitude", "candidate_types", "review_flags"]
CANDIDATE_COLUMNS = ["candidate_id", "candidate_type", "status", "distance_target", "distance_m",
                     "a_bus_id", "a_bus_role", "a_osm_id", "a_component", "a_voltage_kv", "a_latitude", "a_longitude",
                     "a_osm_tags", "b_bus_id", "b_bus_role", "b_osm_id", "b_component", "b_voltage_kv", "b_latitude",
                     "b_longitude", "b_osm_tags", "line_id", "route_nearest_latitude", "route_nearest_longitude",
                     "a_to_b_bus_distance_m", "substation_osm_id", "excluded_way_osm_id", "excluded_reason",
                     "excluded_way_voltage", "excluded_way_components", "also_types",
                     "evidence", "suspected_reason", "required_information"]
SAME_VOLTAGE_KINDS = {"same_substation_same_voltage", "nearby_bus_same_voltage", "endpoint_near_line_same_voltage"}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--korea-project", type=Path, help="korea-grid-poc directory (default input layout)")
    parser.add_argument("--map", type=Path, help="source map JSON (kr_grid_preview.json)")
    parser.add_argument("--network-nc", type=Path, help="PyPSA base.nc for comparison")
    parser.add_argument("--deployed-map", type=Path, help="imported AI-EMS map for comparison")
    parser.add_argument("--osm-objects", type=Path, help="processed osm_power_objects.json")
    parser.add_argument("--excluded", type=Path, help="excluded_objects.json from the build")
    parser.add_argument("--topology-suspects", type=Path, help="topology_suspects.json (crossings) from the build")
    parser.add_argument("--baseline-report", type=Path, help="kr_network_validation.json with the reported count")
    parser.add_argument("--land-boundary", type=Path, help="kr-land-boundary.json for landmass labels")
    parser.add_argument("--bus-distance-m", type=float, default=200.0, help="review radius between buses (m)")
    parser.add_argument("--line-distance-m", type=float, default=50.0, help="review radius dangling bus to route (m)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/kr_topology")
    args = parser.parse_args(argv)
    for name in ("bus_distance_m", "line_distance_m"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')}는 0보다 큰 유한한 숫자여야 합니다: {value}")
    project = args.korea_project.expanduser().resolve(strict=True) if args.korea_project else None
    for name, relative in KOREA_LAYOUT.items():
        if getattr(args, name) is None and project and (project / relative).is_file():
            setattr(args, name, project / relative)
    for name, default in (("deployed_map", ROOT / "data/grids/kr-osm-transmission-map.json"),
                          ("land_boundary", ROOT / "data/grids/kr-land-boundary.json")):
        if getattr(args, name) is None and default.is_file():
            setattr(args, name, default)
    if args.map is None:
        parser.error("--map 또는 원본 지도가 있는 --korea-project가 필요합니다.")
    return args


def run(args) -> dict[str, Any]:
    inputs = [describe(args.map, "source_map")]
    graph = load_map_graph(args.map)
    result = analyze(graph)
    comps, bus_component, degree = result["components"], result["bus_component"], result["degree"]

    comparisons = []
    if args.network_nc:
        inputs.append(describe(args.network_nc, "pypsa_network"))
        nc = load_netcdf_graph(args.network_nc)
        comparisons.append(compare_partitions(graph, comps, nc, analyze(nc)["components"]))
    if args.deployed_map:
        inputs.append(describe(args.deployed_map, "deployed_ai_ems_map"))
        deployed = load_map_graph(args.deployed_map)
        comparisons.append(compare_partitions(graph, comps, deployed, analyze(deployed)["components"]))

    rows = cand.substation_candidates(graph, bus_component)
    rows += cand.shared_node_candidates(graph, bus_component)
    nearby, histogram = cand.nearby_bus_candidates(graph, bus_component, args.bus_distance_m)
    rows += nearby
    rows += cand.endpoint_line_candidates(graph, bus_component, degree, args.line_distance_m)
    excluded_summary = None
    if args.osm_objects and args.excluded:
        inputs += [describe(args.osm_objects, "osm_objects"), describe(args.excluded, "excluded_objects")]
        bridges, excluded_summary = cand.excluded_way_candidates(graph, bus_component, load_osm_ways(args.osm_objects),
                                                                 load_json(args.excluded))
        excluded_summary["exclusion_reasons"] = dict(sorted(Counter(e.get("reason") for e in load_json(args.excluded)).items()))
        rows += bridges
    rows.sort(key=lambda r: (r["candidate_type"], r["a_component"], r["b_component"], r["a_bus_id"], r["b_bus_id"],
                             r.get("line_id") or "", r.get("excluded_way_osm_id") or ""))
    pair_types = defaultdict(set)
    for row in rows:
        pair_types[component_pair(row)].add(row["candidate_type"])
    for index, row in enumerate(rows, 1):
        row["candidate_id"] = f"KR-TOPO-{index:05d}"
        row["also_types"] = ";".join(sorted(pair_types[component_pair(row)] - {row["candidate_type"]}))

    land = None
    if args.land_boundary:
        inputs.append(describe(args.land_boundary, "land_boundary"))
        land = cand.landmass_labels(graph, load_json(args.land_boundary))
    crossings = None
    if args.topology_suspects:
        inputs.append(describe(args.topology_suspects, "topology_suspects"))
        suspects = load_json(args.topology_suspects)
        line_component = {l["line_id"]: bus_component[l["bus1_id"]] for l in graph.lines}
        known = [c for c in suspects.get("crossings", []) if all(i in line_component for i in c["line_ids"])]
        split = [c for c in known if line_component[c["line_ids"][0]] != line_component[c["line_ids"][1]]]
        crossings = dict(crossing_pairs=len(suspects.get("crossings", [])), crossing_pairs_matched=len(known),
                         between_different_components=len(split),
                         between_different_components_same_voltage=sum(c.get("different_voltage") is False for c in split))
    baseline = None
    if args.baseline_report:
        inputs.append(describe(args.baseline_report, "baseline_report"))
        report = load_json(args.baseline_report)
        baseline = {k: report.get(k) for k in ("connected_components", "largest_component_buses", "singleton_buses",
                                                "one_edge_components", "shared_support_branching_buses_for_review")}

    component_rows = component_table(graph, result, rows, land)
    summary = build_summary(args, graph, result, rows, histogram, comparisons, excluded_summary, land, crossings,
                            baseline, inputs, component_rows)
    write_outputs(args.output_dir, summary, component_rows, rows)
    return summary


def component_pair(row: dict) -> tuple[int, int]:
    """Unordered component pair (endpoint candidates keep the measured bus on side a)."""
    return tuple(sorted((row["a_component"], row["b_component"])))


def component_table(graph: Graph, result: dict, rows: list[dict], land: dict | None) -> list[dict]:
    kinds = defaultdict(set)
    for r in rows:
        kinds[r["a_component"]].add(r["candidate_type"])
        kinds[r["b_component"]].add(r["candidate_type"])
    out = []
    for row, members in zip(result["rows"], result["components"]):
        buses = [graph.buses[b] for b in members]
        dangling = [b for b in members if result["degree"][b] == 1]
        stations = sorted({s for b in buses for s in (b.get("substation_candidates") or [])})
        landmasses = Counter(land[b] for b in members) if land else None
        flags = []
        if row["lines"] == 1:
            flags.append("single_line_component")
        if dangling and all(graph.buses[b].get("substation_candidates") for b in dangling):
            flags.append("all_dangling_ends_in_osm_substations")
        if any(graph.buses[b].get("shared_support_branching_requires_review") for b in members):
            flags.append("contains_shared_support_branching")
        if landmasses and "mainland" not in landmasses:
            flags.append("entirely_off_mainland")
        elif landmasses and set(landmasses) - {"mainland"}:
            flags.append("spans_landmasses")
        if not kinds[row["component_id"]]:
            flags.append("no_review_candidate")
        out.append(dict(row, voltage_kv=";".join(f"{v:g}" for v in row["voltage_kv"]),
                        dangling_buses_in_substation=sum(bool(graph.buses[b].get("substation_candidates")) for b in dangling),
                        substations_touched=len(stations),
                        landmass=";".join(f"{k}:{v}" for k, v in sorted(landmasses.items())) if landmasses else "",
                        min_latitude=min(b["latitude"] for b in buses), min_longitude=min(b["longitude"] for b in buses),
                        max_latitude=max(b["latitude"] for b in buses), max_longitude=max(b["longitude"] for b in buses),
                        candidate_types=";".join(sorted(kinds[row["component_id"]])), review_flags=";".join(flags)))
    return out


def build_summary(args, graph, result, rows, histogram, comparisons, excluded_summary, land, crossings, baseline,
                  inputs, component_rows) -> dict[str, Any]:
    stats = result["stats"]
    by_type = Counter(r["candidate_type"] for r in rows)
    pairs_by_type = {k: len({component_pair(r) for r in rows if r["candidate_type"] == k}) for k in by_type}
    comps = result["components"]
    dangling = [b for b in graph.buses if result["degree"][b] == 1]
    in_station = sum(bool(graph.buses[b].get("substation_candidates")) for b in dangling)
    support = [b for b in graph.buses if graph.buses[b].get("shared_support_branching_requires_review")]
    off_mainland = [r for r in component_rows if "entirely_off_mainland" in r["review_flags"]]
    spanning = [r for r in component_rows if "spans_landmasses" in r["review_flags"]]
    unknown = [r for r in component_rows if "no_review_candidate" in r["review_flags"]]
    voltages = sorted({b["voltage_kv"] for b in graph.buses.values()})
    sensitivity = {
        "same_substation_same_voltage": cand.hypothetical_merge(comps, rows, {"same_substation_same_voltage"}),
        "all_same_voltage_candidates": cand.hypothetical_merge(comps, rows, SAME_VOLTAGE_KINDS),
        "all_same_voltage_plus_excluded_way_bridges": cand.hypothetical_merge(comps, rows, SAME_VOLTAGE_KINDS | {"excluded_way_bridge"}),
        "all_candidate_types_including_cross_voltage": cand.hypothetical_merge(comps, rows, set(by_type)),
    }
    causes = [
        dict(cause="1_unconnected_line_endpoints", status="data_observed_cause_unverified",
             evidence=dict(dangling_buses=len(dangling), dangling_inside_osm_substation=in_station,
                           dangling_outside_osm_substation=len(dangling) - in_station),
             note="차수 1 모선은 선로 끝점이다. 변전소 영역 안 끝점은 정상 인출일 수 있고, 밖의 끝점은 OSM 미완성 또는 실제 종단일 수 있다."),
        dict(cause="2_missing_substation_internal_connections", status="data_supported_candidate_unverified",
             evidence=dict(candidates=by_type.get("same_substation_same_voltage", 0),
                           component_pairs=pairs_by_type.get("same_substation_same_voltage", 0),
                           components_after_hypothetical_merge=sensitivity["same_substation_same_voltage"]),
             note="같은 OSM 변전소 영역 안의 같은 전압 성분은 모선(busbar)이 OSM에 없어서 분리됐을 가능성이 크지만, 개폐 상태와 모선 구성은 데이터에 없다."),
        dict(cause="3_missing_cross_voltage_transformers", status="confirmed_by_model_definition",
             evidence=dict(transformers_in_model=0, mixed_voltage_components=stats["mixed_voltage_components"],
                           minimum_components_without_transformers=len(voltages),
                           same_substation_cross_voltage_candidates=by_type.get("same_substation_cross_voltage", 0)),
             note="모델은 변압기를 넣지 않으므로 서로 다른 전압은 항상 다른 성분이다. 어떤 변전소에 어떤 변압기가 있는지는 확인되지 않았다."),
        dict(cause="4_geographic_crossing_is_not_connection", status="excluded_by_definition" if crossings else "not_assessed",
             evidence=crossings or {}, note="교차는 연결로 보지 않는다. 교차 쌍은 접속 후보가 아니다."),
        dict(cause="5_shared_support_branching", status="data_observed_cause_unverified",
             evidence=dict(flagged_buses=len(support),
                           components_containing_flagged_buses=len({result["bus_component"][b] for b in support})),
             note="병가 철탑에서 노드를 공유한 선로가 실제로는 별도 회선일 수 있어 과연결(잘못 합쳐짐) 위험이다. 성분 수를 늘리는 원인이 아니라 줄였을 수 있다."),
        dict(cause="6_nearby_endpoints_with_different_osm_nodes", status="data_supported_candidate_unverified",
             evidence=dict(nearby_bus_same_voltage=by_type.get("nearby_bus_same_voltage", 0),
                           endpoint_near_line_same_voltage=by_type.get("endpoint_near_line_same_voltage", 0),
                           bus_distance_m=args.bus_distance_m, line_distance_m=args.line_distance_m,
                           nearest_component_pair_distance_histogram=histogram),
             note="가까운 거리는 접속의 증거가 아니다. 후보로만 기록한다."),
        dict(cause="7_preprocessing_exclusions", status="data_supported_candidate_unverified" if excluded_summary else "not_assessed",
             evidence=excluded_summary or {}, note="전압 누락·대상 외 전압 등으로 제외된 OSM 선로가 성분 사이를 잇는 경우, 닿는 성분의 모든 쌍을 후보로 기록한다(선로 수와 쌍 후보 수를 따로 집계). 제외 선로의 전압·운전 상태가 확인되지 않아 전기적 연결로 확정하지 않는다."),
        dict(cause="8_separate_systems_or_islands", status="location_observed_electrical_status_unverified" if land else "not_assessed",
             evidence=dict(components_entirely_off_mainland=len(off_mainland),
                           buses_entirely_off_mainland=sum(r["buses"] for r in off_mainland),
                           landmasses=dict(sorted(Counter(lm.split(":")[0] for r in off_mainland for lm in r["landmass"].split(";")).items())),
                           components_spanning_mainland_and_islands=len(spanning),
                           hvdc_ways_in_source=len(graph.layers.get("hvdc", [])),
                           hvdc_names=sorted({(h.get("tags") or {}).get("name:en") or h.get("name") or h["osm_id"] for h in graph.layers.get("hvdc", [])})),
             note="섬 위치는 육지 경계로 확인되지만 독립 계통·HVDC 연계 여부는 전기적으로 확인되지 않았다."),
        dict(cause="unknown", status="unknown",
             evidence=dict(components_without_any_candidate=len(unknown),
                           buses_in_those_components=sum(r["buses"] for r in unknown)),
             note="어떤 후보 유형에도 해당하지 않는 성분이다. 원인을 판단할 근거가 없다."),
    ]
    reported = (baseline or {}).get("connected_components")
    return dict(
        analysis="kr_osm_topology", graph_definition=dict(
            nodes="bus_id (OSM node + voltage)", edges="line_id between bus1_id and bus2_id, undirected",
            merges="none: no distance snapping, no crossing connection, no cross-voltage ties, no inferred transformers",
            component_order="size descending, then smallest bus_id (same as the source build)"),
        ac_pf_readiness=graph.metadata.get("ac_pf_readiness"), model_kind=graph.metadata.get("model_kind"),
        osm_data_timestamp=graph.metadata.get("osm_data_timestamp"), license=graph.metadata.get("license"),
        inputs=inputs, parameters=dict(bus_distance_m=args.bus_distance_m, line_distance_m=args.line_distance_m,
                                       distance_method="haversine, mean Earth radius 6371008.8 m; point-to-route in local equirectangular plane"),
        statistics={k: v for k, v in stats.items() if k not in ("self_loop_line_ids", "parallel_line_ids")},
        self_loop_line_ids=stats["self_loop_line_ids"], parallel_line_ids=stats["parallel_line_ids"],
        stored_component_ids_reproduced=stored_component_ids_match(graph, result["components"]),
        reported_baseline=baseline, difference_from_reported=None if reported is None else stats["connected_components"] - reported,
        input_comparisons=comparisons,
        candidates=dict(total=len(rows), by_type=dict(sorted(by_type.items())),
                        component_pairs_by_type=dict(sorted(pairs_by_type.items())),
                        distinct_component_pairs=len({component_pair(r) for r in rows}),
                        distinct_same_voltage_component_pairs=len({component_pair(r) for r in rows
                                                                   if r["candidate_type"] in SAME_VOLTAGE_KINDS}),
                        excluded_ways_with_bridge_candidates=len({r["excluded_way_osm_id"] for r in rows
                                                                  if r["candidate_type"] == "excluded_way_bridge"}),
                        shared_node_cross_voltage_tower_tagged=sum(
                            "power=tower" in (r["a_osm_tags"] + r["b_osm_tags"]) for r in rows
                            if r["candidate_type"] == "shared_osm_node_cross_voltage"),
                        components_with_any_candidate=len({r["a_component"] for r in rows} | {r["b_component"] for r in rows})),
        hypothetical_component_counts=dict(note="민감도 분석일 뿐 모델에 반영하지 않음. 후보가 모두 실제 접속이라고 가정한 상한 효과.",
                                           current=stats["connected_components"], **sensitivity),
        causes=causes)


def _csv(rows: list[dict], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
    return buffer.getvalue()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="") as stream:
        stream.write(text)
    os.replace(stream.name, path)


def write_outputs(output_dir: Path, summary: dict, components: list[dict], rows: list[dict]) -> None:
    output_dir = Path(output_dir).resolve()
    input_paths = {Path(i["path"]).resolve() for i in summary["inputs"]}
    targets = {name: output_dir / name for name in ("summary.json", "components.csv", "connection_candidates.csv", "report.md")}
    if input_paths & set(targets.values()):
        raise ValueError("출력 파일이 입력 파일과 같습니다.")
    _atomic_write(targets["summary.json"], json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    _atomic_write(targets["components.csv"], _csv(components, COMPONENT_COLUMNS))
    _atomic_write(targets["connection_candidates.csv"], _csv(rows, CANDIDATE_COLUMNS))
    from .report import render_report
    _atomic_write(targets["report.md"], render_report(summary, components))


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        summary = run(args)
    except (OSError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    s = summary["statistics"]
    print(json.dumps(dict(output_dir=str(Path(args.output_dir).resolve()), buses=s["buses"], lines=s["lines"],
                          connected_components=s["connected_components"],
                          difference_from_reported=summary["difference_from_reported"],
                          candidates=summary["candidates"]["by_type"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
