from pathlib import Path
from typing import Any, Literal

from studies.kpg193_full_batch.runner import (
    FullBatchStudy,
    StudyConfig,
)


def run_generator_contingency_screening(
    case_path: str | Path,
    slack_mode: Literal["single", "distributed"] = "single",
    top_n: int = 5,
) -> dict[str, Any]:
    study = FullBatchStudy(
        StudyConfig(
            case_file=Path(case_path),
            output_dir=Path("output/agent_generator_screening"),
            generator_only=True,
            generator_slack=slack_mode,
            run_sensitivity=False,
        )
    )

    result = study.run()

    summary = result["summary"]

    classification_counts: dict[str, int] = {}
    for row in summary:
        classification = row["classification"]
        classification_counts[classification] = (
            classification_counts.get(classification, 0) + 1
        )

    top_contingencies = []

    for row in summary:
        if row.get("review_rank") is None:
            continue

        top_contingencies.append(
            {
                "rank": row["review_rank"],
                "generator_id": row["element_id"],
                "contingency_id": row["contingency_id"],
                "classification": row["classification"],
                "status": row.get("raw_status"),
                "violated_equipment_count": row.get(
                    "violated_equipment_count",
                    0,
                ),
                "max_loading_percent": row.get(
                    "max_loading_percent"
                ),
                "worst_branch_id": row.get(
                    "worst_branch_id"
                ),
                "primary_violation_equipment_id": row.get(
                    "primary_violation_equipment_id"
                ),
                "primary_violation_type": row.get(
                    "primary_violation_type"
                ),
            }
        )

        if len(top_contingencies) >= top_n:
            break

    return {
        "analysis_type": "Generator Contingency Screening",
        "slack_mode": slack_mode,
        "total_contingencies": len(summary),
        "classification_counts": classification_counts,
        "top_n": top_n,
        "top_contingencies": top_contingencies,
    }