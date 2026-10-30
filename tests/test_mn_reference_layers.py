"""Mongolia reference-only layers: selection rules, validated loader, API isolation.

The reference layers are display data in their own file. They must never change the base map,
its PF-preview hash binding, or what Security/Sensitivity accept as line IDs.
"""

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web_app
from ai_ems.grid_catalog import load_mongolia_map
from ai_ems.reference_layers import load_mongolia_reference_layers

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_mn_reference_layers.py"
spec = importlib.util.spec_from_file_location("build_mn_reference_layers", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

MAP_FILE = ROOT / "data" / "grids" / "mn-osm-35k-map.json"
REF_FILE = ROOT / "data" / "grids" / "mn-osm-35k-reference-layers.json"
PF_FILE = ROOT / "data" / "grids" / "mn35k-synthetic-pf-preview.json"
EARTH = (ROOT / builder.DEFAULT_EARTH).resolve()
AUDIT = (ROOT / builder.DEFAULT_AUDIT).resolve()
client = TestClient(web_app.app)
# data/grids/ is generated (git-ignored) like the base map: run scripts/build_mn_reference_layers.py.
needs_data = pytest.mark.skipif(not (REF_FILE.is_file() and MAP_FILE.is_file() and PF_FILE.is_file()),
                                reason="generated Mongolia map/reference files not available")
needs_sources = pytest.mark.skipif(not (EARTH / "resources/MN_35k/osm/raw/all_raw_lines.geojson").is_file()
                                   or not (AUDIT / "collapsed_lines.csv").is_file(),
                                   reason="sibling mongolia-grid-poc inputs not available")


# ------------------------------------------------------------------ generated data
@needs_data
def test_reference_file_is_pinned_to_the_unchanged_base_map():
    payload = json.loads(REF_FILE.read_text(encoding="utf-8"))
    map_sha = hashlib.sha256(MAP_FILE.read_bytes()).hexdigest()
    assert payload["base_map_sha256"] == map_sha
    # The PF preview is still bound to the very same base map (no hash bypass needed).
    pf = json.loads(PF_FILE.read_text(encoding="utf-8"))
    assert pf["metadata"]["map_json_sha256"] == map_sha


@needs_data
def test_loader_returns_only_displayable_reference_equipment():
    data = load_mongolia_reference_layers(REF_FILE, MAP_FILE)
    raw = json.loads(REF_FILE.read_text(encoding="utf-8"))
    assert data["reference_only"] is True
    assert len(data["lines"]) == raw["summary"]["reference_lines"] == 33
    assert len(data["substations"]) == raw["summary"]["reference_substations"] == 17
    hidden = [s for s in raw["substations"] if not s["display"]]
    assert len(hidden) == raw["summary"]["same_location_as_map_bus"]
    assert all(s["duplicate_check"]["status"] == "same_location_as_map_bus" and s["duplicate_check"]["distance_km"] <= 1.0
               for s in hidden)
    assert all(line["voltage_kv"] == 110.0 and line["osm_id"] for line in data["lines"])
    assert all(sub["nearest_map_bus_km"] > builder.SUBSTATION_SAME_AS_BUS_KM for sub in data["substations"])


@needs_data
def test_every_collapsed_110kv_way_is_accounted_for():
    raw = json.loads(REF_FILE.read_text(encoding="utf-8"))
    summary = raw["summary"]
    kept = {line["osm_id"] for line in raw["lines"]}
    excluded = {item["osm_id"] for item in raw["excluded"]["lines"]}
    assert not kept & excluded
    assert len(kept) + len(excluded) == summary["collapsed_110kv_ways"] == 114
    segments = [s for line in raw["lines"] for s in line["collapsed_segment_ids"]]
    segments += [s for item in raw["excluded"]["lines"] for s in item["collapsed_segment_ids"]]
    assert len(segments) == len(set(segments)) == summary["collapsed_110kv_segments"] == 120
    subs = {s["osm_id"] for s in raw["substations"]} | {s["osm_id"] for s in raw["excluded"]["substations"]}
    assert len(subs) == summary["raw_substations"] == 355


@needs_data
def test_reference_ids_never_collide_with_model_ids():
    base = load_mongolia_map(MAP_FILE)
    data = load_mongolia_reference_layers(REF_FILE, MAP_FILE)
    model = {line["line_id"] for line in base["lines"]} | {bus["bus_id"] for bus in base["buses"]}
    model |= {t["transformer_id"] for t in base["transformers"]}
    refs = [item["ref_id"] for item in data["lines"] + data["substations"]]
    assert len(refs) == len(set(refs)) and not set(refs) & model


@needs_data
@needs_sources
def test_builder_output_is_deterministic():
    payload = builder.build(EARTH, AUDIT, MAP_FILE)
    assert payload == json.loads(REF_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ loader rejects
def _copy(tmp_path):
    ref, base = tmp_path / "ref.json", tmp_path / "map.json"
    shutil.copy(REF_FILE, ref)
    shutil.copy(MAP_FILE, base)
    return ref, base


@needs_data
def test_loader_rejects_a_different_base_map(tmp_path):
    ref, base = _copy(tmp_path)
    base.write_bytes(base.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="다른 지도 기준"):
        load_mongolia_reference_layers(ref, base)


@needs_data
@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(reference_only=False), "reference_only"),
    (lambda p: p.update(schema="other"), "형식"),
    (lambda p: p["lines"][0].update(ref_id="222087807-1_0"), "참조 설비 ID"),
    (lambda p: p["lines"][1].update(ref_id=p["lines"][0]["ref_id"]), "참조 설비 ID"),
    (lambda p: p["substations"][0].update(ref_id="REF-L-x"), "참조 설비 ID"),
    (lambda p: p["lines"][0].update({"from": [float("nan"), 47.0]}), "좌표"),
])
def test_loader_rejects_invalid_reference_data(tmp_path, mutate, message):
    ref, base = _copy(tmp_path)
    payload = json.loads(ref.read_text(encoding="utf-8"))
    mutate(payload)
    ref.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_mongolia_reference_layers(ref, base)


