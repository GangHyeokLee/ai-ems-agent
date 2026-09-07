from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ai_ems import load_network
import pandas as pd

from ai_ems.tools.security_tools import run_line_contingency
from ai_ems.tools.sensitivity_tools import (
    rank_generator_sensitivities,
)

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

@app.get("/api/security-analysis")
def security_analysis(
    outage_line_id: str,
    monitored_line_id: str | None = None,
):
    monitored_line_ids = (
        [monitored_line_id] if monitored_line_id is not None else None
    )

    result = run_line_contingency(
        network,
        outage_line_id=outage_line_id,
        monitored_line_ids=monitored_line_ids
    )

    violations  = []

    for item in result["violated_equipment"]:
        violations.append(
            {
                "equipment_id": item["equipment_id"],
                "limit": item["limit"],
                "max_value": item["max_value"],
                "loading_percent": item["loading_percent"],
            }
        )

    return {
        "outage_line_id": result["outage_line_id"],
        "base_converged": result["base_converged"],
        "post_status": result["post_status"],
        "violated_equipment_count": result[
            "violated_equipment_count"
        ],
        "violations": violations,
    }

@app.get("/api/sensitivity-analysis")
def sensitivity_analysis(
    outage_line_id: str,
    monitored_line_id: str | None = None,
    top_n: int = 5,
):
    if monitored_line_id is None:
        security_result = run_line_contingency(
            network,
            outage_line_id=outage_line_id,
        )

        lines = network.get_lines()

        violated_lines = [
            item
            for item in security_result["violated_equipment"]
            if item["equipment_id"] in lines.index
            and item.get("loading_percent") is not None
        ]

        if not violated_lines:
            return {
                "outage_line_id": outage_line_id,
                "monitored_line_id": None,
                "candidates": [],
                "message": "No violated transmission line found.",
            }

        violated_lines.sort(
            key=lambda item: item["loading_percent"],
            reverse=True,
        )

        monitored_line_id = (
            violated_lines[0]["equipment_id"]
        )

    result = rank_generator_sensitivities(
        network,
        outage_line_id=outage_line_id,
        monitored_line_id=monitored_line_id,
        top_n=top_n,
    )

    generators = network.get_generators(
        all_attributes=True
    )

    candidates = []

    for candidate in result["candidates"]:
        generator_id = candidate["generator_id"]

        row = generators.loc[generator_id]

        voltage_level_id = row[
            "voltage_level_id"
        ]

        bus_id = int(
            voltage_level_id.replace("VL-", "")
        )

        location = bus_location_map.get(bus_id)

        candidates.append(
            {
                **candidate,
                "bus_id": bus_id,
                "latitude": (
                    location["latitude"]
                    if location
                    else None
                ),
                "longitude": (
                    location["longitude"]
                    if location
                    else None
                ),
                "name_korean": (
                    location["name_korean"]
                    if location
                    else None
                ),
                "name_english": (
                    location["name_english"]
                    if location
                    else None
                ),
            }
        )

    return {
        "outage_line_id": outage_line_id,
        "monitored_line_id": monitored_line_id,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }