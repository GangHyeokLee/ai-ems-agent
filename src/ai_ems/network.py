from __future__ import annotations

from pathlib import Path
from typing import Any

import pypowsybl as pp


IMPORT_PARAMETERS = {
    "matpower.import.ignore-base-voltage": "false",
}

LOADFLOW_PARAMETERS = pp.loadflow.Parameters(
    distributed_slack=False,
)


def load_network(case_path: str | Path):
    """Load a PyPowSyBl-compatible network from a MATPOWER file."""
    path = Path(case_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Network case file not found: {path}")

    return pp.network.load(
        path,
        parameters=IMPORT_PARAMETERS,
    )


def run_ac_load_flow(network) -> dict[str, Any]:
    """Run AC load flow and return a JSON-serializable result summary."""
    result = pp.loadflow.run_ac(
        network,
        parameters=LOADFLOW_PARAMETERS,
    )

    components = [
        {
            "status": component.status.name,
            "iteration_count": _optional_int(component, "iteration_count"),
            "distributed_active_power_mw": _optional_float(
                component, "distributed_active_power"
            ),
        }
        for component in result
    ]

    converged = bool(result) and all(
        component.status.name == "CONVERGED" for component in result
    )

    response: dict[str, Any] = {
        "converged": converged,
        "components": components,
    }

    if converged:
        buses = network.get_buses()
        voltage_levels = network.get_voltage_levels()
        bus_check = buses.join(
            voltage_levels[["nominal_v"]],
            on="voltage_level_id",
        )
        bus_check["v_pu"] = bus_check["v_mag"] / bus_check["nominal_v"]

        response["bus_voltage_pu"] = {
            "min": float(bus_check["v_pu"].min()),
            "max": float(bus_check["v_pu"].max()),
        }

    return response


def _optional_float(obj: Any, name: str) -> float | None:
    if not hasattr(obj, name):
        return None
    value = getattr(obj, name)
    if value is None:
        return None
    return float(value)


def _optional_int(obj: Any, name: str) -> int | None:
    if not hasattr(obj, name):
        return None
    value = getattr(obj, name)
    if value is None:
        return None
    return int(value)
