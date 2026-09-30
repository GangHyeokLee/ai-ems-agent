from __future__ import annotations

from datetime import datetime
from importlib.metadata import version
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import pypowsybl as pp

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_ems.config import CASE_FILE
from ai_ems.network import load_network
from ai_ems.tools.generator_contingency_tools import (
    analyze_generator_contingency,
    build_generator_loadflow_parameters,
)

OUTAGE_GENERATOR_ID = "GEN-124#1"
MONITORED_LINE_ID = "LINE-134-193"
BALANCE_TYPES = [
    "PROPORTIONAL_TO_GENERATION_P_MAX",
    "PROPORTIONAL_TO_GENERATION_P",
    "PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN",
]

PARTICIPATION_DELTA_THRESHOLD_MW = 0.01
NEAR_PMAX_ABSOLUTE_THRESHOLD_MW = 5.0
NEAR_PMAX_RELATIVE_THRESHOLD_PCT = 1.0

OUTPUT_DIR = REPO_ROOT / "results" / "kpg193_generator_balance_type_gen124_1"

THERMAL_LIMIT_TYPES = {
    "APPARENT_POWER",
    "ACTIVE_POWER",
    "CURRENT",
}
VOLTAGE_LIMIT_TYPES = {
    "LOW_VOLTAGE",
    "HIGH_VOLTAGE",
    "VOLTAGE",
}


def _is_finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _float_or_none(value: Any) -> float | None:
    return float(value) if _is_finite(value) else None


def _converged(results) -> bool:
    return bool(results) and all(
        component.status.name == "CONVERGED" for component in results
    )


def _get_permanent_apparent_power_limit_mva(network, line_id: str) -> float | None:
    """Return the strictest selected permanent apparent-power limit for one line."""

    limits = network.get_loading_limits().reset_index()

    required_columns = {"element_id", "type", "value"}
    if not required_columns.issubset(limits.columns):
        return None

    selected = limits[
        (limits["element_id"].astype(str) == line_id)
        & (limits["type"].astype(str) == "APPARENT_POWER")
    ].copy()

    if selected.empty:
        return None

    if "acceptable_duration" in selected.columns:
        duration = pd.to_numeric(
            selected["acceptable_duration"],
            errors="coerce",
        )
        permanent = selected[duration == -1]
        if not permanent.empty:
            selected = permanent

    values = pd.to_numeric(selected["value"], errors="coerce")
    values = values[np.isfinite(values) & (values > 0)]

    if values.empty:
        return None

    return float(values.min())


def _run_post_line_snapshot(
    balance_type: str,
) -> dict[str, Any]:
    """Run a fresh post-contingency AC LF and capture LINE-134-193 S/loading."""

    network = load_network(CASE_FILE)
    lines = network.get_lines()

    if MONITORED_LINE_ID not in lines.index:
        raise ValueError(f"Unknown monitored line: {MONITORED_LINE_ID}")

    network.update_generators(
        id=OUTAGE_GENERATOR_ID,
        connected=False,
    )

    parameters = build_generator_loadflow_parameters(
        slack_mode="distributed",
        balance_type=balance_type,
    )

    results = pp.loadflow.run_ac(
        network,
        parameters=parameters,
    )

    if not _converged(results):
        return {
            "converged": False,
            "p1_mw": None,
            "q1_mvar": None,
            "p2_mw": None,
            "q2_mvar": None,
            "apparent_power_mva": None,
            "permanent_apparent_power_limit_mva": None,
            "loading_percent": None,
        }

    row = network.get_lines().loc[MONITORED_LINE_ID]

    p1 = float(row["p1"])
    q1 = float(row["q1"])
    p2 = float(row["p2"])
    q2 = float(row["q2"])

    apparent_power_mva = max(
        float(np.hypot(p1, q1)),
        float(np.hypot(p2, q2)),
    )

    limit_mva = _get_permanent_apparent_power_limit_mva(
        network,
        MONITORED_LINE_ID,
    )

    loading_percent = (
        apparent_power_mva / limit_mva * 100.0
        if limit_mva not in (None, 0.0)
        else None
    )

    return {
        "converged": True,
        "p1_mw": p1,
        "q1_mvar": q1,
        "p2_mw": p2,
        "q2_mvar": q2,
        "apparent_power_mva": apparent_power_mva,
        "permanent_apparent_power_limit_mva": limit_mva,
        "loading_percent": loading_percent,
    }


