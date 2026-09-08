import json
from typing import Any


def format_contingency_response(result: dict[str, Any]) -> str:
    monitored_line_id = result["monitored_line_id"]
    candidate_count = result["candidate_count"]
    best = result["best_tested_candidate"]

    if best is None:
        return (
            f"{result['outage_line_id']} 사고를 분석한 결과, "
            f"{monitored_line_id}가 주요 위반 선로로 선택되었지만 "
            "검토 가능한 Redispatch 후보를 찾지 못했습니다."
        )

    redispatch = best["redispatch"]
    validation = best["ac_validation"]

    before_mva = validation["post_contingency"]["apparent_power_mva"]
    after_mva = validation["after_redispatch"]["apparent_power_mva"]
    improvement_mva = validation["improvement_mva"]

    before_loading = validation["loading_before_percent"]
    after_loading = validation["loading_after_percent"]
    loading_change = before_loading - after_loading

    lines = [
        f"{result['outage_line_id']} 사고에 대한 대응방안 분석 결과입니다.",
        "",
        f"- 가장 심한 위반 선로: {monitored_line_id}",
        f"- 검토한 Redispatch 후보: {candidate_count}개",
        (
            f"- 시험 후보 중 1순위: "
            f"{redispatch['up_generator_id']} +{redispatch['delta_mw']:.1f} MW / "
            f"{redispatch['down_generator_id']} -{redispatch['delta_mw']:.1f} MW"
        ),
        (
            f"- AC 조류계산 결과 피상전력: "
            f"{before_mva:.2f} MVA → {after_mva:.2f} MVA "
            f"(약 {improvement_mva:.2f} MVA 감소)"
        ),
        (
            f"- 부하율: {before_loading:.2f}% → {after_loading:.2f}% "
            f"(약 {loading_change:.2f}%p 감소)"
        ),
    ]

    if validation["violation_remaining"]:
        lines.append(
            "- 결과: 과부하는 완화되었지만 위반은 여전히 남아 있습니다."
        )
        lines.append(
            "- 추가 제어 후보 또는 제어량을 생성한 뒤 "
            "다시 물리해석으로 검증할 필요가 있습니다."
        )
    else:
        lines.append(
            "- 결과: 시험한 조치에서 해당 선로의 과부하가 해소되었습니다."
        )

    lines.append(
        "- 주의: 현재 결과는 시험한 후보 중 최선의 결과이며 최적 Redispatch를 의미하지 않습니다."
    )

    return "\n".join(lines)