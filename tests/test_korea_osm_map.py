"""Small invalid fixtures and separate checks against the actual national dataset."""
import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from ai_ems.grid_catalog import KPG_GRID_ID, KOREA_OSM_GRID_ID, list_grids, load_korea_map

ROOT = Path(__file__).resolve().parents[1]
REAL_MAP = ROOT / "data/grids/kr-osm-transmission-map.json"


def small_map():
    return dict(schema_version="kr-grid-preview-1.0", grid_id=KOREA_OSM_GRID_ID, country="KR",
                metadata=dict(model_kind="geographic_topology_only", ac_pf_readiness="NOT_READY", operational=False,
                              attribution="OSM", license="ODbL-1.0"),
                buses=[dict(bus_id="a", latitude=36, longitude=127, voltage_kv=154, osm_id="node/1", source="OSM", inferred=True),
                       dict(bus_id="b", latitude=37, longitude=128, voltage_kv=154, osm_id="node/2")],
                lines=[dict(line_id="ab", bus1_id="a", bus2_id="b", voltage_kv=154, osm_id="way/3", source="OSM",
                            coordinates=[[127,36],[127.4,36.2],[128,37]], inferred=False)],
                powerplants=[dict(record_id="wri-1",latitude=36,longitude=127,capacity_mw=90,mapped_bus_id=None,
                                 name="Historical plant",fueltype="Hydro",source="WRI",current_operational_status="unverified_historical_source")])


