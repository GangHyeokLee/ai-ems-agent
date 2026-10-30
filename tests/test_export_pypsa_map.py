"""Pure-Python exporter regression tests; no PyPSA download required."""

import csv
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_pypsa_map.py"
spec = importlib.util.spec_from_file_location("export_pypsa_map", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_powerplants_keep_duplicate_source_rows(tmp_path):
    file = tmp_path / "powerplants.csv"
    with file.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["Name", "Fueltype", "Capacity", "lat", "lon", "bus", "Technology"],
        )
        writer.writeheader()
        for size in ("92.63355", "92.63355"):
            writer.writerow({
                "Name": "Ulaanbaatar Tes Chp Mongolia",
                "Fueltype": "Oil", "Capacity": size,
                "lat": "47.8938", "lon": "106.8036", "bus": "27",
            })
        writer.writerow({
            "Name": "No coord", "Fueltype": "Solar", "Capacity": "15",
            "lat": "", "lon": "105", "bus": "27",
        })
        writer.writerow({
            "Name": "Wind", "Fueltype": "Wind", "Capacity": "",
            "lat": "47", "lon": "108", "bus": "unknown",
        })
    plants = module.read_powerplant_records(file, {"27"})
    assert len(plants) == 3
    assert plants[0]["record_id"] != plants[1]["record_id"]
    assert plants[0]["mapped_bus_id"] == "27"
    assert plants[0]["capacity_mw"] == 92.63355
    assert plants[2]["mapped_bus_id"] is None
    assert plants[2]["capacity_mw"] is None


def test_preview_with_powerplants(tmp_path):
    file = tmp_path / "powerplants.csv"
    file.write_text(
        "Name,Fueltype,Capacity,lat,lon,bus\n"
        "Solar A,Solar,10,47.9,106.9,27\n",
        encoding="utf-8",
    )
    network = SimpleNamespace(
        buses=pd.DataFrame(
            [{"x": 106.9, "y": 47.9}, {"x": 107.0, "y": 48.0}],
            index=["27", "28"],
        ),
        lines=pd.DataFrame(
            [{"bus0": "27", "bus1": "28"}],
            index=["line-1"],
        ),
    )
    output = module.build_preview(network, file)
    assert len(output["buses"]) == 2
    assert len(output["lines"]) == 1
    assert output["powerplants"][0]["name"] == "Solar A"
    assert output["powerplants"][0]["mapped_bus_id"] == "27"



def test_country_geojson_multipolygon_and_holes(tmp_path):
    import json

    data = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature", "properties": {"name": "Mongolia"},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[
                        [90, 42], [120, 42], [120, 50], [90, 50], [90, 42]
                    ]],
                    [[
                        [100, 44], [101, 44], [101, 45], [100, 45], [100, 44]
                    ]],
                ],
            },
        }],
    }
    path = tmp_path / "country_shapes.geojson"
    path.write_text(json.dumps(data), encoding="utf-8")
    polygons = module.read_country_polygons(path)
    assert len(polygons) == 2
    assert all(len(polygon[0]) >= 4 for polygon in polygons)
    assert polygons[0][0][0] == polygons[0][0][-1]


def test_country_geojson_rejects_invalid_coordinates(tmp_path):
    import json
    import pytest

    path = tmp_path / "bad.geojson"
    path.write_text(json.dumps({
        "type": "Polygon",
        "coordinates": [[[90, 42], [200, 42], [120, 50], [90, 42]]]
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid country polygon coordinate"):
        module.read_country_polygons(path)



def test_admin_regions_geojson(tmp_path):
    import json

    polygon = [[[90, 42], [100, 42], [100, 50], [90, 50], [90, 42]]]
    features = [
        {
            "type": "Feature",
            "properties": {"NAME_1": name},
            "geometry": {"type": "Polygon", "coordinates": polygon},
        }
        for name in ["Arkhangai", "Dornod"]
    ]
    source = tmp_path / "gadm_shapes.geojson"
    source.write_text(json.dumps({
        "type": "FeatureCollection", "features": features,
    }), encoding="utf-8")
    regions = module.read_admin_regions(source)
    assert [r["name"] for r in regions] == ["Arkhangai", "Dornod"]
    assert len(regions[0]["polygons"][0][0]) >= 4


def test_expanded_preview_has_voltage_transformers_without_fake_plant_bus(tmp_path):
    plant_file = tmp_path / "powerplants.csv"
    plant_file.write_text("Name,Fueltype,Capacity,lat,lon,bus\\nSolar,Solar,5,47.0,106.0,35\\n".replace("\\n", "\n"),
                          encoding="utf-8")
    network = SimpleNamespace(
        buses=pd.DataFrame([
            {"x": 106.0, "y": 47.0, "v_nom": 35},
            {"x": 106.2, "y": 47.2, "v_nom": 110},
        ], index=["35", "110"]),
        lines=pd.DataFrame([
            {"bus0": "35", "bus1": "110", "v_nom": 35, "length": 8.5},
        ], index=["line-A"]),
        transformers=pd.DataFrame([
            {"bus0": "35", "bus1": "110", "s_nom": 2000},
        ], index=["trafo-A"]),
    )
    preview = module.build_preview(network, plant_file, include_plant_bus_mapping=False)
    assert preview["buses"][0]["voltage_kv"] == 35
    assert preview["lines"][0]["voltage_kv"] == 35
    assert preview["lines"][0]["length_km"] == 8.5
    assert preview["transformers"] == [{
        "transformer_id": "trafo-A", "bus1_id": "35", "bus2_id": "110", "inferred": True,
    }]
    assert preview["powerplants"][0]["mapped_bus_id"] is None
