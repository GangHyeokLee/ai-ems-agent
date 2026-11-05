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


# --- PH4-02B3-2: answers for v1 runs (verified execution or a fixed failure notice) ---

GUARD_REJECTIONS = frozenset({
    "MULTIPLE_TOOL_CALLS", "NO_TOOL_CALL", "TOOL_NOT_ALLOWED", "INVALID_TOOL_CALL_ID",
    "UNEXPECTED_TOOL_SEQUENCE", "INVALID_ARGUMENTS", "UNEXPECTED_ARGUMENT", "OUTAGE_LINE_MISMATCH",
    "MONITORED_LINE_NOT_ALLOWED", "AUTO_SELECTION_REPLACED", "MONITORED_LINE_MISMATCH", "TOP_N_NOT_ALLOWED",
})
FAILURE_NOTICES = {
    "TOOL_NOT_EXECUTED": "실제 분석 Tool이 실행되지 않아 새 계산 결과가 없습니다. AI 답변 문장은 계산 근거로 쓰지 않습니다.",
    "MODEL_REVISION_CHANGED": ("계산 전후 모델 상태가 달라(예: 새로 로드한 모델의 최초 AC) 이번 결과를 근거로 승인하지 않았습니다. "
                               "자동으로 다시 계산하지 않았습니다. 같은 분석을 새 요청으로 다시 실행하면 현재 상태에서 계산합니다."),
    "MODEL_IDENTITY_UNAVAILABLE": "실제 로드 모델을 식별할 수 없어 결과를 승인하지 않았습니다.",
    "ANALYSIS_BUSY": "다른 계산이 같은 모델을 사용 중이어서 결과를 승인하지 않았습니다. 잠시 후 새 요청으로 다시 실행하세요.",
    "TOOL_INPUT_REJECTED": "선로 ID 등 입력을 모델에서 확인할 수 없어 계산하지 못했습니다.",
    "CHAIN_INCOMPLETE": "N-1 다음 민감도 단계가 실행되지 않아 부분 결과를 공개하지 않았습니다.",
}
GENERIC_FAILURE = "실행 근거 검증을 통과하지 못해 결과를 공개하지 않았습니다."


def failure_notice(reason_code: str | None) -> str:
    if reason_code in GUARD_REJECTIONS:
        return "AI가 고정된 분석 조건과 다른 Tool 호출을 시도해 실행 전에 거부했습니다. 계산하지 않았습니다."
    return FAILURE_NOTICES.get(reason_code, GENERIC_FAILURE)


def format_run_answer(results: list[tuple[dict[str, Any], dict[str, Any]]], status: str,
                      reason_code: str | None, operation: str) -> str:
    """Explain one v1 run from verified (receipt, canonical payload) pairs, or say why none exist."""
    if not results:
        return "\n".join([f"분석 실행 결과를 공개하지 않습니다({reason_code}).", failure_notice(reason_code),
                          "기존 지도와 분석 패널은 바뀌지 않습니다."])
    lines = ["이번 요청에서 검증된 Tool 실행으로 계산한 결과입니다."]
    for receipt, payload in results:
        lines.append("")
        lines += (_security_lines if receipt["kind"] == "security" else _sensitivity_lines)(receipt, payload)
    if operation == "security_then_sensitivity" and len(results) == 1:
        lines += ["", f"N-1 결과가 미평가({reason_code})여서 민감도는 실행하지 않았습니다. 후보·순위가 없습니다."]
    lines += ["", "KPG-193 합성 시험계통의 계산 결과이며 실제 한국 운영계통 결과가 아닙니다."]
    return "\n".join(lines)
