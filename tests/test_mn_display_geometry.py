"""Display geometry must not change map/model contracts or other grids."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import web_app
from ai_ems.mongolia_geometry import DISPLAY_GEOMETRY_SHA256, load_mongolia_display_geometry

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "data/grids/mn-osm-35k-display-geometry.json"
BASE = ROOT / "data/grids/mn-osm-35k-map.json"
pytestmark = pytest.mark.skipif(not ASSET.is_file(), reason="optional generated GIS display asset unavailable")
client = TestClient(web_app.app)


def test_only_existing_lines_and_no_connector_rendering():
    payload = load_mongolia_display_geometry(ASSET, BASE)
    base = json.loads(BASE.read_text())
    assert hashlib.sha256(ASSET.read_bytes()).hexdigest() == DISPLAY_GEOMETRY_SHA256
    assert payload["base_map_sha256"] == hashlib.sha256(BASE.read_bytes()).hexdigest()
    assert payload["display_only"] and payload["connectors"] == "omitted"
    assert len(payload["lines"]) == 141 and len(payload["excluded"]) == 18
    assert {x["line_id"] for x in payload["lines"]+payload["excluded"]} == {x["line_id"] for x in base["lines"]}
    assert sum(len(x["osm_parts"]) for x in payload["lines"]) == 195
    assert sum(x["omitted_connector_km"] for x in payload["lines"]) == pytest.approx(152.2481314423)
    assert all(not x["line_id"].startswith("REF-") for x in payload["lines"])


def test_tampered_coordinates_fail_approved_hash(tmp_path):
    payload = json.loads(ASSET.read_text())
    payload["lines"][0]["osm_parts"][0][0][0] += 0.01
    target = tmp_path / "tampered.json"
    target.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="승인 해시"):
        load_mongolia_display_geometry(target, BASE)


def test_different_map_is_rejected(tmp_path):
    target = tmp_path / "map.json"
    target.write_bytes(BASE.read_bytes()+b"\n")
    with pytest.raises(ValueError, match="현재 지도"):
        load_mongolia_display_geometry(ASSET, target)


def test_api_optional_geometry_keeps_map_pf_and_other_grids(monkeypatch, tmp_path):
    paths = [f"/api/grids/{g}/map" for g in ("mn-osm-35k", "kr-kpg193", "kr-osm-transmission")]
    paths.append("/api/grids/mn-osm-35k/pf-preview")
    before = {p: client.get(p).json() for p in paths}
    response = client.get("/api/grids/mn-osm-35k/display-geometry")
    assert response.status_code == 200 and len(response.json()["lines"]) == 141
    monkeypatch.setattr(web_app, "MONGOLIA_DISPLAY_GEOMETRY_FILE", tmp_path / "absent.json")
    assert client.get("/api/grids/mn-osm-35k/display-geometry").status_code == 404
    assert {p: client.get(p).json() for p in paths} == before
    # Korea's existing generic operation handler returns 409; KPG has no route.
    for grid, status in (("kr-kpg193", 404), ("kr-osm-transmission", 409)):
        assert client.get(f"/api/grids/{grid}/display-geometry").status_code == status


def test_geometry_uses_selected_bundle_map(monkeypatch, tmp_path):
    selected = tmp_path / "bundle-map.json"
    selected.write_bytes(BASE.read_bytes()+b"\n")
    runtime = SimpleNamespace(selected=True, guard=lambda: None, files=lambda: SimpleNamespace(map=selected))
    monkeypatch.setattr(web_app, "_mongolia_bundle_runtime", runtime)
    assert client.get("/api/grids/mn-osm-35k/display-geometry").status_code == 422
