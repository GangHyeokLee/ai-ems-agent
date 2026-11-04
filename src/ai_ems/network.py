from __future__ import annotations

from pathlib import Path
import hashlib
from tempfile import TemporaryDirectory
from typing import Any

import pypowsybl as pp

from .utils.kpg_powsybl_adapter import load_kpg_network
from .network_access import (
    access_for, guarded_network, identified_parameters, identified_provider,
)

IMPORT_PARAMETERS = {
    "matpower.import.ignore-base-voltage": "false",
}

LOADFLOW_PARAMETERS = pp.loadflow.Parameters(
    distributed_slack=False,
)
_SOURCE_ROOT = Path(__file__).parent
# Capture the code identity of this loaded process, not a later on-disk edit.
_CONVERSION_CODE = {
    name: hashlib.sha256((_SOURCE_ROOT / name).read_bytes()).hexdigest()
    for name in ("network.py", "utils/kpg_powsybl_adapter.py")
}


def load_network(case_path: str | Path):
    path = Path(case_path).expanduser().resolve()

    if not path.exists():
        raise FileNotFoundError(
            f"Network case file not found: {path}"
        )

    if path.name == "KPG193_ver2_0_powsybl_full.mat":
        return load_kpg_network(
            path,
            ignore_base_voltage=False,
        )

    return pp.network.load(
        path,
        parameters=IMPORT_PARAMETERS,
    )


def load_identified_network(case_path: str | Path):
    """Load the bytes we fingerprint, preserving basename-dependent conversion.

    The original path may subsequently change; it is never re-read to assert
    provenance of this object. The legacy/private loader remains unchanged.
    """
    path = Path(case_path).expanduser().resolve()
    source = path.read_bytes()
    full_kpg = path.name == "KPG193_ver2_0_powsybl_full.mat"
    importer = ({"matpower.import.ignore-base-voltage": "false"}
                if full_kpg else dict(IMPORT_PARAMETERS))
    with TemporaryDirectory(prefix="ai-ems-model-") as directory:
        frozen = Path(directory) / path.name
        frozen.write_bytes(source)
        network = load_network(frozen)
    provenance = {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "loader": "kpg-full-matpower-v1" if full_kpg else "pypowsybl-import-v1",
        "import_parameters": importer,
        "conversion_code": dict(_CONVERSION_CODE),
    }
    # Runtime importer reconfiguration during a general load is unsupported.
    if not full_kpg and importer != IMPORT_PARAMETERS:
        provenance = {}
    access_for(network).identify(provenance, LOADFLOW_PARAMETERS)
    return network


@guarded_network
def run_ac_load_flow(
        network,
        *,
        parameters: pp.loadflow.Parameters | None = None,
    ) -> dict[str, Any]:
    """Run AC load flow and return a JSON-serializable result summary."""
    effective_parameters = (
        (identified_parameters(network, "loadflow") or LOADFLOW_PARAMETERS)
        if parameters is None else parameters
    )
    if parameters is not None and isinstance(network, pp.network.Network):
        # The web line-analysis model binds its own exact defaults. An explicit
        # library override still executes unchanged, but cannot reuse that identity.
        access_for(network).invalidate_identity()

    result = pp.loadflow.run_ac(
        network,
        parameters=effective_parameters,
        **({"provider": identified_provider(network, "loadflow")}
           if identified_provider(network, "loadflow") else {}),
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
        "load_flow_parameters": {
            "distributed_slack": bool(
                effective_parameters.distributed_slack
            ),
            "balance_type": (
                effective_parameters.balance_type.name
                if effective_parameters.distributed_slack
                else None
            )
        }
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
