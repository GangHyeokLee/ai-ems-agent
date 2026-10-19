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
