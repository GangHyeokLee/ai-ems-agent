"""Mongolia MN_35k synthetic PyPowSyBl model: loading, validation and line N-1 Security Analysis.

Completely separate from the KPG-193 network and its ``LOADFLOW_PARAMETERS``.
The XIIDM and the ID mapping are produced (read-only for us) by
``mongolia-grid-poc/scripts/convert_mn35k_to_powsybl.py``; PyPSA is not needed here.

Model identity is never judged by the XIIDM file hash (``caseDate`` changes on every
save). Instead the model must match: the validated synthetic PF preview (scenario,
source NetCDF SHA-256), element counts, the original-ID mapping and connectivity,
key electrical settings, and the base AC PF results within the conversion tolerances.

Every analysis loads an independent network object from the file, so requests never
share or accumulate contingency state, and the KPG network is never touched.
"""

from __future__ import annotations

import csv
import json
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import pypowsybl as pp

from ai_ems.pf_preview import EXPECTED_SCENARIO_ID, load_mongolia_pf_preview
from ai_ems.tools.security_tools import _serialize_violation, _summarize_violations

GRID_ID = "mn-osm-35k"
SLACK_BUS_ID = "25"
SLACK_VOLTAGE_LEVEL_ID = "VL_25"
SLACK_GENERATOR_ID = "SYNTHETIC_SLACK_25"
EXPECTED_COUNTS = {"Bus": 166, "Line": 156, "Transformer": 27, "Generator": 45, "Load": 166, "Shunt": 1}

# Same values as mongolia-grid-poc configs/mn35k_powsybl_conversion.json (pre-registered).
TOLERANCES = {
    "v_mag_pu": 1e-5,
    "v_ang_deg": 1e-4,
    "branch_mw_mvar": 1e-3,
    "generator_mw_mvar": 1e-3,
    "shunt_mvar": 1e-3,
}

MODEL_CAVEATS = [
    "합성 시험 시나리오(reduced_27_q_limited_with_reactor)이며 실제 몽골 운전계통·측정값이 아닙니다.",
    "합성 부하 25.6MW, 합성 발전기 45대, 합성 리액터 20MVAr를 가정했습니다.",
    "선로·변압기 전기정수와 일부 길이·회선수는 실설비로 검증되지 않았고 변압기 27대는 추정 설비입니다.",
    "병렬 회선은 등가 선로 1개로 표현되어 회선 단위 사고는 계산할 수 없습니다.",
    "계통 분리 시 주 계통(MAIN_CONNECTED)만 계산하며, 분리된 섬의 전압·조류는 계산하지 않습니다.",
    "분리 부하는 합성 부하의 공급 상실 가능량이며 실제 정전 규모가 아닙니다.",
    "스크리닝 기준(전압 0.9~1.1pu, 열적 s_nom, 발전기 Q ±100MVAr)은 합성 가정이며 운영 기준이 아닙니다.",
]


class MongoliaModelUnavailable(RuntimeError):
    """The Mongolia PyPowSyBl model is missing or failed validation."""


class UnknownOutageLine(ValueError):
    pass


class ExcludedOutageLine(ValueError):
    pass


def loadflow_parameters() -> pp.loadflow.Parameters:
    """OpenLoadFlow settings validated against PyPSA in mongolia-grid-poc (mn35k_powsybl.py)."""
    lf = pp.loadflow
    return lf.Parameters(
        voltage_init_mode=lf.VoltageInitMode.UNIFORM_VALUES,
        transformer_voltage_control_on=False,
        use_reactive_limits=False,
        phase_shifter_regulation_on=False,
        twt_split_shunt_admittance=False,
        shunt_compensator_voltage_control_on=False,
        read_slack_bus=False,
        write_slack_bus=False,
        distributed_slack=False,
        component_mode=lf.ComponentMode.MAIN_CONNECTED,
        provider_parameters={
            "slackBusSelectionMode": "NAME",
            "slackBusesIds": SLACK_VOLTAGE_LEVEL_ID,
            "referenceBusSelectionMode": "FIRST_SLACK",
            "newtonRaphsonConvEpsPerEq": "1e-08",
            "newtonRaphsonStoppingCriteriaType": "UNIFORM_CRITERIA",
            "maxNewtonRaphsonIterations": "30",
        },
    )


