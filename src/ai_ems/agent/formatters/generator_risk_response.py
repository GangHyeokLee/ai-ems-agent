from typing import Any

from .generator_response import format_generator_outage_response
from .generator_screening import format_generator_contingency_screening


def format_generator_risk_response(
    result: dict[str, Any],
) -> str:
    """Format screening + top-ranked generator corrective-action analysis."""
    screening = result.get("screening", {})
    selected = result.get("selected_contingency")
    response = result.get("response")
    status = result.get("analysis_status")

    lines = [
        "## 발전기 N-1 위험사고 및 대응방안 통합 분석",
        "",
        format_generator_contingency_screening(screening),
    ]

    if selected is None:
        lines.extend(
            [
                "",
                "### 대응방안 분석",
                "",
                "- Screening 결과에서 후속 분석할 발전기 사고를 선택하지 못했습니다.",
            ]
        )
        return "\n".join(lines)

    generator_id = selected.get("generator_id", "-")
    classification = selected.get("classification", "-")
    loading = selected.get("max_loading_percent")
    loading_text = f"{loading:.2f}%" if loading is not None else "-"

    lines.extend(
        [
            "",
            "### 대응방안 분석 대상",
            "",
            f"- **Screening 1위 발전기 사고**: {generator_id}",
            f"- **분류**: {classification}",
            f"- **Screening 최대 부하율**: {loading_text}",
        ]
    )

    if status == "TOP_CONTINGENCY_NOT_REDISPATCHABLE":
        lines.extend(
            [
                "- Screening 1위 사고가 수렴된 과부하 사고가 아니므로 Redispatch 대응방안 분석을 수행하지 않았습니다.",
                "- Screening 순위는 우선 검토 순위이며 모든 상위 사고가 Redispatch 검토 대상이라는 의미는 아닙니다.",
            ]
        )
        return "\n".join(lines)

    if response is None:
        lines.append("- 대응방안 분석 결과를 얻지 못했습니다.")
        return "\n".join(lines)

    lines.extend(
        [
            "",
            format_generator_outage_response(response),
        ]
    )

    return "\n".join(lines)