def write_map(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_fixture_preserves_route_provenance_and_unmatched_plants(tmp_path):
    raw=small_map()
    data=load_korea_map(write_map(tmp_path/"kr.json",raw))
    for key in ("coordinates","osm_id","source","inferred"):
        assert data["lines"][0][key]==raw["lines"][0][key]
    assert data["buses"][0]["inferred"] is True
    assert data["powerplants"][0]["mapped_bus_id"] is None
    assert data["powerplants"][0]["source"]=="WRI"


@pytest.mark.parametrize("mutate",[
    lambda d:d["lines"][0].update(bus2_id="unknown"),
    lambda d:d["lines"].append(copy.deepcopy(d["lines"][0])),
    lambda d:d["lines"][0].update(coordinates=[[127,36],[999,37]]),
    lambda d:d["lines"][0].update(coordinates=[[127,36],[127,37]]),
    lambda d:d["buses"][0].update(latitude=float("nan")),
    lambda d:d["buses"][0].update(voltage_kv=345),
    lambda d:d["metadata"].update(operational=True),
    lambda d:d["powerplants"][0].update(mapped_bus_id="a"),
    lambda d:d["powerplants"][0].update(capacity_mw=-1),
    lambda d:d["lines"].__setitem__(0,None),
    lambda d:d["lines"][0].update(osm_id="invalid"),
    lambda d:d["lines"][0].update(inferred="false"),
    lambda d:d["lines"][0].update(source=[]),
])
def test_corrupt_data_disables_only_korea(tmp_path,mutate):
    raw=small_map(); mutate(raw)
    file=write_map(tmp_path/"kr.json",raw)
    with pytest.raises(ValueError): load_korea_map(file)
    grids=list_grids(tmp_path/"mn.json",korea_map_file=file)
    assert grids[0]["map_available"] is True and grids[2]["map_available"] is False


def test_missing_invalid_json_and_changed_cache_identity(tmp_path):
    path=tmp_path/"kr.json"
    assert not list_grids(path,korea_map_file=path)[2]["map_available"]
    write_map(path,small_map())
    assert load_korea_map(path)["lines"]
    path.write_text("{broken",encoding="utf-8")
    assert not list_grids(path,korea_map_file=path)[2]["map_available"]
    write_map(path,small_map())
    assert list_grids(path,korea_map_file=path)[2]["map_available"]


@pytest.mark.skipif(not REAL_MAP.is_file(), reason="Import actual Korean national map first")
def test_actual_national_map_inventory_routes_and_flags():
    data=load_korea_map(REAL_MAP)
    assert (len(data["buses"]),len(data["lines"]),len(data["powerplants"]))==(4197,3984,132)
    assert Counter(l["voltage_kv"] for l in data["lines"])=={154:2637,345:1155,765:192}
    assert sum(b.get("shared_support_branching_requires_review",False) for b in data["buses"])==1170
    assert all(l["osm_id"].startswith("way/") and l.get("coordinates") for l in data["lines"])
    assert any(len(l["coordinates"])>2 for l in data["lines"])
    assert not data["generators"] and not data["transformers"]
    assert len(data["admin_regions"])==17 and data["metadata"]["operational"] is False


def test_import_is_atomic_source_unchanged_and_source_cannot_be_target(tmp_path):
    source=write_map(tmp_path/"source.json",small_map()); target=tmp_path/"imported.json"
    original=source.read_bytes()
    command=[sys.executable,str(ROOT/"scripts/import_kr_osm_map.py"),"--source",str(source),"--output",str(target)]
    subprocess.run(command,check=True,capture_output=True)
    assert source.read_bytes()==original
    assert load_korea_map(target)["metadata"]["source_map_sha256"]==hashlib.sha256(original).hexdigest()
    source.write_text("broken")
    imported=target.read_bytes()
    assert subprocess.run(command,capture_output=True).returncode!=0 and target.read_bytes()==imported
    assert subprocess.run(command[:-1]+[str(source)],capture_output=True).returncode!=0


def test_grid_catalog_and_api_missing_corrupt_recovery_and_fixed_path(tmp_path,monkeypatch):
    import web_app
    path=tmp_path/"kr.json"; monkeypatch.setattr(web_app,"KOREA_MAP_FILE",path)
    client=TestClient(web_app.app)
    assert client.get(f"/api/grids/{KOREA_OSM_GRID_ID}/map").status_code==404
    path.write_text("bad")
    assert client.get(f"/api/grids/{KOREA_OSM_GRID_ID}/map").status_code==422
    write_map(path,small_map())
    response=client.get("/api/grids").json()
    assert response["default_grid_id"]==KPG_GRID_ID
    assert len(response["grids"])==3
    kr=response["grids"][2]
    assert kr["map_available"] and kr["chat_mode"]=="disabled"
    assert not any(kr[k] for k in ["analysis_available","pf_preview_available","security_analysis_available","sensitivity_analysis_available","generator_contingency_available","chat_available"])
    assert client.get(f"/api/grids/{KOREA_OSM_GRID_ID}/map?file=/etc/passwd").json()["buses"][0]["bus_id"]=="a"
    assert client.get("/api/grids/kr-kpg193/map").status_code==200
    assert client.get("/api/grids/mn-osm-35k/map").status_code==200


def test_korea_never_invokes_either_analysis_or_agent(tmp_path,monkeypatch):
    import web_app
    blocked=Mock(side_effect=AssertionError("an engine/agent was invoked"))
    for name in ["run_line_contingency","rank_generator_sensitivities","chat","mongolia_chat","_mongolia_security"]:
        monkeypatch.setattr(web_app,name,blocked)
    client=TestClient(web_app.app)
    for operation in ["pf-preview","security-analysis","sensitivity-analysis","generator-contingency-analysis","opf","redispatch"]:
        for method in [client.get,client.post]:
            assert method(f"/api/grids/{KOREA_OSM_GRID_ID}/{operation}").status_code==409
    assert client.post(f"/api/grids/{KOREA_OSM_GRID_ID}/chat",json=dict(session_id="x",message="N-1")).status_code==409
    for endpoint in ["security-analysis","sensitivity-analysis"]:
        assert client.get(f"/api/{endpoint}",params=dict(grid_id=KOREA_OSM_GRID_ID,outage_line_id="LINE-16-28")).status_code==409
    assert client.post("/api/generator-contingency-analysis",json=dict(grid_id=KOREA_OSM_GRID_ID,generator_id="GEN-1")).status_code==409
    blocked.assert_not_called()


def test_legacy_chat_rejects_korea_in_body_or_query(monkeypatch):
    import web_app
    graph=Mock(side_effect=AssertionError("agent invoked"))
    monkeypatch.setattr(web_app.agent_graph,"invoke",graph)
    client=TestClient(web_app.app)
    body=dict(session_id="x",message="run")
    assert client.post("/api/chat",json={**body,"grid_id":KOREA_OSM_GRID_ID}).status_code==409
    assert client.post("/api/chat?grid_id="+KOREA_OSM_GRID_ID,json=body).status_code==409
    assert client.post("/api/grids/mn-osm-35k/chat",json={**body,"grid_id":KOREA_OSM_GRID_ID}).status_code==409
    graph.assert_not_called()


@pytest.mark.parametrize("endpoint",["security-analysis","sensitivity-analysis","contingency-response","redispatch-validation","generator-contingency-analysis"])
def test_physics_api_rejects_unsupported_grid_before_engine(endpoint,monkeypatch):
    from ai_ems.api.routes import create_physics_router
    from fastapi import FastAPI
    app=FastAPI(); engine=Mock(side_effect=AssertionError("engine invoked"))
    app.include_router(create_physics_router(engine,"unused"))
    client=TestClient(app)
    for name in ["run_line_contingency","rank_generator_sensitivities","analyze_contingency_response","validate_balanced_redispatch","analyze_generator_contingency"]:
        monkeypatch.setattr("ai_ems.api.routes."+name,engine)
    valid=dict(outage_line_id="LINE-16-28",monitored_line_id="LINE-16-22",up_generator_id="GEN-19#0",down_generator_id="GEN-124#1",delta_mw=10,generator_id="GEN-124#1")
    for url,body in [(f"/api/v1/{endpoint}",{**valid,"grid_id":KOREA_OSM_GRID_ID}),
                     (f"/api/v1/{endpoint}?grid_id={KOREA_OSM_GRID_ID}",valid)]:
        assert client.post(url,json=body).status_code==422
    engine.assert_not_called()


# ----- Land boundary (coastline) layer: separate from the OSM territorial relation -----
REAL_SOURCE = ROOT.parent / "korea-grid-poc/outputs/map/kr_grid_preview.json"
TERRITORIAL = [[[124.4,37.9],[132.1,37.2],[126.3,32.9],[124.4,37.9]]]
JEJU_WITH_HOLE = [[[126.2,33.2],[126.9,33.2],[126.9,33.55],[126.2,33.55],[126.2,33.2]],
                  [[126.5,33.3],[126.6,33.3],[126.6,33.4],[126.5,33.4],[126.5,33.3]]]
ULLEUNG = [[[130.8,37.45],[130.92,37.45],[130.92,37.55],[130.8,37.45]]]


def land_file(tmp_path, polygons=None, kind="land_polygons"):
    data=dict(schema_version="kr-land-boundary-1.0",country="KR",
              polygons=[JEJU_WITH_HOLE,ULLEUNG] if polygons is None else polygons,
              metadata=dict(kind=kind,source="OSM natural=coastline",license="ODbL-1.0"))
    path=tmp_path/"land.json"; path.write_text(json.dumps(data)); return path


def run_import(source, target, land=None):
    command=[sys.executable,str(ROOT/"scripts/import_kr_osm_map.py"),"--source",str(source),"--output",str(target)]
    return subprocess.run(command+(["--land-boundary",str(land)] if land else []),capture_output=True)


def contains(polygon, lon, lat):
    """Even-odd point in polygon over all rings (outer + holes)."""
    inside=False
    for ring in polygon:
        for (x1,y1),(x2,y2) in zip(ring,ring[1:]):
            if (y1>lat)!=(y2>lat) and lon<x1+(lat-y1)*(x2-x1)/(y2-y1): inside=not inside
    return inside


def test_territorial_country_polygon_is_never_drawn_as_land(tmp_path):
    raw=small_map(); raw["country_polygons"]=[TERRITORIAL]
    assert load_korea_map(write_map(tmp_path/"kr.json",raw))["country_polygons"]==[]
    raw["metadata"]["land_boundary"]=dict(kind="land_polygons")
    assert load_korea_map(write_map(tmp_path/"kr2.json",raw))["country_polygons"]==[TERRITORIAL]


def test_import_land_boundary_replaces_only_geography(tmp_path):
    raw=small_map(); raw["country_polygons"]=[TERRITORIAL]
    raw["admin_regions"]=[dict(name="Jeju",polygons=[[JEJU_WITH_HOLE[0]]])]
    source=write_map(tmp_path/"source.json",raw); land=land_file(tmp_path)
    plain,with_land=tmp_path/"plain.json",tmp_path/"with_land.json"
    assert run_import(source,plain).returncode==0 and run_import(source,with_land,land).returncode==0
    a,b=load_korea_map(plain),load_korea_map(with_land)
    assert a["country_polygons"]==[] and "land_boundary" not in a["metadata"]
    assert b["country_polygons"]==[JEJU_WITH_HOLE,ULLEUNG]  # multiple polygons and inner rings kept
    assert b["metadata"]["land_boundary"]["file_sha256"]==hashlib.sha256(land.read_bytes()).hexdigest()
    for key in ("buses","lines","powerplants","admin_regions"):
        assert a[key]==b[key]
    assert b["lines"][0]["coordinates"]==raw["lines"][0]["coordinates"]


@pytest.mark.parametrize("polygons,kind",[
    ([[[[126,33],[127,33],[127,34],[126,33.5]]]],"land_polygons"),     # ring not closed
    ([[[[126,33],[127,33],[126,33]]]],"land_polygons"),                # fewer than 4 points
    ([[[[126,33],[140,33],[127,34],[126,33]]]],"land_polygons"),       # outside Korea
    ([[[[126,33],[127,"x"],[127,34],[126,33]]]],"land_polygons"),
    ([[[[126,33,0],[127,33],[127,34],[126,33]]]],"land_polygons"),
    ([[[[126,33],[float("nan"),33],[127,34],[126,33]]]],"land_polygons"),
    ([[]],"land_polygons"),
    ([TERRITORIAL],"territorial_sea"),
])
def test_invalid_land_boundary_is_rejected_and_previous_import_kept(tmp_path,polygons,kind):
    source=write_map(tmp_path/"source.json",small_map()); target=tmp_path/"map.json"
    assert run_import(source,target,land_file(tmp_path)).returncode==0
    imported=target.read_bytes()
    assert run_import(source,target,land_file(tmp_path,polygons,kind)).returncode!=0
    assert target.read_bytes()==imported


def test_mongolia_outline_unchanged_by_korea_land_handling():
    from ai_ems.grid_catalog import load_mongolia_map
    path=ROOT/"data/grids/mn-osm-35k-map.json"
    if not path.is_file(): pytest.skip("Mongolia map not generated")
    raw=json.loads(path.read_text(encoding="utf-8")); data=load_mongolia_map(path)
    assert data["country_polygons"]==raw["country_polygons"]
    assert [r["polygons"] for r in data["admin_regions"]]==[r["polygons"] for r in raw["admin_regions"]]


@pytest.mark.skipif(not REAL_MAP.is_file(), reason="Import actual Korean national map first")
def test_actual_land_boundary_is_coastline_not_territorial_sea():
    data=load_korea_map(REAL_MAP); polygons=data["country_polygons"]
    land=data["metadata"]["land_boundary"]
    assert land["kind"]=="land_polygons" and land["license"]=="ODbL-1.0" and len(polygons)==land["displayed_polygons"]
    lons=[p[0] for polygon in polygons for ring in polygon for p in ring]
    assert max(lons)<131.9 and min(lons)>124.5  # the 12nm circle reached 132.12E, the baseline 124.37E
    def on_land(lon,lat): return any(contains(p,lon,lat) for p in polygons)
    for name,lon,lat in [("Seoul",126.98,37.57),("Jeju",126.53,33.36),("Ulleungdo",130.87,37.50),
                         ("Dokdo Dongdo",131.8696,37.2412),("Dokdo Seodo",131.8650,37.2420),("Busan",129.07,35.18)]:
        assert on_land(lon,lat),name
    sea=[(126.0,35.9),(126.5,33.9),(132.0,37.24),(131.0,37.30)]  # West Sea, Jeju strait, Dokdo/Ulleung territorial sea
    assert not any(on_land(lon,lat) for lon,lat in sea)
    if REAL_SOURCE.is_file():
        territorial=json.loads(REAL_SOURCE.read_text(encoding="utf-8"))["country_polygons"]
        assert all(any(contains(p,lon,lat) for p in territorial) for lon,lat in sea)  # what the old outline filled
    dokdo=[p for p in polygons if all(131.85<x<131.88 and 37.23<y<37.25 for x,y in p[0])]
    assert len(dokdo)>=2


@pytest.mark.skipif(not (REAL_MAP.is_file() and REAL_SOURCE.is_file()), reason="actual source and import required")
def test_actual_transmission_data_identical_to_source_after_land_import():
    imported,source=load_korea_map(REAL_MAP),load_korea_map(REAL_SOURCE)
    for key in ("buses","lines","powerplants","admin_regions","transformers","generators"):
        assert imported[key]==source[key],key
    assert imported["metadata"]["source_map_sha256"]==hashlib.sha256(REAL_SOURCE.read_bytes()).hexdigest()
    assert imported["metadata"]["ac_pf_readiness"]=="NOT_READY"
