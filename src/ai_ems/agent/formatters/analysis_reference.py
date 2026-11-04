"""PH4-02B3-1 deterministic explanation of server-registered KPG analysis results.

Every number comes from the stored canonical REST payload; nothing is computed,
estimated or taken from a model. Unevaluated results get no ranking or verdict.
"""
from typing import Any

MAX_LISTED_VIOLATIONS = 10
SELECTION_LABELS = {"auto": "자동 선택", "explicit": "사용자 지정"}


def _number(value: Any, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _violation_text(item: dict[str, Any]) -> str:
    unit = item.get("unit") or ""
    text = (f"{item.get('equipment_id')} {item.get('limit_type')}: "
            f"계산값 {_number(item.get('value'))} {unit} / 한계 {_number(item.get('limit'))} {unit}")
    if item.get("loading_percent") is not None:
        text += f" (부하율 {_number(item['loading_percent'])}%)"
    if item.get("violation_direction") == "below_minimum":
        text += " (하한 미달)"
    return text


def _security_lines(receipt: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    lines = [f"[N-1 Security] 사고 선로 {payload['outage_line_id']} · 계산 {receipt['created_at']} "
             f"· 결과 ID {receipt['result_id']}"]
    if payload.get("violation_assessment_available") is not True:
        return lines + [f"- 사고 후 PF {payload.get('post_status')}: 수렴하지 않아 위반 판정을 할 수 없습니다(미평가)."]
    count = payload["violated_equipment_count"]
    lines.append(f"- 사고 후 PF {payload['post_status']}, 한계 위반 설비 {count}건(정적 AC 해석 기준).")
    lines += [f"  - {_violation_text(item)}" for item in payload["violations"][:MAX_LISTED_VIOLATIONS]]
    if count > MAX_LISTED_VIOLATIONS:
        lines.append(f"  - 외 {count - MAX_LISTED_VIOLATIONS}건")
    return lines


def _sensitivity_lines(receipt: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    lines = [f"[민감도] 사고 선로 {payload['outage_line_id']} · 계산 {receipt['created_at']} "
             f"· 결과 ID {receipt['result_id']}"]
    if payload.get("analysis_status") != "COMPLETED":
        return lines + [f"- {payload.get('analysis_status')}: {payload.get('message', '')} 후보 0개, 순위 없음(미평가)."]
    selection = SELECTION_LABELS.get(receipt.get("monitored_selection"), "-")
    lines.append(f"- 감시 선로 {payload['monitored_line_id']}({selection}), 후보 {payload['candidate_count']}개.")
    for rank, candidate in enumerate(payload["candidates"], start=1):
        place = f"모선 {candidate.get('bus_id')}"
        if candidate.get("name_korean"):
            place += f", {candidate['name_korean']}"
        lines.append(f"  {rank}. {candidate['generator_id']} ({place}): 민감도 {_number(candidate['sensitivity'], 6)} MW/MW")
    lines.append("- 발전기 주입 1 MW 변화에 대한 감시 선로 유효전력 조류 변화의 국부 선형 근사이며, "
                 "재급전 권고나 최적해가 아닙니다.")
    return lines


def format_reference_answer(results: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    """Explain (receipt, canonical payload) pairs in their reference order."""
    lines = ["저장된 서버 분석 결과를 설명합니다. 이번 요청에서 새 계산은 수행하지 않았습니다."]
    for receipt, payload in results:
        lines.append("")
        lines += (_security_lines if receipt["kind"] == "security" else _sensitivity_lines)(receipt, payload)
    lines += ["", "KPG-193 합성 시험계통의 저장된 계산 결과이며 실제 한국 운영계통 결과가 아닙니다."]
    return "\n".join(lines)
