"""GIS-only export checks; no PF, model creation or source writes."""
import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("shapely")
pytest.importorskip("pyproj")
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("display_export", ROOT / "scripts/export_mn_display_geometry.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)
PROJECT = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def test_source_gaps_are_never_joined_even_when_clean_contains_connector():
    source = MultiLineString([[(106, 47), (106.01, 47)], [(106.02, 47), (106.03, 47)]])
    built = LineString([(106, 47), (106.01, 47), (106.02, 47), (106.03, 47)])
    parts, supported, omitted = exporter.source_parts(built, source, built, PROJECT)
    assert parts == [[[106, 47], [106.01, 47]], [[106.02, 47], [106.03, 47]]]
    assert supported > 0 and omitted > 0


def test_build_endpoint_connectors_are_omitted():
    raw = LineString([(106, 47), (106.01, 47), (106.01, 47.01)])
    built = LineString([(105.99, 47), *raw.coords, (106.02, 47.01)])
    parts, _, omitted = exporter.source_parts(built, raw, raw, PROJECT)
    assert parts == [[list(x) for x in raw.coords]]
    assert omitted > 1


def test_actual_export_reproduces_reviewed_asset():
    earth = ROOT.parent / "mongolia-grid-poc/pypsa-earth"
    if not earth.is_dir():
        pytest.skip("existing Mongolia source checkout unavailable")
    actual = exporter.build(earth, earth.parent / "reports/model_refinement/line_length_changes.csv",
                            ROOT / "data/grids/mn-osm-35k-map.json")
    assert actual == json.loads((ROOT / "data/grids/mn-osm-35k-display-geometry.json").read_text())
