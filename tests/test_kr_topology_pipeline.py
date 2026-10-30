"""Korea topology pipeline: components, review candidates, adapters, determinism and real data."""
import copy
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.kr import candidates as cand  # noqa: E402
from pipelines.kr.analyze_topology import main as analyze_main  # noqa: E402
from pipelines.kr.sources import load_map_graph, load_netcdf_graph, validate_graph  # noqa: E402
from pipelines.kr.topology import analyze, compare_partitions  # noqa: E402

KOREA_PROJECT = ROOT.parent / "korea-grid-poc"
EAST_50M = 50 / (math.radians(1) * cand.EARTH_RADIUS_M * math.cos(math.radians(36)))
NORTH_20M = 20 / (math.radians(1) * cand.EARTH_RADIUS_M)
STATION = {"type": "Polygon", "coordinates": [[[127.009, 35.999], [127.0115, 35.999], [127.0115, 36.001],
                                                [127.009, 36.001], [127.009, 35.999]]]}


def bus(name, node, lon, lat, kv, station=False):
    return dict(bus_id=f"osm_node_{node}_{kv * 1000}V", latitude=lat, longitude=lon, voltage_kv=kv,
                osm_id=f"node/{node}", source="OSM way node", inferred=True,
                substation_candidates=["way/900"] if station else [], osm_node_tags={"power": "portal"}, label=name)


def line(way, a, b, kv):
    return dict(line_id=f"way_{way}_{kv * 1000}V_0", bus1_id=a["bus_id"], bus2_id=b["bus_id"], voltage_kv=kv,
                osm_id=f"way/{way}", source="OSM", inferred=False,
                coordinates=[[a["longitude"], a["latitude"]], [b["longitude"], b["latitude"]]])


def fixture_map():
    """A–B: same substation, 50 m apart (154 kV). C: 345 kV on A's node. D, E: T-junction gap. F: isolated."""
    a1, a2 = bus("a1", 1, 127.0, 36.0, 154), bus("a2", 2, 127.01, 36.0, 154, station=True)
    b1, b2 = bus("b1", 3, 127.01 + EAST_50M, 36.0, 154, station=True), bus("b2", 4, 127.02, 36.0, 154)
    c1, c2 = bus("c1", 2, 127.01, 36.0, 345, station=True), bus("c2", 5, 127.01, 36.01, 345)
    d1, d2 = bus("d1", 6, 128.0, 37.0, 154), bus("d2", 7, 128.01, 37.0, 154)
    e1, e2 = bus("e1", 8, 128.005, 37.0 + NORTH_20M, 154), bus("e2", 9, 128.005, 37.01, 154)
    f1 = bus("f1", 10, 129.0, 35.5, 765)
    buses = [a1, a2, b1, b2, c1, c2, d1, d2, e1, e2, f1]
    lines = [line(101, a1, a2, 154), line(102, b1, b2, 154), line(103, c1, c2, 345), line(104, d1, d2, 154),
             line(105, e1, e2, 154)]
    return dict(schema_version="kr-grid-preview-1.0", grid_id="kr-osm-transmission", country="KR",
                metadata=dict(model_kind="geographic_topology_only", ac_pf_readiness="NOT_READY", operational=False,
                              osm_data_timestamp="2026-10-20T20:23:47Z", license="ODbL-1.0"),
                buses=buses, lines=lines, transformers=[], generators=[], powerplants=[],
                substations=[dict(osm_id="way/900", name="Test SS", tags={"power": "substation"}, geometry=STATION)],
                osm_transformers=[dict(osm_id="node/950", tags={"power": "transformer"},
                                       geometry={"type": "Point", "coordinates": [127.0105, 36.0002]})],
                hvdc=[], unclassified_lines=[])


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def osm_objects(tmp_path, three_component_way=False):
    rows = [(101, "154000", [1, 2]), (102, "154000", [3, 4]), (103, "345000", [2, 5]),
            (104, "154000", [6, 7]), (105, "154000", [8, 9]), (200, "", [4, 11, 6])]
    excluded = [dict(osm_id="way/200", reason="missing_voltage", classification="AC")]
    if three_component_way:   # like way/143990774: one out-of-scope way touching three components
        rows.append((201, "380000", [1, 12, 6, 13, 9]))
        excluded.append(dict(osm_id="way/201", reason="outside_target_154_345_765kV", classification="AC"))
    ways = [dict(osm_id=f"way/{w}", tags={"power": "line", "voltage": v}, node_refs=refs) for w, v, refs in rows]
    return write(tmp_path / "objects.json", ways), write(tmp_path / "excluded.json", excluded)


