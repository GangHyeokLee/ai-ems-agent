from __future__ import annotations

import hashlib
import platform
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pypowsybl as pp

from ai_ems.network import LOADFLOW_PARAMETERS, load_network, run_ac_load_flow
from ai_ems.tools.sensitivity_tools import rank_generator_sensitivities

from .core import (
    classify_result,
    parse_line_buses,
    primary_violation,
    rank_contingencies,
    summarize_violations,
)


@dataclass(frozen=True)
class StudyConfig:
    case_file: Path
    output_dir: Path
    include_generators: bool = False
    chunk_size: int = 0
    top_n_sensitivity_contingencies: int = 10
    sensitivity_candidates_per_contingency: int = 10
    run_sensitivity: bool = True


@dataclass(frozen=True)
class ContingencySpec:
    contingency_id: str
    element_id: str
    element_type: str
    from_bus: int | None = None
    to_bus: int | None = None


class FullBatchStudy:
    def __init__(self, config: StudyConfig):
        self.config = config
        self.network = load_network(config.case_file)
        self.lines = self.network.get_lines()
        self.generators = self.network.get_generators()
        self.voltage_levels = self.network.get_voltage_levels()
        self.buses = self.network.get_buses()
        self.line_limits = _extract_apparent_power_limits(
            self.network, set(map(str, self.lines.index))
        )

    def run(self) -> dict[str, Any]:
        base = run_ac_load_flow(self.network)
        if not base["converged"]:
            raise RuntimeError("Base-case AC load flow did not converge.")

        specs = self._build_specs()
        chunks = _chunks(specs, self.config.chunk_size)
        summaries: list[dict[str, Any]] = []
        violations: list[dict[str, Any]] = []

        for chunk in chunks:
            chunk_summaries, chunk_violations = self._run_with_isolation(chunk)
            summaries.extend(chunk_summaries)
            violations.extend(chunk_violations)

        ranked = rank_contingencies(summaries)
        sensitivity = self._run_top_sensitivities(ranked)
        return {
            "manifest": self._manifest(base, specs),
            "summary": ranked,
            "violations": violations,
            "sensitivity": sensitivity,
        }

    def _build_specs(self) -> list[ContingencySpec]:
        specs: list[ContingencySpec] = []
        for line_id, row in self.lines.sort_index().iterrows():
            from_bus, to_bus = parse_line_buses(str(line_id), row)
            specs.append(
                ContingencySpec(
                    contingency_id=f"LINE_OUT::{line_id}",
                    element_id=str(line_id),
                    element_type="LINE",
                    from_bus=from_bus,
                    to_bus=to_bus,
                )
            )

        if self.config.include_generators:
            connected = self.generators[self.generators["connected"]]
            for generator_id in sorted(map(str, connected.index)):
                specs.append(
                    ContingencySpec(
                        contingency_id=f"GEN_OUT::{generator_id}",
                        element_id=generator_id,
                        element_type="GENERATOR",
                    )
                )
        return specs

    def _run_with_isolation(
        self, specs: list[ContingencySpec]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            return self._run_chunk(specs)
        except Exception as exc:
            if len(specs) > 1:
                middle = len(specs) // 2
                left_summary, left_violations = self._run_with_isolation(
                    specs[:middle]
                )
                right_summary, right_violations = self._run_with_isolation(
                    specs[middle:]
                )
                return (
                    left_summary + right_summary,
                    left_violations + right_violations,
                )

            spec = specs[0]
            message = f"{type(exc).__name__}: {exc}"
            return [
                {
                    **asdict(spec),
                    "raw_status": None,
                    "classification": "EXECUTION_ERROR",
                    "violation_count": 0,
                    "violated_equipment_count": 0,
                    "error": message,
                }
            ], []

    def _run_chunk(
        self, specs: list[ContingencySpec]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        analysis = pp.security.create_analysis()
        for spec in specs:
            analysis.add_single_element_contingency(
                spec.element_id, spec.contingency_id
            )

        monitored_kwargs: dict[str, Any] = {
            "branch_ids": list(map(str, self.lines.index)),
            "voltage_level_ids": list(map(str, self.voltage_levels.index)),
        }
        try:
            analysis.add_monitored_elements(**monitored_kwargs)
        except TypeError:
            # Keep line metrics on older providers even if voltage monitoring
            # is unavailable.
            analysis.add_monitored_elements(
                branch_ids=monitored_kwargs["branch_ids"]
            )

        parameters = pp.security.Parameters(
            load_flow_parameters=LOADFLOW_PARAMETERS,
            provider_parameters={
                "contingencyPropagation": "true",
                "createResultExtension": "true",
            },
        )
        result = analysis.run_ac(self.network, parameters=parameters)

        branch_results = getattr(result, "branch_results", None)
        bus_results = getattr(result, "bus_results", None)
        summaries: list[dict[str, Any]] = []
        flat_violations: list[dict[str, Any]] = []

        for spec in specs:
            post = result.find_post_contingency_result(spec.contingency_id)
            raw_status = _enum_name(getattr(post, "status", None))
            post_violations = getattr(post, "limit_violations", None)
            raw_violations = [
                _serialize_object(item)
                for item in (
                    post_violations if post_violations is not None else []
                )
            ]
            grouped = summarize_violations(raw_violations)
            connectivity = _serialize_connectivity(post)
            branch_metrics = _branch_metrics(
                branch_results,
                spec.contingency_id,
                self.line_limits,
            )
            voltage_metrics = _voltage_metrics(
                bus_results,
                spec.contingency_id,
                self.buses,
                self.voltage_levels,
            )
            primary = primary_violation(grouped)
            line_violation = _most_severe_line_violation(
                grouped,
                set(map(str, self.lines.index)),
            )
            violation_loading = (
                line_violation.get("loading_percent") if line_violation else None
            )
            if violation_loading is not None and (
                branch_metrics["max_loading_percent"] is None
                or float(violation_loading)
                > float(branch_metrics["max_loading_percent"])
            ):
                branch_metrics.update(
                    max_loading_percent=float(violation_loading),
                    worst_branch_id=line_violation["equipment_id"],
                )
            max_relative = max(
                (
                    float(item["relative_violation"])
                    for item in grouped
                    if item.get("relative_violation") is not None
                ),
                default=None,
            )
            max_voltage_violation = max(
                (
                    float(item["violation_amount"])
                    for item in grouped
                    if item.get("limit_type")
                    in {"LOW_VOLTAGE", "HIGH_VOLTAGE", "VOLTAGE"}
                    and item.get("violation_amount") is not None
                ),
                default=None,
            )
            classification = classify_result(
                raw_status,
                len(raw_violations),
                connectivity,
            )

            summary = {
                **asdict(spec),
                "raw_status": raw_status,
                "classification": classification,
                "violation_count": len(raw_violations),
                "violated_equipment_count": len(grouped),
                "max_relative_violation": max_relative,
                "max_voltage_violation": max_voltage_violation,
                "primary_violation_equipment_id": (
                    primary.get("equipment_id") if primary else None
                ),
                "primary_violation_type": (
                    primary.get("limit_type") if primary else None
                ),
                "primary_violation_amount": (
                    primary.get("violation_amount") if primary else None
                ),
                "sensitivity_target_line_id": (
                    line_violation.get("equipment_id") if line_violation else None
                ),
                **connectivity,
                **branch_metrics,
                **voltage_metrics,
                "error": None,
            }
            summaries.append(summary)

            for item in grouped:
                flat_violations.append(
                    {
                        "contingency_id": spec.contingency_id,
                        "element_id": spec.element_id,
                        "element_type": spec.element_type,
                        **item,
                    }
                )

        return summaries, flat_violations

    def _run_top_sensitivities(
        self, ranked: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not self.config.run_sensitivity:
            return []

        eligible = [
            row
            for row in ranked
            if row.get("element_type") == "LINE"
            and row.get("classification") == "CONVERGED_VIOLATION"
            and row.get("sensitivity_target_line_id") in self.lines.index
        ][: self.config.top_n_sensitivity_contingencies]

        output: list[dict[str, Any]] = []
        for contingency in eligible:
            try:
                result = rank_generator_sensitivities(
                    self.network,
                    outage_line_id=str(contingency["element_id"]),
                    monitored_line_id=str(
                        contingency["sensitivity_target_line_id"]
                    ),
                    top_n=self.config.sensitivity_candidates_per_contingency,
                )
                for candidate_rank, candidate in enumerate(
                    result["candidates"], start=1
                ):
                    output.append(
                        {
                            "review_rank": contingency.get("review_rank"),
                            "contingency_id": contingency["contingency_id"],
                            "outage_line_id": contingency["element_id"],
                            "monitored_line_id": result["monitored_line_id"],
                            "candidate_rank": candidate_rank,
                            **candidate,
                            "error": None,
                        }
                    )
            except Exception as exc:
                output.append(
                    {
                        "review_rank": contingency.get("review_rank"),
                        "contingency_id": contingency["contingency_id"],
                        "outage_line_id": contingency["element_id"],
                        "monitored_line_id": contingency.get(
                            "sensitivity_target_line_id"
                        ),
                        "candidate_rank": None,
                        "generator_id": None,
                        "sensitivity": None,
                        "abs_sensitivity": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return output

    def _manifest(
        self,
        base: dict[str, Any],
        specs: list[ContingencySpec],
    ) -> dict[str, Any]:
        return {
            "study": "KPG-193 Full Batch Security Study",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "pypowsybl_version": getattr(pp, "__version__", "unknown"),
            "case_file": str(self.config.case_file.resolve()),
            "case_sha256": _sha256(self.config.case_file),
            "git_commit": _git_value("rev-parse", "HEAD"),
            "git_worktree_status": _git_value("status", "--short"),
            "network_counts": {
                "lines": len(self.lines),
                "generators": len(self.generators),
                "connected_generators": int(self.generators["connected"].sum()),
                "buses": len(self.buses),
            },
            "contingency_counts": {
                "total": len(specs),
                "line": sum(spec.element_type == "LINE" for spec in specs),
                "generator": sum(
                    spec.element_type == "GENERATOR" for spec in specs
                ),
            },
            "base_case": base,
            "config": {
                **asdict(self.config),
                "case_file": str(self.config.case_file),
                "output_dir": str(self.config.output_dir),
            },
            "ranking_method": "physics-first deterministic lexicographic",
        }


def _chunks(items: list[Any], chunk_size: int) -> list[list[Any]]:
    if chunk_size <= 0 or chunk_size >= len(items):
        return [items]
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def _serialize_object(obj: Any) -> dict[str, Any]:
    fields = [
        "subject_id",
        "subject_name",
        "limit_type",
        "limit_name",
        "acceptable_duration",
        "limit",
        "limit_reduction",
        "value",
        "side",
    ]
    output: dict[str, Any] = {}
    for field in fields:
        if not hasattr(obj, field):
            continue
        output[field] = _json_value(getattr(obj, field))
    return output or {"raw": str(obj)}


def _serialize_connectivity(post_result: Any) -> dict[str, Any]:
    disconnected = (
        getattr(post_result, "disconnected_elements", None)
        if post_result is not None
        else None
    )
    return {
        "created_connected_component_count": None,
        "created_synchronous_component_count": None,
        "disconnected_load_active_power": None,
        "disconnected_generation_active_power": None,
        "disconnected_element_ids": _json_value(disconnected) or [],
        "disconnected_element_count": len(disconnected or []),
    }


def _branch_metrics(
    frame: pd.DataFrame | None,
    contingency_id: str,
    limits: dict[str, float],
) -> dict[str, Any]:
    rows = _contingency_rows(frame, contingency_id)
    if rows.empty:
        return {
            "max_loading_percent": None,
            "worst_branch_id": None,
            "worst_branch_apparent_power_mva": None,
        }

    candidates: list[tuple[float, str, float]] = []
    for _, row in rows.iterrows():
        line_id = str(row.get("branch_id", ""))
        values = []
        for p_col, q_col in (("p1", "q1"), ("p2", "q2")):
            if p_col in row and q_col in row and pd.notna(row[p_col]) and pd.notna(row[q_col]):
                values.append(float(np.hypot(row[p_col], row[q_col])))
        if not values or line_id not in limits or limits[line_id] <= 0:
            continue
        apparent = max(values)
        candidates.append((apparent / limits[line_id] * 100.0, line_id, apparent))

    if not candidates:
        return {
            "max_loading_percent": None,
            "worst_branch_id": None,
            "worst_branch_apparent_power_mva": None,
        }
    loading, line_id, apparent = max(candidates, key=lambda item: (item[0], item[1]))
    return {
        "max_loading_percent": loading,
        "worst_branch_id": line_id,
        "worst_branch_apparent_power_mva": apparent,
    }


def _voltage_metrics(
    frame: pd.DataFrame | None,
    contingency_id: str,
    buses: pd.DataFrame,
    voltage_levels: pd.DataFrame,
) -> dict[str, Any]:
    rows = _contingency_rows(frame, contingency_id)
    if rows.empty or "v_mag" not in rows.columns:
        return {"min_voltage_pu": None, "min_voltage_bus_id": None}

    nominal_by_vl = voltage_levels["nominal_v"].to_dict()
    bus_to_vl = buses["voltage_level_id"].to_dict()
    values: list[tuple[float, str]] = []
    for _, row in rows.iterrows():
        bus_id = str(row.get("bus_id", ""))
        vl_id = row.get("voltage_level_id")
        if vl_id is None or pd.isna(vl_id):
            vl_id = bus_to_vl.get(bus_id)
        nominal = nominal_by_vl.get(vl_id)
        if nominal and pd.notna(row.get("v_mag")):
            values.append((float(row["v_mag"]) / float(nominal), bus_id))
    if not values:
        return {"min_voltage_pu": None, "min_voltage_bus_id": None}
    voltage, bus_id = min(values, key=lambda item: (item[0], item[1]))
    return {"min_voltage_pu": voltage, "min_voltage_bus_id": bus_id}


def _contingency_rows(
    frame: pd.DataFrame | None, contingency_id: str
) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame()
    rows = frame.reset_index()
    if "contingency_id" in rows.columns:
        return rows[rows["contingency_id"] == contingency_id]
    return pd.DataFrame()


def _extract_apparent_power_limits(
    network,
    line_ids: set[str],
) -> dict[str, float]:
    try:
        frame = network.get_operational_limits().reset_index()
    except Exception:
        return {}
    if frame.empty:
        return {}

    id_column = next(
        (name for name in ("element_id", "id", "branch_id") if name in frame.columns),
        frame.columns[0],
    )
    type_column = next(
        (name for name in ("type", "limit_type") if name in frame.columns),
        None,
    )
    value_column = next(
        (name for name in ("value", "limit") if name in frame.columns),
        None,
    )
    if type_column is None or value_column is None:
        return {}

    selected = frame[
        frame[id_column].astype(str).isin(line_ids)
        & frame[type_column].astype(str).str.upper().eq("APPARENT_POWER")
    ]
    output: dict[str, float] = {}
    for line_id, group in selected.groupby(id_column):
        source = group
        if "acceptable_duration" in group.columns:
            permanent = group[group["acceptable_duration"] < 0]
            if not permanent.empty:
                source = permanent
        value = source[value_column].min()
        if pd.notna(value):
            output[str(line_id)] = float(value)
    return output


def _most_severe_line_violation(
    violations: list[dict[str, Any]],
    line_ids: set[str],
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in violations
        if item.get("equipment_id") in line_ids
        and item.get("loading_percent") is not None
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            float(item["loading_percent"]),
            str(item["equipment_id"]),
        ),
    )


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "name", value))


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "name"):
        return value.name
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value if isinstance(value, (str, int, float, bool)) else str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_value(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
