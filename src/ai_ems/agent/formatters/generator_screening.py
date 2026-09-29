from typing import Any


def format_generator_contingency_screening(
    result: dict[str, Any],
) -> str:
    slack_mode = result.get("slack_mode", "-")
    total = result.get("total_contingencies", 0)
    counts = result.get("classification_counts", {})
    top = result.get("top_contingencies", [])

    clean = counts.get("CONVERGED_CLEAN", 0)
    violation = counts.get("CONVERGED_VIOLATION", 0)
    islanded = counts.get("ISLANDED", 0)
    non_converged = counts.get("NON_CONVERGED", 0)
    execution_error = counts.get("EXECUTION_ERROR", 0)

    lines = [
        "## 발전기 N-1 Screening 결과",
        "",
        f"- **Load Flow balancing 가정**: {slack_mode}",
        f"- **분석 발전기 수**: {total}개",
        f"- **정상 수렴 / 위반 없음**: {clean}건",
        f"- **수렴 / 위반 발생**: {violation}건",
    ]

    if islanded:
        lines.append(f"- **계통 분리**: {islanded}건")
    if non_converged:
        lines.append(f"- **비수렴**: {non_converged}건")
    if execution_error:
        lines.append(f"- **실행 오류**: {execution_error}건")

    lines.extend(["", "### 우선 검토 대상", ""])

    if not top:
        lines.append("우선 검토 대상으로 분류된 발전기 사고가 없습니다.")
    else:
        lines.extend(
            [
                "| 순위 | 발전기 | 분류 | 사고 후 위반 설비 | 최대 부하율 |",
                "|---:|---|---|---|---:|",
            ]
        )

        for item in top:
            loading = item.get("max_loading_percent")
            loading_text = f"{loading:.2f}%" if loading is not None else "-"
            violation_equipment = (
                item.get("primary_violation_equipment_id")
                or item.get("worst_branch_id")
                or "-"
            )

            lines.append(
                "| "
                f"{item.get('rank', '-')} | "
                f"{item.get('generator_id', '-')} | "
                f"{item.get('classification', '-')} | "
                f"{violation_equipment} | "
                f"{loading_text} |"
            )

    lines.extend(
        [
            "",
            "### 해석",
            "",
            "- 위 순위는 Batch Security Analysis 결과의 물리적 위반 정도를 기준으로 한 우선 검토 순위입니다.",
            "- `CONVERGED_VIOLATION`은 계산은 수렴했지만 사고 후 한계 위반 설비가 확인되었다는 의미입니다.",
            "- 최대 부하율은 설비 한계 대비 사고 후 Loading을 나타냅니다.",
            "- 이 Screening은 위험 사고 후보를 선별하기 위한 단계이며, 교정제어를 의미하지 않습니다.",
            "- Single/Distributed slack은 Load Flow balancing 가정이며 운영자 Redispatch가 아닙니다.",
            "- 이 결과는 정적 AC Security Analysis 결과이며 동특성 안정도를 검증한 것은 아닙니다.",
        ]
    )

    return "\n".join(lines)