def run_cli(tmp_path, out, *extra, three_component_way=False):
    source = write(tmp_path / "map.json", fixture_map())
    objects, excluded = osm_objects(tmp_path, three_component_way)
    argv = ["--map", str(source), "--osm-objects", str(objects), "--excluded", str(excluded),
            "--output-dir", str(out), *extra]
    # Explicit inputs only: the real deployed map/land layer in data/grids must not be picked up.
    argv += ["--deployed-map", str(source), "--land-boundary", str(write(tmp_path / "land.json", dict(polygons=[])))]
    assert analyze_main(argv) == 0
    return json.loads((out / "summary.json").read_text(encoding="utf-8"))


def candidates_of(out):
    import csv
    return list(csv.DictReader((out / "connection_candidates.csv").open(encoding="utf-8")))


def test_components_isolated_bus_and_voltage_breakdown(tmp_path):
    graph = load_map_graph(write(tmp_path / "map.json", fixture_map()))
    stats = analyze(graph)["stats"]
    assert (stats["buses"], stats["lines"], stats["connected_components"]) == (11, 5, 6)
    assert stats["isolated_single_bus_components"] == 1 and stats["zero_degree_buses"] == 1
    assert stats["one_line_components"] == 5 and stats["self_loops"] == 0 and stats["mixed_voltage_components"] == 0
    assert stats["size_distribution"]["1"] == 1 and stats["size_distribution"]["2"] == 5
    assert {k: v["components"] for k, v in stats["by_voltage_kv"].items()} == {"154": 4, "345": 1, "765": 1}


def test_union_of_lines_and_parallel_groups():
    buses = [dict(bus_id=i, latitude=36, longitude=127 + n / 100, voltage_kv=154) for n, i in enumerate("pqrs")]
    lines = [dict(line_id=i, bus1_id=a, bus2_id=b, voltage_kv=154) for i, a, b in
             [("pq", "p", "q"), ("qr", "q", "r"), ("pq2", "q", "p")]]
    stats = analyze(validate_graph("t", buses, lines))["stats"]
    assert stats["connected_components"] == 2 and stats["parallel_line_groups"] == 1


def test_distance_calculations():
    one_degree = math.radians(1) * 6371008.8
    assert cand.haversine_m(36, 127, 37, 127) == pytest.approx(one_degree, abs=0.01)
    assert cand.haversine_m(0, 0, 0, 1) == pytest.approx(one_degree, abs=0.01)
    assert cand.haversine_m(36, 127, 37, 127) == pytest.approx(110_950, rel=0.005)   # WGS84 meridian arc 36-37 N
    assert cand.haversine_m(36, 127.01, 36, 127.01 + EAST_50M) == pytest.approx(50.0, abs=0.01)
    assert float(cand.point_segment_m(37.0 + NORTH_20M, 128.005, 37.0, 128.0, 37.0, 128.01)) == pytest.approx(20.0, abs=0.01)
    assert float(cand.point_segment_m(37.0, 128.02, 37.0, 128.0, 37.0, 128.01)) == pytest.approx(
        cand.haversine_m(37, 128.01, 37, 128.02), rel=1e-3)   # beyond the end: distance to the end point


