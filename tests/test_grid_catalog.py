import json

import pytest

from ai_ems.grid_catalog import (
    KPG_GRID_ID,
    MONGOLIA_GRID_ID,
    list_grids,
    load_mongolia_map,
)


def test_grid_catalog_does_not_claim_mongolia_analysis(tmp_path):
    entries = {item["id"]: item for item in list_grids(tmp_path / "mn.json")}

    assert entries[KPG_GRID_ID]["analysis_available"] is True
    assert entries[MONGOLIA_GRID_ID]["map_available"] is False
    assert entries[MONGOLIA_GRID_ID]["analysis_available"] is False
    assert entries[MONGOLIA_GRID_ID]["status"] == "preparing"


def test_available_mongolia_preview_is_map_only(tmp_path):
    target = tmp_path / "mn.json"
    target.write_text(json.dumps({
        "buses": [
            {"bus_id": "a", "latitude": 47.9, "longitude": 106.9, "name": "UB"},
            {"bus_id": "b", "latitude": 48.0, "longitude": 107.1},
        ],
        "lines": [{"line_id": "ab", "bus1_id": "a", "bus2_id": "b"}],
    }), encoding="utf-8")

    grid = {item["id"]: item for item in list_grids(target)}[MONGOLIA_GRID_ID]
    assert grid["map_available"] is True
    assert grid["analysis_available"] is False
    assert grid["status"] == "map_ready"

    data = load_mongolia_map(target)
    assert len(data["buses"]) == 2
    assert data["lines"][0]["bus1_id"] == "a"
    assert data["generators"] == []


@pytest.mark.parametrize("payload", [
    {"buses": [], "lines": []},
    {"buses": [{"bus_id": "a", "latitude": float("nan"), "longitude": 106.0}], "lines": []},
    {"buses": [{"bus_id": "a", "latitude": 47.0, "longitude": 106.0}],
     "lines": [{"line_id": "invalid", "bus1_id": "a", "bus2_id": "missing"}]},
    {"buses": [
        {"bus_id": "a", "latitude": 47.0, "longitude": 106.0},
        {"bus_id": "a", "latitude": 48.0, "longitude": 107.0},
    ], "lines": []},
])
def test_reject_invalid_map_data(tmp_path, payload):
    target = tmp_path / "mn.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mongolia_map(target)


def test_missing_preview(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_mongolia_map(tmp_path / "missing.json")