def test_missing_reference_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_mongolia_reference_layers(tmp_path / "missing.json", MAP_FILE)


# ------------------------------------------------------------------ API isolation
@needs_data
def test_api_serves_reference_layers_separately_from_the_base_map():
    response = client.get("/api/grids/mn-osm-35k/reference-layers")
    assert response.status_code == 200
    assert len(response.json()["lines"]) == 33
    base = client.get("/api/grids/mn-osm-35k/map").json()
    assert base == load_mongolia_map(MAP_FILE)
    assert len(base["buses"]) == 173 and len(base["lines"]) == 159
    assert not any(str(line["line_id"]).startswith("REF-") for line in base["lines"])
    assert client.get("/api/grids/mn-osm-35k/pf-preview").status_code == 200


@needs_data
def test_api_reference_layers_unavailable_does_not_break_the_map(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "MONGOLIA_REFERENCE_LAYERS_FILE", tmp_path / "missing.json")
    assert client.get("/api/grids/mn-osm-35k/reference-layers").status_code == 404
    assert client.get("/api/grids/mn-osm-35k/map").status_code == 200
    assert client.get("/api/grids/mn-osm-35k/pf-preview").status_code == 200


@needs_data
def test_reference_lines_cannot_be_n1_outages_or_monitored_lines():
    ref_id = load_mongolia_reference_layers(REF_FILE, MAP_FILE)["lines"][0]["ref_id"]
    outage = client.get("/api/grids/mn-osm-35k/security-analysis", params={"outage_line_id": ref_id})
    assert outage.status_code == 404
    sensitivity = client.get("/api/grids/mn-osm-35k/sensitivity-analysis",
                             params={"outage_line_id": ref_id})
    assert sensitivity.status_code == 404
    monitored = client.get("/api/grids/mn-osm-35k/sensitivity-analysis",
                           params={"outage_line_id": "751261406-1_0", "monitored_line_id": ref_id})
    assert monitored.status_code == 422 and ref_id in monitored.json()["detail"]
    # The OSM way number alone is not a model line either.
    osm_id = ref_id.removeprefix("REF-L-")
    assert client.get("/api/grids/mn-osm-35k/security-analysis", params={"outage_line_id": osm_id}).status_code == 404


def test_kpg_has_no_reference_layer_endpoint():
    assert client.get("/api/grids/kr-kpg193/reference-layers").status_code == 404


# ------------------------------------------------------------------ selection rules (synthetic)
def _way(osm_id, coords, voltage="110000", other=None):
    return {"properties": {"id": int(osm_id), "tags.voltage": voltage, "other_tags": other},
            "geometry": {"type": "LineString", "coordinates": coords}}


def _seg(osm_id, suffix="0", bus="1"):
    return {"line_id": f"{osm_id}-1_{suffix}", "osm_id": osm_id, "voltage_kv": "110.0", "collapsed_bus_id": bus}