def test_candidate_types_are_separated_and_unverified(tmp_path):
    out = tmp_path / "out"
    summary = run_cli(tmp_path, out)
    rows = candidates_of(out)
    labels = {b["bus_id"]: b["label"] for b in fixture_map()["buses"]}
    named = {(r["candidate_type"], frozenset({labels[r["a_bus_id"]], labels[r["b_bus_id"]]})) for r in rows}
    assert named == {("same_substation_same_voltage", frozenset({"a2", "b1"})),
                     ("same_substation_cross_voltage", frozenset({"a2", "c1"})),
                     ("same_substation_cross_voltage", frozenset({"b1", "c1"})),
                     ("shared_osm_node_cross_voltage", frozenset({"a2", "c1"})),
                     ("nearby_bus_same_voltage", frozenset({"a2", "b1"})),
                     ("nearby_bus_cross_voltage", frozenset({"b1", "c1"})),
                     ("endpoint_near_line_same_voltage", frozenset({"d1", "e1"})),
                     ("excluded_way_bridge", frozenset({"b2", "d1"}))}
    assert all(r["status"] == "unverified" for r in rows)
    near = next(r for r in rows if r["candidate_type"] == "nearby_bus_same_voltage")
    assert float(near["distance_m"]) == pytest.approx(50.0, abs=0.05)
    cross = next(r for r in rows if r["candidate_type"] == "same_substation_cross_voltage")
    assert "osm_transformers_inside=1" in cross["evidence"]
    edge = next(r for r in rows if r["candidate_type"] == "endpoint_near_line_same_voltage")
    assert float(edge["distance_m"]) == pytest.approx(20.0, abs=0.05) and edge["line_id"] == "way_104_154000V_0"
    # Candidates are review items only: the model and its components are unchanged.
    assert summary["statistics"]["connected_components"] == 6
    assert summary["hypothetical_component_counts"]["current"] == 6
    assert summary["hypothetical_component_counts"]["all_same_voltage_candidates"] == 4
    unknown = next(c for c in summary["causes"] if c["cause"] == "unknown")
    assert unknown["evidence"]["components_without_any_candidate"] == 1   # the isolated 765 kV bus


def test_endpoint_candidate_measures_to_the_route_midpoint_not_the_end_bus():
    """A dangling bus 30 m beside the middle of an ~18 km line: the distance is to the route."""
    far = [dict(bus_id=i, latitude=36.0, longitude=lon, voltage_kv=154, osm_id=f"node/{n}")
           for n, (i, lon) in enumerate([("g1", 128.5), ("g2", 128.7)], 1)]
    near = [dict(bus_id="h1", latitude=36.0 + 30 / (math.radians(1) * cand.EARTH_RADIUS_M), longitude=128.6,
                 voltage_kv=154, osm_id="node/3"),
            dict(bus_id="h2", latitude=36.05, longitude=128.6, voltage_kv=154, osm_id="node/4")]
    lines = [dict(line_id="long", bus1_id="g1", bus2_id="g2", voltage_kv=154, coordinates=[[128.5, 36.0], [128.7, 36.0]]),
             dict(line_id="spur", bus1_id="h1", bus2_id="h2", voltage_kv=154)]
    graph = validate_graph("t", far + near, lines)
    result = analyze(graph)
    rows = cand.endpoint_line_candidates(graph, result["bus_component"], result["degree"], 50)
    assert len(rows) == 1
    row = rows[0]
    assert row["a_bus_id"] == "h1" and row["a_bus_role"] == "measured_bus"
    assert row["distance_target"] == "a_bus_to_line_route" and row["distance_m"] == pytest.approx(30.0, abs=0.05)
    assert row["route_nearest_latitude"] == pytest.approx(36.0, abs=1e-6)
    assert row["route_nearest_longitude"] == pytest.approx(128.6, abs=1e-6)
    assert row["b_bus_role"] == "end_bus_of_approached_line" and row["b_bus_id"] in {"g1", "g2"}
    assert row["a_to_b_bus_distance_m"] == pytest.approx(
        float(cand.haversine_m(row["a_latitude"], row["a_longitude"], row["b_latitude"], row["b_longitude"])), abs=0.01)
    assert row["a_to_b_bus_distance_m"] > 8_000   # the end bus is ~9 km away; it is not the measured point


