"""Read-only environment probes and baseline assembly. No installation or model construction."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

from .inventory import Checks, SCHEMA, collect, git_state, inspect_relationships, sha256
from .paths import active_runtime

PROBE = Path(__file__).with_name("probe.py")


def run_probe(python, mode, paths, timeout=120, model=None):
    command = [str(python), "-B", "-I", str(PROBE), mode, "--ems-root", str(paths.ems_root)]
    if mode == "numerical":
        command += ["--xiidm", str(paths.runtime_xiidm), "--reports", str(paths.runtime_reports)]
    if model is not None:
        command += ["--model", str(model)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        # Native dependencies may emit a notice before the probe's single JSON result.
        result = json.loads(done.stdout.splitlines()[-1])
        if not result.get("ok") or done.returncode:
            return {"ok": False, "error": result.get("error", f"probe exit {done.returncode}")}
        return result
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def test_results(paths):
    """Keep separate runs separate: never add duplicate tests across basic and browser runs."""
    runs = []
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise ValueError(f"잘못된 JUnit XML: {path}") from exc
        totals = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        modules, skips, failures = {}, [], []
        for case in root.iter("testcase"):
            module = case.get("classname", "unknown")
            name = module + "::" + case.get("name", "unknown")
            status = "passed"
            for tag, label in (("skipped", "skipped"), ("failure", "failed"), ("error", "errors")):
                node = case.find(tag)
                if node is not None:
                    status = label
                    detail = {"test": name, "reason": node.get("message", "")}
                    (skips if label == "skipped" else failures).append(detail)
                    break
            totals[status] += 1
            modules.setdefault(module, {k: 0 for k in totals})[status] += 1
        runs.append({"path": str(Path(path).resolve()), "sha256": sha256(path), "totals": totals,
                     "modules": dict(sorted(modules.items())), "skipped": sorted(skips, key=lambda r:r["test"]),
                     "failures": sorted(failures, key=lambda r:r["test"])})
    return runs


def environment(paths, checks, pixi=None):
    result = {}
    for name in ("runtime", "build", "conversion"):
        probe = run_probe(getattr(paths, name + "_python"), "versions", paths)
        result[name] = probe
        scopes = (name,) if name != "build" else ("build", "conversion")
        checks.equal("environment:" + name, probe["ok"], True, scopes)
        if not probe["ok"]:
            checks.error("environment_detail:" + name, probe["error"], scopes)
            continue
        packages = probe["result"]["packages"]
        required = {"pypowsybl": "1.16.1"} if name == "runtime" else {"pypsa": "1.2.4"}
        if name == "conversion":
            required["pypowsybl"] = "1.16.1"
        for package, version in required.items():
            checks.equal(f"version:{name}:{package}", packages.get(package), version, scopes)
        needed = ("pandas", "numpy", "networkx") if name == "runtime" else ("geopandas", "shapely", "pyproj", "scipy", "snakemake", "powerplantmatching")
        for package in needed:
            checks.equal(f"installed:{name}:{package}", packages.get(package) is not None, True, scopes)
    runtime = result["runtime"].get("result", {})
    for name in ("build", "conversion"):
        other = result[name].get("result", {})
        if runtime and other:
            checks.equal("separate_prefix:" + name, Path(runtime["prefix"]).resolve() != Path(other["prefix"]).resolve(), True, (name,))
    requirements = (paths.ems_root / "requirements.txt").read_text(encoding="utf-8")
    if "networkx" not in requirements.lower():
        checks.warning("runtime_dependency_declaration", "networkx는 현재 설치되어 있으나 requirements.txt에 직접 선언되지 않았습니다. 이번 단계에서는 런타임 의존성을 변경하지 않습니다.")
    selected_pixi = pixi or os.environ.get("KEMS_MN_PIXI") or shutil.which("pixi")
    try:
        if not selected_pixi:
            raise ValueError("Pixi 실행 파일 없음 (--pixi 또는 KEMS_MN_PIXI)")
        done = subprocess.run([str(selected_pixi), "--version"], capture_output=True, text=True, timeout=15)
        if done.returncode:
            raise ValueError("pixi --version 실패")
        result["pixi"] = {"path": str(selected_pixi), "version": done.stdout.strip()}
        checks.equal("tool:pixi", bool(done.stdout.strip()), True, ("build", "conversion"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        checks.error("tool:pixi", exc, ("build", "conversion"))
    return result


def assemble(paths, contract, *, pixi=None, verify_runtime=False, junit=(), profile="all"):
    checks = Checks()
    config = run_probe(paths.runtime_python, "configuration", paths)
    checks.equal("runtime_path_discovery", config["ok"], True)
    if config["ok"]:
        paths = active_runtime(paths, config["result"])
    elif paths.runtime_xiidm is None or paths.runtime_reports is None:
        if profile in ("build", "conversion"):
            # Build-only inspection can work without an EMS venv. These are explicitly
            # labelled source selections, never claimed to be active runtime settings.
            specs = {item["role"]: item for item in contract["files"]}
            paths = active_runtime(paths, {
                "xiidm": str(paths.earth_checkout / specs["xiidm"]["path"]),
                "reports": str(paths.source_project / Path(specs["conversion.element_mapping.csv"]["path"]).parent),
            })
            checks.warning("build_only_selection", "EMS 런타임 설정을 읽지 못했습니다. 구축 원본만 점검하며 활성 런타임을 확인한 것으로 간주하지 않습니다.")
        else:
            checks.error("runtime_path_discovery_detail", config["error"])
            return paths, {"schema": SCHEMA, "paths": paths.report(), "checks": checks.items, "files": {}, "ready": False}
    report = {"schema": SCHEMA, "baseline_id": contract["baseline_id"], "contract_sha256": sha256(Path(__file__).with_name("baseline_contract.json")),
              "paths": paths.report(), "active_runtime_configuration": config, "repositories": {}}
    for name, root in (("ems", paths.ems_root), ("source", paths.source_project), ("earth", paths.earth_checkout)):
        try:
            state = git_state(root)
            report["repositories"][name] = state
            if name in contract["repositories"]:
                checks.equal("git_commit:" + name, state["head"], contract["repositories"][name], ("build", "conversion"))
                checks.equal("git_tracked_unchanged:" + name, state["tracked_changes"], "", ("build", "conversion"))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            checks.error("git:" + name, exc, ("build", "conversion"))
    report["files"] = collect(paths, contract, checks)
    try:
        report["facts"] = inspect_relationships(report["files"], contract, checks)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        checks.error("relationship_schema", f"{type(exc).__name__}: {exc}")
    report["environment"] = environment(paths, checks, pixi)
    report["netcdf_models"] = {}
    for role in ("legacy_model", "map_model", "passive_model", "initial_pf", "pf_model"):
        model_path = Path(report["files"][role]["path"])
        inspector = paths.build_python if report["environment"]["build"]["ok"] else paths.runtime_python
        probe = run_probe(inspector, "netcdf", paths, model=model_path)
        report["netcdf_models"][role] = probe
        checks.equal("netcdf_readable:" + role, probe["ok"], True, ("build", "conversion"))
        if probe["ok"]:
            if role in ("map_model", "passive_model"):
                expected = {**{k:v for k,v in contract["map_counts"].items() if k != "PlantRecord"},
                            "Generator": 0, "Load": 0, "Shunt": 0}
            elif role == "legacy_model":
                expected = dict(Bus=110, Line=111, Transformer=10, Generator=0, Load=0, Shunt=0)
            else:
                expected = dict(contract["analysis_counts"])
                if role == "initial_pf":
                    expected.update(Generator=108, Shunt=0)
            checks.equal("netcdf_inventory:" + role, probe["result"]["counts"], expected, ("build", "conversion"))
    report["control_contract"] = {key: contract[key] for key in ("scenario_id", "slack_bus", "loadflow_parameters", "sensitivity_definition")}
    report["planned_destinations"] = {}
    for name in ("work_dir", "output_dir", "bundle_dir"):
        path = getattr(paths, name)
        ancestor = path
        while not ancestor.exists():
            ancestor = ancestor.parent
        writable = ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK)
        checks.equal("destination_writable:" + name, writable, True, ("build", "conversion"))
        report["planned_destinations"][name] = {"exists": path.exists(), "active": False,
                                                "action": "inspection_only; no directory created, no runtime switch"}
    report["numerical"] = {"executed": False}
    if verify_runtime:
        if checks.ready("runtime"):
            probe = run_probe(paths.runtime_python, "numerical", paths)
            report["numerical"] = {"executed": True, **probe}
            checks.equal("existing_runtime_verification", probe["ok"], True)
            if probe["ok"]:
                value = probe["result"]
                checks.equal("loadflow_control_contract", value["loadflow_parameters"], contract["loadflow_parameters"])
                # JSON-normalize tuples from the numerical code before comparing with the frozen contract.
                checks.equal("sensitivity_control_contract", value["sensitivity_definition"], contract["sensitivity_definition"])
                checks.equal("n1_all_converged", all(r["post_status"] == "CONVERGED" for r in value["n1"]), True)
                checks.equal("sensitivity_all_completed", all(r["analysis_status"] == "COMPLETED" for r in value["sensitivity"]), True)
            else:
                checks.error("existing_runtime_verification_detail", probe["error"])
        else:
            checks.error("existing_runtime_verification", "정적 런타임 검사가 실패하여 모델을 실행하지 않았습니다.")
    report["test_runs"] = test_results(junit)
    for index, run in enumerate(report["test_runs"]):
        checks.equal(f"test_run:{index}:failures_and_errors", run["totals"]["failed"] + run["totals"]["errors"], 0, ("baseline",))
    report["checks"] = checks.items
    report["readiness"] = {name: checks.ready(name) for name in ("runtime", "build", "conversion")}
    report["ready"] = checks.ready()
    report["stage_capabilities"] = {"inspect": True, "build_models": False, "convert_models": False, "activate_bundle": False}
    return paths, report