def _generator_distribution_rows(
    balance_type: str,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for item in result.get("generator_changes", []):
        delta = _float_or_none(item.get("delta_generation_mw"))
        max_p = _float_or_none(item.get("max_p_mw"))
        headroom_after = _float_or_none(item.get("headroom_after_mw"))

        headroom_after_pct = None
        if headroom_after is not None and max_p not in (None, 0.0):
            headroom_after_pct = headroom_after / abs(max_p) * 100.0

        participating = (
            delta is not None
            and abs(delta) > PARTICIPATION_DELTA_THRESHOLD_MW
        )

        near_pmax_5mw = (
            headroom_after is not None
            and headroom_after <= NEAR_PMAX_ABSOLUTE_THRESHOLD_MW
        )

        near_pmax_1pct = (
            headroom_after_pct is not None
            and headroom_after_pct <= NEAR_PMAX_RELATIVE_THRESHOLD_PCT
        )

        rows.append(
            {
                "balance_type": balance_type,
                "generator_id": item.get("generator_id"),
                "bus_id": item.get("bus_id"),
                "generation_before_mw": item.get("generation_before_mw"),
                "delta_generation_mw": delta,
                "generation_after_mw": item.get("generation_after_mw"),
                "min_p_mw": item.get("min_p_mw"),
                "max_p_mw": max_p,
                "headroom_before_mw": item.get("headroom_before_mw"),
                "headroom_after_mw": headroom_after,
                "headroom_after_pct_of_pmax": headroom_after_pct,
                "participating": participating,
                "near_pmax_5mw": near_pmax_5mw,
                "near_pmax_1pct": near_pmax_1pct,
            }
        )

    return rows


def _violation_rows(
    balance_type: str,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    security = result.get("security_analysis", {})
    rows: list[dict[str, Any]] = []

    for item in security.get("limit_violations", []):
        limit_type = str(item.get("limit_type", ""))

        rows.append(
            {
                "balance_type": balance_type,
                **item,
                "category": (
                    "thermal"
                    if limit_type in THERMAL_LIMIT_TYPES
                    else "voltage"
                    if limit_type in VOLTAGE_LIMIT_TYPES
                    else "other"
                ),
            }
        )

    return rows


def _distribution_summary(
    generator_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not generator_rows:
        return {
            "participating_generator_count": 0,
            "min_headroom_after_mw": None,
            "headroom_p10_mw": None,
            "headroom_p25_mw": None,
            "headroom_median_mw": None,
            "near_pmax_count_5mw": 0,
            "near_pmax_count_1pct": 0,
            "total_positive_delta_generation_mw": None,
            "max_delta_generation_mw": None,
        }

    frame = pd.DataFrame(generator_rows)

    headroom = pd.to_numeric(
        frame["headroom_after_mw"],
        errors="coerce",
    ).dropna()

    delta = pd.to_numeric(
        frame["delta_generation_mw"],
        errors="coerce",
    ).dropna()

    positive_delta = delta[delta > PARTICIPATION_DELTA_THRESHOLD_MW]

    return {
        "participating_generator_count": int(frame["participating"].sum()),
        "min_headroom_after_mw": (
            float(headroom.min()) if not headroom.empty else None
        ),
        "headroom_p10_mw": (
            float(headroom.quantile(0.10)) if not headroom.empty else None
        ),
        "headroom_p25_mw": (
            float(headroom.quantile(0.25)) if not headroom.empty else None
        ),
        "headroom_median_mw": (
            float(headroom.median()) if not headroom.empty else None
        ),
        "near_pmax_count_5mw": int(frame["near_pmax_5mw"].sum()),
        "near_pmax_count_1pct": int(frame["near_pmax_1pct"].sum()),
        "total_positive_delta_generation_mw": (
            float(positive_delta.sum()) if not positive_delta.empty else 0.0
        ),
        "max_delta_generation_mw": (
            float(delta.max()) if not delta.empty else None
        ),
    }


def _build_summary_row(
    balance_type: str,
    result: dict[str, Any],
    line_snapshot: dict[str, Any],
    generator_rows: list[dict[str, Any]],
    violation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    security = result.get("security_analysis", {})
    post = result.get("post_contingency", {})
    post_loadflow = post.get("loadflow", {})
    post_power = post.get("power_balance") or {}
    base_power = result.get("base", {}).get("power_balance", {})

    thermal_violation_count = sum(
        row.get("category") == "thermal" for row in violation_rows
    )
    voltage_violation_count = sum(
        row.get("category") == "voltage" for row in violation_rows
    )

    return {
        "balance_type": balance_type,
        "provider": pp.loadflow.get_default_provider(),
        "base_converged": result.get("base_converged"),
        "post_converged": result.get("post_contingency_converged"),
        "security_status": security.get("post_status"),
        "lost_generation_mw": result.get("outage_generator", {}).get(
            "actual_generation_mw"
        ),
        "distributed_active_power_mw": post_loadflow.get(
            "distributed_active_power_mw"
        ),
        "residual_mismatch_mw": post_loadflow.get(
            "active_power_mismatch_mw"
        ),
        **_distribution_summary(generator_rows),
        "base_system_loss_mw": base_power.get("balance_based_loss_mw"),
        "post_system_loss_mw": post_power.get("balance_based_loss_mw"),
        "system_loss_change_mw": result.get("loss_change", {}).get(
            "balance_based_loss_change_mw"
        ),
        "base_line_p_loss_mw": base_power.get("line_active_power_loss_mw"),
        "post_line_p_loss_mw": post_power.get("line_active_power_loss_mw"),
        "line_p_loss_change_mw": result.get("loss_change", {}).get(
            "line_active_power_loss_change_mw"
        ),
        "line_134_193_mva": line_snapshot.get("apparent_power_mva"),
        "line_134_193_limit_mva": line_snapshot.get(
            "permanent_apparent_power_limit_mva"
        ),
        "line_134_193_loading_pct": line_snapshot.get("loading_percent"),
        "violation_count": security.get("violation_count"),
        "violated_equipment_count": security.get("violated_equipment_count"),
        "thermal_violation_count": thermal_violation_count,
        "voltage_violation_count": voltage_violation_count,
    }


def _write_readme(summary_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# KPG-193 GEN-124#1 Distributed Slack Balance Type 비교",
        "",
        "## 목적",
        "",
        "동일한 발전기 N-1 사고에서 Distributed Slack의 Balance Type에 따라 발전기 보상 분포, headroom, 선로 과부하, 손실 및 Security Analysis 결과가 어떻게 달라지는지 비교한다.",
        "",
        "## 비교 대상",
        "",
        *[f"- `{balance_type}`" for balance_type in BALANCE_TYPES],
        "",
        "`PROPORTIONAL_TO_GENERATION_PARTICIPATION_FACTOR`는 KPG-193의 `activePowerControl` extension 데이터가 없어 이번 실험에서 제외했다.",
        "",
        "## 해석 주의",
        "",
        "- Distributed Slack Balance Type은 Load Flow의 유효전력 mismatch 배분 가정이다.",
        "- 특정 Balance Type에서 과부하나 손실이 더 작다고 해서 그 설정을 최적 Redispatch라고 해석하지 않는다.",
        "- 실제 운영 제어 후보는 Sensitivity/Redispatch를 별도로 생성하고 AC Load Flow 및 Security Analysis로 재검증해야 한다.",
        "",
        "## 출력 파일",
        "",
        "- `balance_type_summary.csv`: Balance Type별 핵심 비교 지표",
        "- `generator_distribution.csv`: 생존 발전기별 ΔP/headroom",
        "- `violations.csv`: Security Analysis limit violation 원시 레코드",
        "- `summary.json`: 핵심 비교 지표 JSON",
        "- `run_manifest.json`: 실행 조건",
        "",
        "## 실행 결과 요약",
        "",
    ]

    for row in summary_rows:
        lines.extend(
            [
                f"### {row['balance_type']}",
                "",
                f"- Post AC LF converged: `{row['post_converged']}`",
                f"- Security status: `{row['security_status']}`",
                f"- Distributed active power: `{_fmt(row['distributed_active_power_mw'])} MW`",
                f"- Residual mismatch: `{_fmt(row['residual_mismatch_mw'])} MW`",
                f"- Participating generators: `{row['participating_generator_count']}`",
                f"- Minimum post headroom: `{_fmt(row['min_headroom_after_mw'])} MW`",
                f"- Near Pmax (≤5 MW): `{row['near_pmax_count_5mw']}`",
                f"- Near Pmax (≤1% Pmax): `{row['near_pmax_count_1pct']}`",
                f"- LINE-134-193: `{_fmt(row['line_134_193_mva'])} MVA / {_fmt(row['line_134_193_loading_pct'])}%`",
                f"- Post system loss: `{_fmt(row['post_system_loss_mw'])} MW`",
                f"- Post AC line P loss: `{_fmt(row['post_line_p_loss_mw'])} MW`",
                f"- Limit violations: `{row['violation_count']}` records / `{row['violated_equipment_count']}` equipment",
                f"- Thermal / voltage violation records: `{row['thermal_violation_count']} / {row['voltage_violation_count']}`",
                "",
            ]
        )

    (OUTPUT_DIR / "README.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def _fmt(value: Any) -> str:
    return f"{float(value):.3f}" if _is_finite(value) else "N/A"


def _print_mode_result(
    summary: dict[str, Any],
    generator_rows: list[dict[str, Any]],
) -> None:
    print()
    print("-" * 80)
    print(summary["balance_type"])
    print("-" * 80)
    print(
        "LF/Security       : "
        f"post={summary['post_converged']} / {summary['security_status']}"
    )
    print(
        "Distributed/mismatch: "
        f"{_fmt(summary['distributed_active_power_mw'])} MW / "
        f"{_fmt(summary['residual_mismatch_mw'])} MW"
    )
    print(
        "Participants      : "
        f"{summary['participating_generator_count']}"
    )
    print(
        "Headroom min/p10/median: "
        f"{_fmt(summary['min_headroom_after_mw'])} / "
        f"{_fmt(summary['headroom_p10_mw'])} / "
        f"{_fmt(summary['headroom_median_mw'])} MW"
    )
    print(
        "Near Pmax        : "
        f"<=5MW {summary['near_pmax_count_5mw']} / "
        f"<=1% {summary['near_pmax_count_1pct']}"
    )
    print(
        f"{MONITORED_LINE_ID:<18}: "
        f"{_fmt(summary['line_134_193_mva'])} MVA / "
        f"{_fmt(summary['line_134_193_loading_pct'])}%"
    )
    print(
        "Post loss        : "
        f"system={_fmt(summary['post_system_loss_mw'])} MW / "
        f"lineP={_fmt(summary['post_line_p_loss_mw'])} MW"
    )
    print(
        "Violations       : "
        f"records={summary['violation_count']} / "
        f"equipment={summary['violated_equipment_count']} / "
        f"thermal={summary['thermal_violation_count']} / "
        f"voltage={summary['voltage_violation_count']}"
    )

    top = sorted(
        generator_rows,
        key=lambda row: abs(row.get("delta_generation_mw") or 0.0),
        reverse=True,
    )[:8]

    print("Top generator ΔP:")
    for row in top:
        print(
            f"  {str(row['generator_id']):<14} "
            f"ΔP={_fmt(row['delta_generation_mw']):>9} MW  "
            f"post_headroom={_fmt(row['headroom_after_mw']):>9} MW"
        )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    provider = pp.loadflow.get_default_provider()

    print("=" * 80)
    print("KPG-193 GEN-124#1 Distributed Slack Balance Type Comparison")
    print("=" * 80)
    print(f"PyPowSyBl : {version('pypowsybl')}")
    print(f"Provider  : {provider}")
    print(f"Case      : {CASE_FILE}")
    print(f"Outage    : {OUTAGE_GENERATOR_ID}")
    print(f"Monitor   : {MONITORED_LINE_ID}")

    summary_rows: list[dict[str, Any]] = []
    all_generator_rows: list[dict[str, Any]] = []
    all_violation_rows: list[dict[str, Any]] = []

    for balance_type in BALANCE_TYPES:
        parameters = build_generator_loadflow_parameters(
            slack_mode="distributed",
            balance_type=balance_type,
        )

        supported = pp.loadflow.check_loadflow_parameters(
            parameters=parameters,
            provider=provider,
        )
        if not supported:
            raise RuntimeError(
                f"Load Flow provider {provider} does not support {balance_type}."
            )

        result = analyze_generator_contingency(
            case_path=CASE_FILE,
            generator_id=OUTAGE_GENERATOR_ID,
            slack_mode="distributed",
            balance_type=balance_type,
            top_n_overloads=20,
        )

        line_snapshot = _run_post_line_snapshot(balance_type)
        generator_rows = _generator_distribution_rows(
            balance_type,
            result,
        )
        violation_rows = _violation_rows(
            balance_type,
            result,
        )

        summary = _build_summary_row(
            balance_type,
            result,
            line_snapshot,
            generator_rows,
            violation_rows,
        )

        summary_rows.append(summary)
        all_generator_rows.extend(generator_rows)
        all_violation_rows.extend(violation_rows)

        _print_mode_result(summary, generator_rows)

    summary_frame = pd.DataFrame(summary_rows)
    generator_frame = pd.DataFrame(all_generator_rows)
    violation_frame = pd.DataFrame(all_violation_rows)

    summary_frame.to_csv(
        OUTPUT_DIR / "balance_type_summary.csv",
        index=False,
    )
    generator_frame.to_csv(
        OUTPUT_DIR / "generator_distribution.csv",
        index=False,
    )
    violation_frame.to_csv(
        OUTPUT_DIR / "violations.csv",
        index=False,
    )

    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(
            summary_rows,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "pypowsybl_version": version("pypowsybl"),
        "loadflow_provider": provider,
        "case_file": str(CASE_FILE),
        "outage_generator_id": OUTAGE_GENERATOR_ID,
        "monitored_line_id": MONITORED_LINE_ID,
        "balance_types": BALANCE_TYPES,
        "excluded_balance_type": (
            "PROPORTIONAL_TO_GENERATION_PARTICIPATION_FACTOR"
        ),
        "excluded_reason": (
            "KPG-193 activePowerControl extension has no rows."
        ),
        "participation_delta_threshold_mw": (
            PARTICIPATION_DELTA_THRESHOLD_MW
        ),
        "near_pmax_absolute_threshold_mw": (
            NEAR_PMAX_ABSOLUTE_THRESHOLD_MW
        ),
        "near_pmax_relative_threshold_pct": (
            NEAR_PMAX_RELATIVE_THRESHOLD_PCT
        ),
        "interpretation": (
            "Balance Type is a load-flow balancing assumption, not corrective redispatch."
        ),
    }

    (OUTPUT_DIR / "run_manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _write_readme(summary_rows)

    print()
    print("=" * 80)
    print("Saved")
    print("=" * 80)
    for name in [
        "balance_type_summary.csv",
        "generator_distribution.csv",
        "violations.csv",
        "summary.json",
        "run_manifest.json",
        "README.md",
    ]:
        print(OUTPUT_DIR / name)


if __name__ == "__main__":
    main()