def test_excluded_way_touching_three_components_yields_every_pair(tmp_path):
    out = tmp_path / "out"
    summary = run_cli(tmp_path, out, three_component_way=True)
    labels = {b["bus_id"]: b["label"] for b in fixture_map()["buses"]}
    rows = [r for r in candidates_of(out) if r["excluded_way_osm_id"] == "way/201"]
    assert {frozenset({labels[r["a_bus_id"]], labels[r["b_bus_id"]]}) for r in rows} == {
        frozenset({"a1", "d1"}), frozenset({"a1", "e2"}), frozenset({"d1", "e2"})}
    assert all(r["status"] == "unverified" and r["excluded_way_voltage"] == "380000"
               and r["distance_target"] == "not_applicable" and len(r["excluded_way_components"].split(";")) == 3
               for r in rows)
    evidence = next(c for c in summary["causes"] if c["cause"] == "7_preprocessing_exclusions")["evidence"]
    assert evidence["excluded_ways_bridging_components"] == 2
    assert evidence["excluded_ways_touching_three_or_more_components"] == 1
    assert evidence["excluded_way_bridge_pair_candidates"] == 4 and summary["candidates"]["by_type"]["excluded_way_bridge"] == 4
    assert summary["candidates"]["excluded_ways_with_bridge_candidates"] == 2
    # Bridges alone: way/201 joins A, D and E, way/200 joins B and D -> 6 components become 3
    # (keeping only the first two components of way/201 would leave 4). The model itself is unchanged.
    bridges = [dict(r, a_component=int(r["a_component"]), b_component=int(r["b_component"]))
               for r in candidates_of(out) if r["candidate_type"] == "excluded_way_bridge"]
    assert cand.hypothetical_merge([[str(i)] for i in range(6)], bridges, {"excluded_way_bridge"}) == 3
    assert summary["hypothetical_component_counts"]["all_same_voltage_plus_excluded_way_bridges"] == 3
    assert summary["statistics"]["connected_components"] == 6


@pytest.mark.parametrize("option", ["--bus-distance-m", "--line-distance-m"])
@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "Infinity", "-1", "0"])
def test_distance_arguments_must_be_finite_and_positive(tmp_path, option, value):
    with pytest.raises(SystemExit) as raised:
        analyze_main(["--map", str(write(tmp_path / "map.json", fixture_map())), f"{option}={value}",
                      "--output-dir", str(tmp_path / "out")])
    assert raised.value.code == 2 and not (tmp_path / "out").exists()