def test_line_rules_group_split_segments_and_exclude_weak_or_duplicate_ways():
    east = lambda lon0, km: [[lon0, 47.9], [lon0 + km / 74.6, 47.9]]   # ~74.6 km per degree at 47.9N
    raw = {
        "1": _way("1", east(106.0, 3.0)),                                   # kept; two split segments
        "2": _way("2", east(106.0, 0.2)),                                   # short span
        "3": _way("3", east(106.0, 3.0), voltage="35000"),                  # not 110 kV
        "4": _way("4", east(106.0, 3.0), other="{'tags.demolished:power': 'line'}"),
        "5": _way("5", east(107.0, 3.0)),                                   # on an existing map line
        "6": _way("6", [[106.0, 47.9 + 0.0005], [106.0 + 2.0 / 74.6, 47.9 + 0.0005]]),  # inside way 1 corridor
        "7": _way("7", [[106.5, 47.9], [106.5 + 2 / 74.6, 47.9], [106.5 + 0.1 / 74.6, 47.9]]),  # folds back
        "8": _way("8", east(108.0, 2.0)),                                   # partly displayed already
        "10": _way("10", east(108.49, 3.0)),                                # one end outside the country
    }
    collapsed = [_seg("1", "0"), _seg("1", "1"), _seg("2"), _seg("3"), _seg("4"), _seg("5"), _seg("6"), _seg("7"),
                 _seg("8"), _seg("10"), {"line_id": "9-1_0", "osm_id": "9", "voltage_kv": "110.0", "collapsed_bus_id": "1"}]
    lineage = {"8": {"map_ids": "8-1_1"}}
    existing = {"M-1": [[(107.0, 47.9), (107.1, 47.9)]]}
    buses = [{"bus_id": "1", "longitude": 106.0, "latitude": 47.9, "voltage_kv": 110.0}]
    country = [[[[100.0, 40.0], [108.5, 40.0], [108.5, 50.0], [100.0, 50.0], [100.0, 40.0]]]]
    lines, excluded = builder.select_lines(collapsed, raw, lineage, existing, buses, country)
    reasons = {item["osm_id"]: item["reason"] for item in excluded}
    assert [line["osm_id"] for line in lines] == ["1"]
    assert lines[0]["collapsed_segment_ids"] == ["1-1_0", "1-1_1"]
    assert lines[0]["duplicate_check"]["merged_osm_ids"] == ["6"]
    assert lines[0]["from"] == [106.0, 47.9] and lines[0]["display"] is True
    assert reasons == {
        "2": "short_span_fragment", "3": "voltage_not_110kV", "4": "demolished_or_disused_tag",
        "5": "overlaps_existing_map_line", "6": "contained_in_other_reference_line",
        "7": "straight_line_not_representative", "8": "way_partly_in_existing_map", "9": "no_raw_geometry",
        "10": "endpoint_outside_country",
    }


def test_substation_rules_keep_evidence_and_never_fill_missing_values():
    square = [[[100.0, 40.0], [110.0, 40.0], [110.0, 50.0], [100.0, 50.0], [100.0, 40.0]]]
    buses = [{"bus_id": "1", "longitude": 106.0, "latitude": 47.9, "voltage_kv": 110.0}]

    def sub(osm_id, lon, voltage=None, kind=None, other=None):
        return {"properties": {"id": osm_id, "tags.voltage": voltage, "tags.substation": kind, "other_tags": other},
                "geometry": {"type": "Point", "coordinates": [lon, 47.9]}}

    raw = {
        "1": sub(1, 106.05, "110000;35000", other="{'tags.name': 'Test'}"),   # kept, 3.7 km from bus
        "2": sub(2, 106.0501, "110000"),                                        # 7 m from #1 -> duplicate object
        "3": sub(3, 106.005, "110000"),                                         # same site as map bus
        "4": sub(4, 106.2),                                                     # no evidence
        "5": sub(5, 106.3, None, "transmission"),                              # kept, voltage stays empty
        "6": sub(6, 106.4, "10000"),                                            # below 35 kV
        "7": sub(7, 120.0, "220000"),                                           # outside outline
    }
    rows = [{"osm_id": k, "osm_type": "node", "final_bus_ids": "UNKNOWN", "reason": "x"} for k in raw]
    records, excluded = builder.select_substations(rows, raw, buses, [square])
    shown = {r["osm_id"]: r for r in records if r["display"]}
    assert sorted(shown) == ["1", "5"]
    assert shown["1"]["name"] == "Test" and shown["1"]["voltage_kv"] == [110.0, 35.0]
    assert shown["1"]["duplicate_check"]["merged_osm_ids"] == ["2"]
    assert shown["5"]["voltage_kv"] == [] and shown["5"]["name"] is None
    assert [r["osm_id"] for r in records if not r["display"]] == ["3"]
    assert {e["osm_id"]: e["reason"] for e in excluded} == {
        "2": "duplicate_osm_object", "4": "no_voltage_or_transmission_type_evidence",
        "6": "below_35kV", "7": "outside_country",
    }
