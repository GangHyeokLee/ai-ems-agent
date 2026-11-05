"""Korean geographic background is separate from synthetic KPG network."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_kpg_geography.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("prepare_kpg_geography", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _geojson(name):
    ring = [[126.0, 35.0], [128.0, 35.0], [128.0, 38.0], [126.0, 38.0], [126.0, 35.0]]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"NAME_1": name},
         "geometry": {"type": "Polygon", "coordinates": [ring]}}
    ]}


def test_invalid_korea_admin_count_is_rejected():
    with pytest.raises(ValueError, match="Expected 17"):
        module.build_geography(_geojson("KOR"), {
            "type": "FeatureCollection",
            "features": _geojson("Seoul")["features"] * 2,
        })


def test_reject_non_allowlisted_download_domain():
    with pytest.raises(ValueError, match="Unapproved"):
        module.fetch_json("https://example.invalid/secret.json")
