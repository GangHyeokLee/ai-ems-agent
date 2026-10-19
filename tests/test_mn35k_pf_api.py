import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web_app

ROOT = Path(__file__).resolve().parents[1]
EXPORT_TEST = ROOT / "tests" / "test_mn35k_pf_export.py"
spec = importlib.util.spec_from_file_location("pf_api_fixture", EXPORT_TEST)
export_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_fixture)

client = TestClient(web_app.app)


def prepare_assets(tmp_path):
    inputs = export_fixture.fixture_inputs()
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps(inputs["map_data"]), encoding="utf-8")
    inputs["map_json_sha256"] = hashlib.sha256(map_file.read_bytes()).hexdigest()
    preview = export_fixture.module.build_preview(**inputs)
    preview_file = tmp_path / "preview.json"
    preview_file.write_text(json.dumps(preview, allow_nan=False), encoding="utf-8")
    return map_file, preview_file, preview


def test_fixed_pf_preview_endpoint_returns_only_validated_scenario(tmp_path, monkeypatch):
    map_file, preview_file, expected = prepare_assets(tmp_path)
    monkeypatch.setattr(web_app, "MONGOLIA_MAP_FILE", map_file)
    monkeypatch.setattr(web_app, "MONGOLIA_PF_PREVIEW_FILE", preview_file)

    response = client.get("/api/grids/mn-osm-35k/pf-preview")
    grids = client.get("/api/grids").json()["grids"]
    mongolia = next(item for item in grids if item["id"] == "mn-osm-35k")

    assert response.status_code == 200
    assert response.json() == expected
    assert mongolia["pf_preview_available"] is True
    assert mongolia["analysis_available"] is False
    assert client.get("/api/grids/kr-kpg193/map").status_code == 200


def test_missing_preview_is_404_but_map_route_remains_available(tmp_path, monkeypatch):
    map_file, _, _ = prepare_assets(tmp_path)
    monkeypatch.setattr(web_app, "MONGOLIA_MAP_FILE", map_file)
    monkeypatch.setattr(web_app, "MONGOLIA_PF_PREVIEW_FILE", tmp_path / "missing.json")

    assert client.get("/api/grids/mn-osm-35k/pf-preview").status_code == 404
    assert client.get("/api/grids/mn-osm-35k/map").status_code == 200
    grid = next(item for item in client.get("/api/grids").json()["grids"] if item["id"] == "mn-osm-35k")
    assert grid["map_available"] is True
    assert grid["pf_preview_available"] is False
    assert grid["analysis_available"] is False


@pytest.mark.parametrize(
    ("mutate", "status"),
    [
        (lambda preview: preview.__setitem__("broken", float("nan")), 422),
        (lambda preview: preview["metadata"].__setitem__("scenario_id", "single_slack"), 422),
    ],
)
def test_invalid_preview_is_422_without_affecting_map(tmp_path, monkeypatch, mutate, status):
    map_file, preview_file, preview = prepare_assets(tmp_path)
    changed = json.loads(json.dumps(preview))
    mutate(changed)
    preview_file.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(web_app, "MONGOLIA_MAP_FILE", map_file)
    monkeypatch.setattr(web_app, "MONGOLIA_PF_PREVIEW_FILE", preview_file)

    assert client.get("/api/grids/mn-osm-35k/pf-preview").status_code == status
    assert client.get("/api/grids/mn-osm-35k/map").status_code == 200