@dataclass
class ModelFiles:
    xiidm: Path
    reports_dir: Path
    pf_preview: Path
    map_file: Path

    @property
    def mapping(self) -> Path:
        return self.reports_dir / "element_mapping.csv"

    @property
    def generator_reference(self) -> Path:
        return self.reports_dir / "pf_generator_comparison.csv"

    @property
    def conversion_validation(self) -> Path:
        return self.reports_dir / "conversion_validation.json"

    @property
    def input_hashes(self) -> Path:
        return self.reports_dir / "input_hashes.json"

    def signature(self) -> tuple:
        out = []
        for path in (self.xiidm, self.mapping, self.generator_reference, self.conversion_validation,
                     self.input_hashes, self.pf_preview, self.map_file):
            try:
                stat = path.stat()
                out.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                out.append((str(path), None, None))
        return tuple(out)


@dataclass
class ValidatedModel:
    files: ModelFiles
    preview: dict[str, Any]
    mapping: dict[str, dict[str, dict[str, str]]]   # component -> original_id -> row
    iidm_to_original: dict[str, tuple[str, str]]     # iidm_id (and VL id) -> (component, original_id)
    generator_reference: dict[str, dict[str, float]]
    base_check: dict[str, Any] = field(default_factory=dict)

    @property
    def scenario_id(self) -> str:
        return self.preview["metadata"]["scenario_id"]

    @property
    def excluded_line_ids(self) -> set[str]:
        return {str(item) for item in self.preview["metadata"]["excluded_line_ids"]}


def _read_mapping(path: Path) -> tuple[dict, dict]:
    if not path.is_file():
        raise MongoliaModelUnavailable(f"ID 대응표가 없습니다: {path}")
    mapping: dict[str, dict[str, dict[str, str]]] = {}
    back: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            component, original, iidm = row.get("component"), row.get("original_id"), row.get("iidm_id")
            if not component or not original or not iidm:
                raise MongoliaModelUnavailable("ID 대응표에 빈 값이 있습니다.")
            if original in mapping.setdefault(component, {}) or iidm in back:
                raise MongoliaModelUnavailable(f"ID 대응표가 중복됩니다: {component} {original}")
            mapping[component][original] = row
            back[iidm] = (component, original)
            if component == "Bus":
                back[row["iidm_voltage_level_id"]] = ("Bus", original)
    counts = {k: len(v) for k, v in mapping.items()}
    if counts != EXPECTED_COUNTS:
        raise MongoliaModelUnavailable(f"ID 대응표 설비 수가 다릅니다: {counts}")
    return mapping, back


def _read_generator_reference(path: Path) -> dict[str, dict[str, float]]:
    if not path.is_file():
        raise MongoliaModelUnavailable(f"발전기 PF 기준값이 없습니다: {path}")
    reference = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            p, q = float(row["p_mw_pypsa"]), float(row["q_mvar_pypsa"])
            if not (math.isfinite(p) and math.isfinite(q)):
                raise MongoliaModelUnavailable("발전기 PF 기준값에 유한하지 않은 값이 있습니다.")
            reference[row["generator_id"]] = {"p_mw": p, "q_mvar": q}
    return reference


def _check_provenance(files: ModelFiles, preview: dict[str, Any]) -> dict[str, Any]:
    try:
        validation = json.loads(files.conversion_validation.read_text(encoding="utf-8"))
        hashes = json.loads(files.input_hashes.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MongoliaModelUnavailable(f"변환 검증 기록을 읽을 수 없습니다: {exc}") from exc
    scenario = validation.get("input_scenario", {})
    source_hashes = set(hashes.get("input_model", {}).values())
    result = {
        "conversion_scenario_id": scenario.get("meta_scenario_id"),
        "conversion_input_verified": scenario.get("passed") is True,
        "preview_scenario_id": preview["metadata"]["scenario_id"],
        "source_sha256": preview["metadata"]["source_sha256"],
        "source_sha256_matches_conversion_input": preview["metadata"]["source_sha256"] in source_hashes,
    }
    if not (result["conversion_input_verified"]
            and result["conversion_scenario_id"] == EXPECTED_SCENARIO_ID == result["preview_scenario_id"]
            and result["source_sha256_matches_conversion_input"]):
        raise MongoliaModelUnavailable(f"변환 모델의 시나리오 출처가 검증된 PF와 일치하지 않습니다: {result}")
    return result


def _check_structure(net, model: ValidatedModel) -> dict[str, Any]:
    inventory = {
        "Bus": len(net.get_bus_breaker_view_buses()),
        "Line": len(net.get_lines()),
        "Transformer": len(net.get_2_windings_transformers()),
        "Generator": len(net.get_generators()),
        "Load": len(net.get_loads()),
        "Shunt": len(net.get_shunt_compensators()),
    }
    if inventory != EXPECTED_COUNTS:
        raise MongoliaModelUnavailable(f"XIIDM 설비 수가 다릅니다: {inventory}")
    getters = {
        "Bus": net.get_bus_breaker_view_buses, "Line": net.get_lines,
        "Transformer": net.get_2_windings_transformers, "Generator": net.get_generators,
        "Load": net.get_loads, "Shunt": net.get_shunt_compensators,
    }
    for component, rows in model.mapping.items():
        frame = getters[component]()
        iidm_ids = {row["iidm_id"] for row in rows.values()}
        if set(frame.index) != iidm_ids:
            raise MongoliaModelUnavailable(f"XIIDM {component} ID가 대응표와 다릅니다.")
    lines = net.get_lines()
    for original, row in model.mapping["Line"].items():
        iidm = lines.loc[row["iidm_id"]]
        if iidm["name"] != original or (iidm.voltage_level1_id, iidm.voltage_level2_id) != (
            "VL_" + row["original_bus0"], "VL_" + row["original_bus1"]
        ):
            raise MongoliaModelUnavailable(f"선로 {original}의 원본 ID 또는 연결 모선이 다릅니다.")
    trafos = net.get_2_windings_transformers()
    for original, row in model.mapping["Transformer"].items():
        iidm = trafos.loc[row["iidm_id"]]
        if iidm["name"] != original or (iidm.voltage_level1_id, iidm.voltage_level2_id) != (
            "VL_" + row["original_bus0"], "VL_" + row["original_bus1"]
        ):
            raise MongoliaModelUnavailable(f"변압기 {original}의 원본 ID 또는 연결 모선이 다릅니다.")
    preview = model.preview
    expected_sets = {
        "Bus": {str(b["bus_id"]) for b in preview["buses"]},
        "Line": {str(l["line_id"]) for l in preview["lines"]},
        "Transformer": {str(t["transformer_id"]) for t in preview["transformers"]},
    }
    for component, ids in expected_sets.items():
        if set(model.mapping[component]) != ids:
            raise MongoliaModelUnavailable(f"XIIDM {component} 집합이 검증된 PF 해석 대상과 다릅니다.")
    gens = net.get_generators()
    slack = gens.loc["G_" + SLACK_GENERATOR_ID]
    pq = gens.loc["G_SYNTHETIC_PV_10"]
    shunt = net.get_linear_shunt_compensator_sections().loc["SH_SYNTHETIC_SHUNT_REACTOR_25"]
    settings = {
        "slack_generator_voltage_level": slack.voltage_level_id,
        "voltage_regulating_generators": int(gens.voltage_regulator_on.sum()),
        "pq_generator_target_q_mvar": float(pq.target_q),
        "reactor_b_per_section_s": float(shunt.b_per_section),
        "load_p_total_mw": float(net.get_loads().p0.sum()),
        "load_q_total_mvar": float(net.get_loads().q0.sum()),
    }
    if (settings["slack_generator_voltage_level"] != SLACK_VOLTAGE_LEVEL_ID
            or settings["voltage_regulating_generators"] != 44
            or bool(pq.voltage_regulator_on) or settings["pq_generator_target_q_mvar"] != -100.0
            or not math.isclose(settings["reactor_b_per_section_s"], -20.0 / 220.0**2, rel_tol=1e-12)
            or not math.isclose(settings["load_p_total_mw"], preview["metadata"]["load_mw"], abs_tol=1e-8)
            or not math.isclose(settings["load_q_total_mvar"], preview["metadata"]["load_mvar"], abs_tol=1e-8)):
        raise MongoliaModelUnavailable(f"XIIDM 주요 설정이 검증된 시나리오와 다릅니다: {settings}")
    return {"inventory": inventory, "settings": settings}


def _run_base_lf(net) -> dict[str, Any]:
    results = pp.loadflow.run_ac(net, parameters=loadflow_parameters())
    if not results:
        raise MongoliaModelUnavailable("기본 AC PF 결과가 없습니다.")
    main = results[0]
    slack = list(main.slack_bus_results or [])
    info = {
        "status": main.status.name,
        "iteration_count": int(main.iteration_count),
        "reference_bus_id": str(main.reference_bus_id),
        "slack_bus_ids": [s.id for s in slack],
        "slack_active_power_mismatch_mw": float(sum(s.active_power_mismatch for s in slack)),
    }
    if info["status"] != "CONVERGED":
        raise MongoliaModelUnavailable(f"기본 AC PF가 수렴하지 않았습니다: {info}")
    prefix = SLACK_VOLTAGE_LEVEL_ID + "_"
    if not info["reference_bus_id"].startswith(prefix) or not all(i.startswith(prefix) for i in info["slack_bus_ids"]):
        raise MongoliaModelUnavailable(f"Slack이 모선 {SLACK_BUS_ID}에 배치되지 않았습니다: {info}")
    return info


def _bus_voltages(net, model: ValidatedModel) -> dict[str, tuple[float, float]]:
    """Original bus ID -> (v pu, angle deg) for energized buses (configured bus IDs)."""
    view = net.get_bus_breaker_view_buses()
    calc = net.get_buses()
    nominal = net.get_voltage_levels().nominal_v
    out = {}
    for original, row in model.mapping["Bus"].items():
        calc_id = view.at[row["iidm_id"], "bus_id"]
        if not calc_id or calc_id not in calc.index:
            continue
        v = float(calc.at[calc_id, "v_mag"])
        a = float(calc.at[calc_id, "v_angle"])
        if math.isfinite(v) and math.isfinite(a):
            out[original] = (v / float(nominal[row["iidm_voltage_level_id"]]), a)
    return out


def _branch_flows(frame, rows: dict[str, dict[str, str]]) -> dict[str, tuple[float, float, float, float]]:
    out = {}
    for original, row in rows.items():
        r = frame.loc[row["iidm_id"]]
        out[original] = (float(r.p1), float(r.q1), float(r.p2), float(r.q2))
    return out


def _compare_base(net, model: ValidatedModel, lf: dict[str, Any]) -> dict[str, Any]:
    preview = model.preview
    voltages = _bus_voltages(net, model)
    slack_angle = voltages[SLACK_BUS_ID][1]
    errors = {"v_mag_pu": 0.0, "v_ang_deg": 0.0, "line_mw_mvar": 0.0, "transformer_mw_mvar": 0.0,
              "generator_mw_mvar": 0.0, "shunt_mvar": 0.0}
    for bus in preview["buses"]:
        got = voltages.get(str(bus["bus_id"]))
        if got is None:
            raise MongoliaModelUnavailable(f"기본 PF에서 모선 {bus['bus_id']}의 전압이 없습니다.")
        errors["v_mag_pu"] = max(errors["v_mag_pu"], abs(got[0] - float(bus["v_mag_pu"])))
        errors["v_ang_deg"] = max(errors["v_ang_deg"], abs((got[1] - slack_angle) - float(bus["v_ang_deg"])))
    for key, frame, rows, items in (
        ("line_mw_mvar", net.get_lines(), model.mapping["Line"], preview["lines"]),
        ("transformer_mw_mvar", net.get_2_windings_transformers(), model.mapping["Transformer"], preview["transformers"]),
    ):
        flows = _branch_flows(frame, rows)
        id_key = "line_id" if key.startswith("line") else "transformer_id"
        for item in items:
            got = flows[str(item[id_key])]
            ref = (item["p0_mw"], item["q0_mvar"], item["p1_mw"], item["q1_mvar"])
            errors[key] = max(errors[key], *(abs(a - float(b)) for a, b in zip(got, ref)))
    gens = net.get_generators()
    for original, ref in model.generator_reference.items():
        row = gens.loc["G_" + original]
        p = -float(row.p) + (lf["slack_active_power_mismatch_mw"] if original == SLACK_GENERATOR_ID else 0.0)
        q = -float(row.q)
        errors["generator_mw_mvar"] = max(errors["generator_mw_mvar"], abs(p - ref["p_mw"]), abs(q - ref["q_mvar"]))
    shunt_q = float(net.get_shunt_compensators().loc["SH_SYNTHETIC_SHUNT_REACTOR_25", "q"])
    reactor_ref = -float(preview["metadata"]["reactor_actual_q_mvar"])
    errors["shunt_mvar"] = abs(shunt_q - reactor_ref)
    limits = {"v_mag_pu": TOLERANCES["v_mag_pu"], "v_ang_deg": TOLERANCES["v_ang_deg"],
              "line_mw_mvar": TOLERANCES["branch_mw_mvar"], "transformer_mw_mvar": TOLERANCES["branch_mw_mvar"],
              "generator_mw_mvar": TOLERANCES["generator_mw_mvar"], "shunt_mvar": TOLERANCES["shunt_mvar"]}
    failures = {k: v for k, v in errors.items() if not v <= limits[k]}
    result = {"max_abs_error": errors, "tolerances": limits,
              "slack_p_mw": -float(gens.loc["G_" + SLACK_GENERATOR_ID, "p"]) + lf["slack_active_power_mismatch_mw"],
              "slack_q_mvar": -float(gens.loc["G_" + SLACK_GENERATOR_ID, "q"]),
              "reactor_q_absorbed_mvar": shunt_q, "within_tolerance": not failures}
    if failures:
        raise MongoliaModelUnavailable(f"기본 PF가 검증 기준(PyPSA)과 허용오차를 벗어났습니다: {failures}")
    return result


def load_validated_model(files: ModelFiles) -> tuple[ValidatedModel, Any]:
    """Validate everything and return (model metadata, freshly loaded base-solved network)."""
    if not files.xiidm.is_file():
        raise MongoliaModelUnavailable(
            f"몽골 PyPowSyBl 모델(XIIDM)이 없습니다: {files.xiidm}. "
            "mongolia-grid-poc에서 scripts/convert_mn35k_to_powsybl.py로 재생성하세요."
        )
    try:
        preview = load_mongolia_pf_preview(files.pf_preview, files.map_file)
    except (FileNotFoundError, ValueError) as exc:
        raise MongoliaModelUnavailable(f"검증 기준 합성 PF를 사용할 수 없습니다: {exc}") from exc
    provenance = _check_provenance(files, preview)
    mapping, back = _read_mapping(files.mapping)
    model = ValidatedModel(files, preview, mapping, back, _read_generator_reference(files.generator_reference))
    if set(model.generator_reference) != set(mapping["Generator"]):
        raise MongoliaModelUnavailable("발전기 PF 기준값의 발전기 ID가 대응표와 다릅니다.")
    net = _load_network(files.xiidm)
    structure = _check_structure(net, model)
    lf = _run_base_lf(net)
    comparison = _compare_base(net, model, lf)
    model.base_check = {"provenance": provenance, "structure": structure, "loadflow": lf, "comparison": comparison}
    return model, net


def _load_network(path: Path):
    try:
        return pp.network.load(str(path))
    except Exception as exc:  # noqa: BLE001 - surface any IIDM parse error as unavailability
        raise MongoliaModelUnavailable(f"XIIDM을 불러올 수 없습니다: {exc}") from exc


class MongoliaSecurityService:
    """Caches the validation verdict (by file signature) and runs isolated per-request analyses."""

    def __init__(self, files: ModelFiles):
        self.files = files
        self._lock = threading.Lock()
        self._signature = None
        self._model: ValidatedModel | None = None
        self._error: str | None = None
        self._sensitivity: dict[str, Any] = {"passed": False, "reason": "모델 검증 전"}

    def status(self) -> dict[str, Any]:
        model, error = self._ensure()
        if model is None:
            return {"available": False, "reason": error, "sensitivity_available": False,
                    "sensitivity_reason": error}
        return {"available": True, "reason": None, "scenario_id": model.scenario_id,
                "base_check": model.base_check,
                "sensitivity_available": self._sensitivity["passed"] is True,
                "sensitivity_reason": self._sensitivity.get("reason"),
                "sensitivity_self_check": self._sensitivity}

    def _ensure(self) -> tuple[ValidatedModel | None, str | None]:
        signature = self.files.signature()
        with self._lock:
            if signature != self._signature:
                try:
                    self._model, net = load_validated_model(self.files)
                    self._error = None
                    from ai_ems.mongolia_sensitivity import run_self_check

                    self._sensitivity = run_self_check(net, self._model)
                except MongoliaModelUnavailable as exc:
                    self._model, self._error = None, str(exc)
                    self._sensitivity = {"passed": False, "reason": str(exc)}
                self._signature = signature
            return self._model, self._error

    def _validated_outage(self, outage_line_id: str) -> tuple[ValidatedModel, str]:
        model, error = self._ensure()
        if model is None:
            raise MongoliaModelUnavailable(error or "몽골 모델을 사용할 수 없습니다.")
        line_id = outage_line_id.strip()
        if line_id in model.excluded_line_ids:
            raise ExcludedOutageLine(f"선로 {line_id}은(는) 기준 PF 해석 대상에서 제외된 선로입니다.")
        if line_id not in model.mapping["Line"]:
            raise UnknownOutageLine(f"몽골 PF 해석 대상에 없는 선로 ID입니다: {line_id}")
        return model, line_id

    def run_line_sensitivity(self, outage_line_id: str, monitored_line_id: str | None = None,
                             top_n: int = 10) -> dict[str, Any]:
        from ai_ems.mongolia_sensitivity import SensitivityVerificationFailed, analyze_line_sensitivity

        model, line_id = self._validated_outage(outage_line_id)
        if self._sensitivity.get("passed") is not True:
            raise SensitivityVerificationFailed(self._sensitivity.get("reason") or "Sensitivity 자체 검증 미통과")
        net = _load_network(model.files.xiidm)
        _check_structure(net, model)
        lf = _run_base_lf(net)
        _compare_base(net, model, lf)
        return analyze_line_sensitivity(net, model, line_id, monitored_line_id, top_n=top_n)

    def run_line_contingency(self, outage_line_id: str) -> dict[str, Any]:
        model, error = self._ensure()
        if model is None:
            raise MongoliaModelUnavailable(error or "몽골 모델을 사용할 수 없습니다.")
        line_id = outage_line_id.strip()
        if line_id in model.excluded_line_ids:
            raise ExcludedOutageLine(f"선로 {line_id}은(는) 기준 PF 해석 대상에서 제외된 선로입니다.")
        if line_id not in model.mapping["Line"]:
            raise UnknownOutageLine(f"몽골 PF 해석 대상에 없는 선로 ID입니다: {line_id}")
        # Independent network per request: re-validates the base state on the same file.
        net = _load_network(model.files.xiidm)
        _check_structure(net, model)
        lf = _run_base_lf(net)
        _compare_base(net, model, lf)
        return analyze_line_outage(net, model, line_id, lf)


def topology_islands(net, outage_iidm_line_id: str, model: ValidatedModel) -> list[str]:
    """Buses (original IDs) disconnected from the slack after removing one line, from topology only."""
    graph = nx.MultiGraph()
    graph.add_nodes_from(model.mapping["Bus"])
    for frame, component in ((net.get_lines(), "Line"), (net.get_2_windings_transformers(), "Transformer")):
        for iidm_id, row in frame.iterrows():
            if iidm_id == outage_iidm_line_id or not (row.connected1 and row.connected2):
                continue
            graph.add_edge(row.voltage_level1_id[3:], row.voltage_level2_id[3:], key=iidm_id)
    main = nx.node_connected_component(graph, SLACK_BUS_ID)
    return sorted((b for b in graph.nodes if b not in main), key=_bus_sort_key)


def _bus_sort_key(bus: str):
    return (0, int(bus), bus) if bus.isdigit() else (1, 0, bus)


def _loading(p1, q1, p2, q2, rating):
    return 100.0 * max(math.hypot(p1, q1), math.hypot(p2, q2)) / rating


def analyze_line_outage(net, model: ValidatedModel, line_id: str, base_lf: dict[str, Any]) -> dict[str, Any]:
    line_row = model.mapping["Line"][line_id]
    iidm_line = line_row["iidm_id"]
    contingency_id = f"OUT_{line_id}"
    base_v = _bus_voltages(net, model)
    base_lines = _branch_flows(net.get_lines(), model.mapping["Line"])
    ratings = net.get_loading_limits().reset_index()
    ratings = ratings.loc[ratings.acceptable_duration == -1].groupby("element_id").value.min().to_dict()
    vls = net.get_voltage_levels()
    screening = {
        "voltage_pu": [float((vls.low_voltage_limit / vls.nominal_v).min()), float((vls.high_voltage_limit / vls.nominal_v).max())],
        "thermal": "permanent APPARENT_POWER limit = s_nom (MVA)",
        "generator_q_mvar": [float(net.get_generators().min_q.min()), float(net.get_generators().max_q.max())],
        "synthetic_assumption": True,
    }

    analysis = pp.security.create_analysis()
    analysis.add_single_element_contingency(iidm_line, contingency_id)
    analysis.add_monitored_elements(
        voltage_level_ids=list(vls.index),
        branch_ids=list(net.get_lines().index) + list(net.get_2_windings_transformers().index),
    )
    sa = analysis.run_ac(net, parameters=loadflow_parameters())
    pre = sa.pre_contingency_result
    try:
        post = sa.find_post_contingency_result(contingency_id)
    except KeyError:
        post = None   # OLF gives no post-contingency result when the SA base state fails
    post_status = post.status.name if post is not None else "NO_RESULT"
    converged = pre.status.name == "CONVERGED" and post_status == "CONVERGED"
    raw_violations = [_serialize_violation(item) for item in post.limit_violations] if post is not None else []
    violations = []
    for item in _summarize_violations(raw_violations):
        component, original = model.iidm_to_original.get(item["equipment_id"], ("Unknown", item["equipment_id"]))
        violations.append({**item, "equipment_id": original, "equipment_type": component,
                           "iidm_id": item["equipment_id"]})

    topo_isolated = topology_islands(net, iidm_line, model)
    bus_results = sa.bus_results.reset_index()
    post_bus_rows = bus_results.loc[bus_results.contingency_id == contingency_id]
    nominal = vls.nominal_v
    post_v = {}
    for row in post_bus_rows.itertuples():
        kind, original = model.iidm_to_original.get(row.bus_id, (None, None))
        if kind == "Bus" and math.isfinite(row.v_mag):
            post_v[original] = (row.v_mag / float(nominal[row.voltage_level_id]), float(row.v_angle))
    pf_isolated = sorted((b for b in model.mapping["Bus"] if b not in post_v), key=_bus_sort_key) if converged else []
    isolated = topo_isolated
    loads = net.get_loads()
    load_by_bus = {model.mapping["Load"][o]["original_bus0"]: float(loads.at[r["iidm_id"], "p0"])
                   for o, r in model.mapping["Load"].items()}
    isolated_generators = sorted(o for o, r in model.mapping["Generator"].items() if r["original_bus0"] in isolated)
    isolated_set = set(isolated)
    isolated_lines = sorted(o for o, r in model.mapping["Line"].items()
                            if o != line_id and r["original_bus0"] in isolated_set and r["original_bus1"] in isolated_set)
    isolated_transformers = sorted(o for o, r in model.mapping["Transformer"].items()
                                   if r["original_bus0"] in isolated_set and r["original_bus1"] in isolated_set)
    disconnected = sorted(model.iidm_to_original.get(e, ("Unknown", e))[1] for e in getattr(post, "disconnected_elements", []) or [])

    result: dict[str, Any] = {
        "grid_id": GRID_ID,
        "scenario_id": model.scenario_id,
        "synthetic_scenario": True,
        "actual_operating_data": False,
        "engine": "PyPowSyBl AC Security Analysis (OpenLoadFlow)",
        "contingency_id": contingency_id,
        "outage_line_id": line_id,
        "outage_line": {"bus0": line_row["original_bus0"], "bus1": line_row["original_bus1"],
                        "voltage_kv": float(line_row["nominal_kv"])},
        "base_converged": True,
        "base_iterations": base_lf["iteration_count"],
        "pre_status": pre.status.name,
        "post_status": post_status,
        "analysis_status": "COMPLETED" if converged else "SECURITY_NOT_CONVERGED",
        "violation_assessment_available": converged,
        "main_component_converged": converged,
        "island_detected": bool(isolated),
        "isolated_bus_count": len(isolated),
        "isolated_buses": isolated,
        "isolated_load_mw": sum(load_by_bus.get(b, 0.0) for b in isolated),
        "isolated_generator_ids": isolated_generators,
        "isolated_line_ids": isolated_lines,
        "isolated_transformer_ids": isolated_transformers,
        "island_pf_computed": False,
        "island_check": {
            "topology_isolated_buses": topo_isolated,
            "post_pf_missing_buses": pf_isolated if converged else None,
            "consistent": (pf_isolated == topo_isolated) if converged else None,
        },
        "disconnected_elements": disconnected,
        "screening_limits": screening,
        "model_caveats": MODEL_CAVEATS,
        "violated_equipment_count": len({v["equipment_id"] for v in violations}) if converged else None,
        "violations": violations if converged else [],
    }
    numeric_keys = ["post_voltage_min_pu", "post_voltage_max_pu", "max_voltage_change_pu", "max_voltage_change_bus_id",
                    "max_line_p_change_mw", "max_line_p_change_id", "max_line_q_change_mvar", "max_line_q_change_id",
                    "post_max_line_loading_percent", "post_max_line_loading_id", "top_voltage_changes",
                    "top_line_flow_changes", "generator_q_screening"]
    if not converged:
        result.update({key: None for key in numeric_keys})
        return result
    if result["island_check"]["consistent"] is not True:
        raise RuntimeError(f"PF 결과의 분리 모선과 토폴로지 분석이 다릅니다: {result['island_check']}")

    branch = sa.branch_results.reset_index()
    post_branch = branch.loc[branch.contingency_id == contingency_id].set_index("branch_id")
    dv = {b: post_v[b][0] - base_v[b][0] for b in post_v}
    changes = []
    for original, row in model.mapping["Line"].items():
        if original == line_id or row["iidm_id"] not in post_branch.index:
            continue
        r = post_branch.loc[row["iidm_id"]]
        flows = (float(r.p1), float(r.q1), float(r.p2), float(r.q2))
        if not all(math.isfinite(x) for x in flows):
            continue
        b = base_lines[original]
        changes.append({
            "line_id": original, "base_p0_mw": b[0], "post_p0_mw": flows[0], "dp_mw": flows[0] - b[0],
            "base_q0_mvar": b[1], "post_q0_mvar": flows[1], "dq_mvar": flows[1] - b[1],
            "post_loading_percent": _loading(*flows, ratings[row["iidm_id"]]),
        })
    top_dv = sorted(dv.items(), key=lambda kv: -abs(kv[1]))[:5]
    top_dp = sorted(changes, key=lambda c: -abs(c["dp_mw"]))[:5]
    dq_max = max(changes, key=lambda c: abs(c["dq_mvar"]))
    load_max = max(changes, key=lambda c: c["post_loading_percent"])

    # Generator Q screening on the same independent network object, then restore the base variant.
    base_variant = net.get_working_variant_id()
    net.clone_variant(base_variant, contingency_id)
    try:
        net.set_working_variant(contingency_id)
        net.update_lines(id=[iidm_line], connected1=[False], connected2=[False])
        post_lf = pp.loadflow.run_ac(net, parameters=loadflow_parameters())
        gens = net.get_generators()
        live = gens.loc[gens.q.notna()]
        q_injection = -live.q
        q_viol = live.index[(q_injection < live.min_q - 1e-6) | (q_injection > live.max_q + 1e-6)]
        gen_q = {
            "status": post_lf[0].status.name if post_lf else None,
            "min_mvar": float(q_injection.min()), "max_mvar": float(q_injection.max()),
            "violations": [model.iidm_to_original[g][1] for g in q_viol],
        }
    finally:
        net.set_working_variant(base_variant)
        net.remove_variant(contingency_id)

    result.update({
        "post_voltage_min_pu": min(v for v, _ in post_v.values()),
        "post_voltage_max_pu": max(v for v, _ in post_v.values()),
        "max_voltage_change_pu": abs(top_dv[0][1]),
        "max_voltage_change_bus_id": top_dv[0][0],
        "max_line_p_change_mw": abs(top_dp[0]["dp_mw"]),
        "max_line_p_change_id": top_dp[0]["line_id"],
        "max_line_q_change_mvar": abs(dq_max["dq_mvar"]),
        "max_line_q_change_id": dq_max["line_id"],
        "post_max_line_loading_percent": load_max["post_loading_percent"],
        "post_max_line_loading_id": load_max["line_id"],
        "top_voltage_changes": [
            {"bus_id": b, "base_v_pu": base_v[b][0], "post_v_pu": post_v[b][0], "dv_pu": d} for b, d in top_dv
        ],
        "top_line_flow_changes": top_dp,
        "generator_q_screening": gen_q,
    })
    return result
