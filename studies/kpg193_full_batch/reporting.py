from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .core import compare_with_legacy


def write_study_outputs(
    result: dict[str, Any],
    output_dir: Path,
    legacy_csv: Path | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(result["summary"])
    violations = pd.DataFrame(result["violations"])
    sensitivity = pd.DataFrame(result["sensitivity"])

    paths = {
        "manifest": output_dir / "run_manifest.json",
        "summary": output_dir / "contingency_summary.csv",
        "violations": output_dir / "violations.csv",
        "ranking": output_dir / "risk_ranking.csv",
        "sensitivity": output_dir / "topn_sensitivity.csv",
        "result_json": output_dir / "summary.json",
        "report": output_dir / "report.html",
    }

    _write_json(paths["manifest"], result["manifest"])
    _write_json(paths["result_json"], result)
    summary.to_csv(paths["summary"], index=False, encoding="utf-8-sig")
    summary.sort_values(
        ["review_priority", "review_rank", "contingency_id"],
        na_position="last",
    ).to_csv(paths["ranking"], index=False, encoding="utf-8-sig")
    violations.to_csv(paths["violations"], index=False, encoding="utf-8-sig")
    sensitivity.to_csv(paths["sensitivity"], index=False, encoding="utf-8-sig")

    comparison = pd.DataFrame()
    mapping = pd.DataFrame()
    if legacy_csv is not None:
        legacy = pd.read_csv(legacy_csv)
        comparison, mapping = compare_with_legacy(result["summary"], legacy)
        paths["comparison"] = output_dir / "legacy_comparison.csv"
        paths["mapping"] = output_dir / "equipment_mapping.csv"
        comparison.to_csv(paths["comparison"], index=False, encoding="utf-8-sig")
        mapping.to_csv(paths["mapping"], index=False, encoding="utf-8-sig")

    paths["report"].write_text(
        build_html_report(
            result["manifest"],
            summary,
            violations,
            sensitivity,
            comparison,
            mapping,
        ),
        encoding="utf-8",
    )
    return paths


def build_html_report(
    manifest: dict[str, Any],
    summary: pd.DataFrame,
    violations: pd.DataFrame,
    sensitivity: pd.DataFrame,
    comparison: pd.DataFrame,
    mapping: pd.DataFrame,
) -> str:
    counts = summary["classification"].value_counts().to_dict()
    top = summary.sort_values(
        ["review_priority", "review_rank", "contingency_id"],
        na_position="last",
    ).head(20)
    top_columns = [
        column
        for column in (
            "review_rank",
            "contingency_id",
            "classification",
            "raw_status",
            "additional_disconnected_element_count",
            "violation_count",
            "violated_equipment_count",
            "max_loading_percent",
            "min_voltage_pu",
            "primary_violation_equipment_id",
            "error",
        )
        if column in top.columns
    ]

    cards = "".join(
        f'<div class="card"><strong>{html.escape(name)}</strong><span>{value}</span></div>'
        for name, value in [
            ("Total", len(summary)),
            ("Converged clean", counts.get("CONVERGED_CLEAN", 0)),
            ("Converged violation", counts.get("CONVERGED_VIOLATION", 0)),
            ("Islanded", counts.get("ISLANDED", 0)),
            ("Non-converged", counts.get("NON_CONVERGED", 0)),
            ("Execution error", counts.get("EXECUTION_ERROR", 0)),
        ]
    )

    comparison_html = "<p>Legacy comparison was not requested.</p>"
    if not comparison.empty:
        class_matches = int(comparison["classification_match"].sum())
        violation_matches = int(comparison["violation_presence_match"].sum())
        unmatched = (
            int((mapping["mapping_status"] != "MATCHED").sum())
            if not mapping.empty
            else 0
        )
        comparison_html = (
            f"<p>Matched contingencies: <strong>{len(comparison)}</strong>; "
            f"classification matches: <strong>{class_matches}</strong>; "
            f"violation-presence matches: <strong>{violation_matches}</strong>; "
            f"unmatched mapping rows: <strong>{unmatched}</strong>.</p>"
            + _table(comparison.head(20))
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KPG-193 Full Batch Security Study</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #182230; }}
h1, h2 {{ color: #102a43; }}
.meta {{ color: #52606d; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr)); gap: 12px; }}
.card {{ border: 1px solid #bcccdc; border-radius: 8px; padding: 12px; background: #f8fafc; }}
.card strong, .card span {{ display: block; }} .card span {{ font-size: 1.6rem; margin-top: 4px; }}
table {{ border-collapse: collapse; width: 100%; font-size: .85rem; display: block; overflow-x: auto; }}
th, td {{ border: 1px solid #d9e2ec; padding: 6px 8px; text-align: left; white-space: nowrap; }}
th {{ background: #eaf2f8; }} section {{ margin-top: 2rem; }} code {{ background:#eef2f6; padding:2px 4px; }}
</style>
</head>
<body>
<h1>KPG-193 Full Batch Security Study</h1>
<p class="meta">Generated {html.escape(str(manifest.get('created_at_utc')))} · PyPowSyBl {html.escape(str(manifest.get('pypowsybl_version')))}</p>
<p>Ranking is a deterministic review order based on physical-analysis outcomes; it is not an AI score or a probabilistic risk estimate.</p>
<div class="cards">{cards}</div>
<section><h2>Top review contingencies</h2>{_table(top[top_columns])}</section>
<section><h2>Violation records</h2><p>{len(violations)} equipment-level records.</p>{_table(violations.head(20))}</section>
<section><h2>Top-N sensitivity linkage</h2><p>Sensitivity is attempted only for converged line outages with a violated monitored line.</p>{_table(sensitivity.head(30))}</section>
<section><h2>Legacy direct AC-PF comparison</h2>{comparison_html}</section>
<section><h2>Reproducibility</h2><pre>{html.escape(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))}</pre></section>
</body>
</html>
"""


def _table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<p>No records.</p>"
    return frame.to_html(index=False, border=0, na_rep="", escape=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
