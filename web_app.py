from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from ai_ems import load_network

CASE_FILE = "data/KPG193_ver2_0_pypowsybl.mat"
UI_DIR = Path("ui")

network = load_network(CASE_FILE)

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