def test_distance_thresholds_are_explicit(tmp_path):
    out = tmp_path / "out"
    summary = run_cli(tmp_path, out, "--bus-distance-m", "40", "--line-distance-m", "10")
    types = {r["candidate_type"] for r in candidates_of(out)}
    assert "nearby_bus_same_voltage" not in types and "endpoint_near_line_same_voltage" not in types
    assert summary["parameters"]["bus_distance_m"] == 40
    with pytest.raises(SystemExit):
        analyze_main(["--map", str(tmp_path / "map.json"), "--bus-distance-m", "0"])


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d["buses"].append(copy.deepcopy(d["buses"][0])), "모선"),
    (lambda d: d["lines"].append(copy.deepcopy(d["lines"][0])), "선로"),
    (lambda d: d["lines"][0].update(bus2_id="missing"), "선로"),
])
def test_duplicate_ids_and_bad_references_are_rejected(tmp_path, mutate, message):
    data = fixture_map()
    mutate(data)
    with pytest.raises(ValueError):
        load_map_graph(write(tmp_path / "bad.json", data))
    with pytest.raises(ValueError):
        validate_graph("raw", data["buses"], data["lines"])
    path = write(tmp_path / "bad.json", data)
    assert analyze_main(["--map", str(path), "--output-dir", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out" / "summary.json").exists()


def test_invalid_input_files_fail_cleanly(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert analyze_main(["--map", str(broken), "--output-dir", str(tmp_path / "o")]) == 2
    assert analyze_main(["--map", str(tmp_path / "missing.json"), "--output-dir", str(tmp_path / "o")]) == 2
    nc = tmp_path / "bad.nc"
    nc.write_bytes(b"not hdf5")
    with pytest.raises(OSError):
        load_netcdf_graph(nc)


def test_deterministic_outputs_and_inputs_untouched(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    run_cli(tmp_path, first)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("*.json")}
    run_cli(tmp_path, second)
    for name in ("summary.json", "components.csv", "connection_candidates.csv", "report.md"):
        if name == "summary.json":  # output paths differ only in the output directory, not in inputs
            assert json.loads((first / name).read_text()) == json.loads((second / name).read_text())
        else:
            assert (first / name).read_bytes() == (second / name).read_bytes(), name
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.glob("*.json")} == before


@pytest.mark.parametrize("short", ["buses_x", "buses_v_nom", "buses_osm_id", "lines_bus1", "lines_v_nom"])
def test_netcdf_array_length_mismatch_is_rejected(tmp_path, short):
    import h5py
    string = h5py.string_dtype()
    arrays = dict(buses_i=["a", "b", "c"], buses_x=[127.0, 127.1, 127.2], buses_y=[36.0] * 3,
                  buses_v_nom=[154.0] * 3, buses_osm_id=["node/1", "node/2", "node/3"],
                  lines_i=["l1", "l2"], lines_bus0=["a", "b"], lines_bus1=["b", "c"], lines_v_nom=[154.0] * 2)
    arrays[short] = arrays[short][:-1]
    path = tmp_path / "mismatch.nc"
    with h5py.File(path, "w") as nc:
        for name, values in arrays.items():
            nc.create_dataset(name, data=values, dtype=string if isinstance(values[0], str) else None)
    with pytest.raises(ValueError, match="배열 길이"):
        load_netcdf_graph(path)


def test_netcdf_adapter_matches_map(tmp_path):
    import h5py
    data = fixture_map()
    path = tmp_path / "base.nc"
    string = h5py.string_dtype()
    with h5py.File(path, "w") as nc:
        nc["buses_i"] = [b["bus_id"] for b in data["buses"]]
        nc["buses_x"] = [b["longitude"] for b in data["buses"]]
        nc["buses_y"] = [b["latitude"] for b in data["buses"]]
        nc["buses_v_nom"] = [float(b["voltage_kv"]) for b in data["buses"]]
        nc.create_dataset("buses_osm_id", data=[b["osm_id"] for b in data["buses"]], dtype=string)
        nc["lines_i"] = [l["line_id"] for l in data["lines"]]
        nc.create_dataset("lines_bus0", data=[l["bus1_id"] for l in data["lines"]], dtype=string)
        nc.create_dataset("lines_bus1", data=[l["bus2_id"] for l in data["lines"]], dtype=string)
        nc["lines_v_nom"] = [float(l["voltage_kv"]) for l in data["lines"]]
        nc.attrs["meta"] = json.dumps({"ac_pf_readiness": "NOT_READY"})
    graph, network = load_map_graph(write(tmp_path / "map.json", data)), load_netcdf_graph(path)
    comparison = compare_partitions(graph, analyze(graph)["components"], network, analyze(network)["components"])
    assert all(comparison[k] for k in ("same_bus_ids", "same_line_ids", "same_line_endpoints", "same_component_membership"))
    assert network.metadata["ac_pf_readiness"] == "NOT_READY"


def test_imported_map_keeps_the_same_topology(tmp_path):
    source = write(tmp_path / "source.json", fixture_map())
    target = tmp_path / "imported.json"
    done = subprocess.run([sys.executable, str(ROOT / "scripts/import_kr_osm_map.py"), "--source", str(source),
                           "--output", str(target)], capture_output=True)
    assert done.returncode == 0, done.stderr
    a, b = load_map_graph(source), load_map_graph(target)
    comparison = compare_partitions(a, analyze(a)["components"], b, analyze(b)["components"])
    assert comparison["same_component_membership"] and comparison["same_line_endpoints"]
    assert b.layers["substations"] == []   # the map API file drops OSM layers; analysis then skips them


@pytest.mark.skipif(not (KOREA_PROJECT / "outputs/map/kr_grid_preview.json").is_file(),
                    reason="korea-grid-poc data not available")
def test_actual_korea_topology_reproduces_reported_components(tmp_path):
    inputs = [KOREA_PROJECT / p for p in ("outputs/map/kr_grid_preview.json", "networks/KR/base.nc")]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs]
    assert analyze_main(["--korea-project", str(KOREA_PROJECT), "--output-dir", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    stats = summary["statistics"]
    assert (stats["buses"], stats["lines"]) == (4197, 3984)
    assert stats["connected_components"] == summary["reported_baseline"]["connected_components"]
    assert summary["difference_from_reported"] == 0 and summary["stored_component_ids_reproduced"] is True
    assert stats["mixed_voltage_components"] == 0 and stats["self_loops"] == 0
    assert all(c["same_component_membership"] and c["same_line_endpoints"] for c in summary["input_comparisons"])
    assert summary["ac_pf_readiness"] == "NOT_READY"
    assert summary["candidates"]["total"] > 0
    rows = candidates_of(tmp_path)
    assert all(r["status"] == "unverified" for r in rows)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs] == before
