"""Read-only probes executed in a selected interpreter, never in a merged environment.

Version mode uses distribution metadata, so GIS/PyPSA imports cannot create external caches.
Numerical mode calls the existing EMS algorithms without saving a network.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

PACKAGES = ("pypsa", "pypowsybl", "pandas", "numpy", "networkx", "snakemake",
            "powerplantmatching", "geopandas", "shapely", "pyproj", "scipy", "h5py", "pytest", "osmium")
PARAMETERS = ("voltage_init_mode", "transformer_voltage_control_on", "use_reactive_limits",
              "phase_shifter_regulation_on", "twt_split_shunt_admittance",
              "shunt_compensator_voltage_control_on", "read_slack_bus", "write_slack_bus",
              "distributed_slack", "component_mode", "provider_parameters")


def versions():
    packages = {}
    for name in PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version.split()[0], "prefix": sys.prefix,
            "base_prefix": sys.base_prefix, "packages": packages}


def configuration(root):
    spec = importlib.util.spec_from_file_location("mn_inspected_config", root / "src/ai_ems/config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # No dotenv values other than these two non-secret file selectors leave the subprocess.
    return {"xiidm": str(module.MONGOLIA_XIIDM_FILE.resolve()),
            "reports": str(module.MONGOLIA_POWSYBL_REPORTS_DIR.resolve())}


def netcdf(path):
    names = {"buses": "Bus", "lines": "Line", "transformers": "Transformer",
             "generators": "Generator", "loads": "Load", "shunt_impedances": "Shunt"}
    with path.open("rb") as stream:
        signature = stream.read(4)
    if signature.startswith(b"CDF"):
        from scipy.io import netcdf_file
        with netcdf_file(path, "r", mmap=False) as dataset:
            counts = {label: len(dataset.variables[name + "_i"].data) if name + "_i" in dataset.variables else 0
                      for name, label in names.items()}
        kind = "NetCDF3"
    else:
        try:
            import h5py
        except ImportError:
            from netCDF4 import Dataset
            with Dataset(path, "r") as dataset:
                counts = {label: len(dataset.variables[name + "_i"]) if name + "_i" in dataset.variables else 0
                          for name, label in names.items()}
        else:
            with h5py.File(path, "r") as dataset:
                counts = {label: len(dataset[name + "_i"]) if name + "_i" in dataset else 0
                          for name, label in names.items()}
        kind = "HDF5/NetCDF4"
    return {"format": kind, "counts": counts}


def numerical(root, xiidm, reports):
    # Existing security helpers also import studies/ from the EMS source root.
    sys.path[:0] = [str(root / "src"), str(root)]
    from ai_ems import mongolia_security as ms, mongolia_sensitivity as sens

    files = ms.ModelFiles(xiidm, reports, root / "data/grids/mn35k-synthetic-pf-preview.json",
                          root / "data/grids/mn-osm-35k-map.json")
    service = ms.MongoliaSecurityService(files)
    status = service.status()
    if not status["available"] or not status["sensitivity_available"]:
        raise ValueError(f"기존 런타임 검증 실패: {status}")
    params = ms.loadflow_parameters()
    parameters = {name: getattr(params, name).name if hasattr(getattr(params, name), "name")
                  else getattr(params, name) for name in PARAMETERS}
    return {
        "runtime_files": {"xiidm": str(xiidm), "reports": str(reports)},
        "status": status, "loadflow_parameters": parameters,
        "sensitivity_definition": {"fd_step_mw": sens.FD_STEP_MW, "fd_abs_tol": sens.FD_ABS_TOL,
                                   "fd_rel_tol": sens.FD_REL_TOL, "self_check": sens.SELF_CHECK},
        "n1": [service.run_line_contingency(line) for line in
               ("222087807-1_0", "751261406-1_0", "484333456-1_0")],
        "sensitivity": [service.run_line_sensitivity("751261406-1_0", line, top_n=45) for line in
                        ("303374638-1_0", "484276912-1_0", "259474322-1_0")],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("versions", "configuration", "numerical", "netcdf"))
    parser.add_argument("--ems-root", type=Path)
    parser.add_argument("--xiidm", type=Path)
    parser.add_argument("--reports", type=Path)
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "versions":
            value = versions()
        elif args.mode == "configuration":
            value = configuration(args.ems_root)
        elif args.mode == "netcdf":
            value = netcdf(args.model)
        else:
            value = numerical(args.ems_root, args.xiidm, args.reports)
        print(json.dumps({"ok": True, "result": value}, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
