from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ai_ems import load_network
import pandas as pd

CASE_FILE = "data/KPG193_ver2_0_pypowsybl.mat"
BUS_LOCATION_FILE = "data/bus_location.csv"
UI_DIR = Path("ui")

network = load_network(CASE_FILE)

bus_locations = pd.read_csv(BUS_LOCATION_FILE)

bus_location_map = {
    int(row["bus_id"]): {
        "latitude": float(row["Latitude"]),
        "longitude": float(row["Longitude"]),
        "name_korean": row["name_Korean"],
        "name_english": row["name_English"],
    }
    for _, row in bus_locations.iterrows()
}

app = FastAPI(
  title="AI-EMS Agent",
)


@app.get("/")
def index():
    return FileResponse(
        UI_DIR / "index.html"
    )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
    }


@app.get("/api/network-summary")
def network_summary():
    return {
        "buses": len(network.get_buses()),
        "lines": len(network.get_lines()),
        "generators": len(network.get_generators()),
        "loads": len(network.get_loads()),
    }

@app.get("/api/network-map")
def network_map():
    buses = []

    for bus_id, location in bus_location_map.items():
        buses.append(
            {
                "bus_id": bus_id,
                **location,
            }
        )

    lines_df = network.get_lines()

    lines = []

    for line_id, row in lines_df.iterrows():
        voltage_level1_id = row["voltage_level1_id"]
        voltage_level2_id = row["voltage_level2_id"]

        bus1_id = int(
            voltage_level1_id.replace("VL-", "")
        )

        bus2_id = int(
            voltage_level2_id.replace("VL-", "")
        )

        if(
            bus1_id not in bus_location_map
            or bus2_id not in bus_location_map
        ):
            continue

        lines.append(
            {
                "line_id": line_id,
                "bus1_id": bus1_id,
                "bus2_id": bus2_id,
            }
        )

    return {
        "buses": buses,
        "lines": lines,
    }

