import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from ai_ems.grid_catalog import MONGOLIA_GRID_ID, list_grids, load_mongolia_map
from ai_ems.pf_preview import load_mongolia_pf_preview, validate_pf_preview

ROOT = Path(__file__).resolve().parents[1]
EXPORT_TEST = ROOT / "tests" / "test_mn35k_pf_export.py"
spec = importlib.util.spec_from_file_location("pf_export_fixture", EXPORT_TEST)
export_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_fixture)


def write_valid_assets(tmp_path):
    inputs = export_fixture.fixture_inputs()
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps(inputs["map_data"]), encoding="utf-8")
    map_hash = hashlib.sha256(map_file.read_bytes()).hexdigest()
    inputs["map_json_sha256"] = map_hash
    preview = export_fixture.module.build_preview(**inputs)
    preview_file = tmp_path / "preview.json"
    preview_file.write_text(
        json.dumps(preview, allow_nan=False),
        encoding="utf-8",
    )
    return map_file, preview_file, preview


def test_valid_preview_is_read_only_contract_and_matches_map_assets(tmp_path):
    map_file, preview_file, expected = write_valid_assets(tmp_path)

    actual = load_mongolia_pf_preview(preview_file, map_file)

    assert actual == expected
    assert len(actual["buses"]) == 166
    assert len(actual["lines"]) == 156
    assert len(actual["transformers"]) == 27
    assert actual["metadata"]["actual_operating_data"] is False
    assert "analysis_available" not in actual["metadata"]


def test_missing_preview_is_distinct_from_missing_map(tmp_path):
    map_file, _, _ = write_valid_assets(tmp_path)

    with pytest.raises(FileNotFoundError, match="PF 미리보기"):
        load_mongolia_pf_preview(tmp_path / "missing.json", map_file)

    with pytest.raises(FileNotFoundError, match="지도 데이터"):
        load_mongolia_pf_preview(tmp_path / "preview.json", tmp_path / "no-map.json")


def test_invalid_json_returns_validation_error(tmp_path):
    map_file, preview_file, _ = write_valid_assets(tmp_path)
    preview_file.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        load_mongolia_pf_preview(preview_file, map_file)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["metadata"].__setitem__("scenario_id", "reference_108_generators"), "시나리오"),
        (lambda data: data["metadata"].__setitem__("actual_operating_data", True), "합성"),
        (lambda data: data["metadata"].__setitem__("map_json_sha256", "e" * 64), "fingerprint"),
        (lambda data: data["buses"][0].__setitem__("v_mag_pu", float("nan")), "유한"),
        (lambda data: data["lines"][0].__setitem__("bus2_id", "wrong"), "연결 모선"),
        (lambda data: data["buses"][0].pop("v_ang_deg"), "필수 필드"),
    ],
)
def test_rejects_corrupt_or_misidentified_preview(tmp_path, mutate, message):
    map_file, _, preview = write_valid_assets(tmp_path)
    changed = json.loads(json.dumps(preview))
    mutate(changed)

    with pytest.raises(ValueError, match=message):
        validate_pf_preview(
            changed,
            load_mongolia_map(map_file),
            hashlib.sha256(map_file.read_bytes()).hexdigest(),
        )


def test_preview_capability_does_not_enable_analysis(tmp_path):
    map_file, preview_file, _ = write_valid_assets(tmp_path)

    grids = {item["id"]: item for item in list_grids(map_file, preview_file)}

    assert grids[MONGOLIA_GRID_ID]["map_available"] is True
    assert grids[MONGOLIA_GRID_ID]["pf_preview_available"] is True
    assert grids[MONGOLIA_GRID_ID]["analysis_available"] is False
    assert grids[MONGOLIA_GRID_ID]["status"] == "map_ready"


def test_catalog_does_not_claim_pf_capability_without_file(tmp_path):
    map_file = tmp_path / "map.json"
    map_file.write_text('{"buses":[],"lines":[]}', encoding="utf-8")

    grid = {item["id"]: item for item in list_grids(map_file, tmp_path / "missing.json")}[MONGOLIA_GRID_ID]

    assert grid["map_available"] is True
    assert grid["pf_preview_available"] is False
    assert grid["analysis_available"] is False
