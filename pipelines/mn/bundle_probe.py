"""Isolated EMS-runtime probe using only explicit bundle paths (no activation)."""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys


def observe(root):
    from ai_ems import mongolia_security as ms, mongolia_sensitivity as sens
    from ai_ems.grid_catalog import load_mongolia_map
    from ai_ems.pf_preview import load_mongolia_pf_preview
    from ai_ems.reference_layers import load_mongolia_reference_layers
    from pipelines.mn.bundle import ASSETS, MODEL
    from pipelines.mn.probe import PARAMETERS

    load_mongolia_map(root/ASSETS["map"])
    load_mongolia_pf_preview(root/ASSETS["pf_preview"], root/ASSETS["map"])
    reference = load_mongolia_reference_layers(root/ASSETS["reference"], root/ASSETS["map"])
    files = ms.ModelFiles(root/MODEL, root/"reports", root/ASSETS["pf_preview"], root/ASSETS["map"])
    service = ms.MongoliaSecurityService(files)
    status = service.status()
    if not status["available"] or not status["sensitivity_available"]:
        raise ValueError(f"EMS model validation failed: {status}")
    params = ms.loadflow_parameters()
    numerical = {
        "status": status,
        "loadflow_parameters": {name: getattr(params, name).name if hasattr(getattr(params, name), "name")
                                else getattr(params, name) for name in PARAMETERS},
        "sensitivity_definition": {"fd_step_mw": sens.FD_STEP_MW, "fd_abs_tol": sens.FD_ABS_TOL,
                                   "fd_rel_tol": sens.FD_REL_TOL, "self_check": list(sens.SELF_CHECK)},
        "n1": [service.run_line_contingency(line) for line in ("222087807-1_0", "751261406-1_0", "484333456-1_0")],
        "sensitivity": [service.run_line_sensitivity("751261406-1_0", line, top_n=45) for line in
                        ("303374638-1_0", "484276912-1_0", "259474322-1_0")],
    }
    return numerical, {"lines": len(reference["lines"]), "substations": len(reference["substations"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--ems-root", type=Path, required=True)
    parser.add_argument("--creation-candidate", action="store_true", help="Private pre-publication observation, without claiming existing runtime evidence")
    args = parser.parse_args(argv)
    # This boundary only adds EMS-owned modules, never external build scripts.
    sys.path[:0] = [str(args.ems_root/"src"), str(args.ems_root)]
    try:
        from pipelines.mn.bundle import _inspect_bundle, read_json, require, validate_bundle
        from pipelines.mn.inventory import Checks, compare_numerical
        inspect = _inspect_bundle if args.creation_candidate else validate_bundle
        initial = inspect(args.bundle, expected_manifest_sha256=args.manifest_sha256)
        actual, reference_layers = observe(args.bundle)
        contract = read_json(args.bundle/"validation/runtime_contract.json")
        checks = Checks()
        compare_numerical(actual, contract["numerical_reference"], checks)
        require(len(checks.items) == 186 and checks.ready(),
                f"PF/N-1/Sensitivity numeric baseline mismatch: count={len(checks.items)}, failures="
                f"{[c for c in checks.items if c['passed'] is not True]}")
        require(sum(len(r["candidates"]) for r in actual["sensitivity"]) == 132, "Sensitivity candidate count mismatch")
        require(inspect(args.bundle, expected_manifest_sha256=args.manifest_sha256) == initial, "Bundle changed during probe")
        environment = {"python": sys.version.split()[0], "packages": {
            name: importlib.metadata.version(name) for name in ("pypowsybl", "pandas", "numpy", "networkx", "scipy")}}
        require(environment["packages"]["pypowsybl"] == "1.16.1", "Unreviewed runtime engine version")
        print(json.dumps({"runtime_compatible": True, "activated": False, "environment": environment,
                          "numerical_checks": checks.items, "numerical": actual, "reference_layers": reference_layers,
                          "pypsa_installed": importlib.util.find_spec("pypsa") is not None}, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"runtime_compatible": False, "activated": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
