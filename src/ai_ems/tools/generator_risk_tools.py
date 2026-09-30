from ai_ems.tools.generator_screening_tools import (
    run_generator_contingency_screening,
)
from ai_ems.tools.workflow_tools import analyze_generator_outage_response


def analyze_generator_risk_response(
    case_path,
    slack_mode: str = "distributed",
    screening_top_n: int = 5,
    response_top_n: int = 3,
    delta_mw: float = 10.0,
) -> dict:
    """Screen generator N-1 contingencies and analyze the top-ranked response.

    This is a high-level workflow for one-turn requests such as:
    "find the riskiest generator outage and analyze the response".

    Flow:
    full generator N-1 screening -> select rank 1 contingency ->
    generator-outage corrective-action workflow.

    The screening rank is physics-first review priority. The corrective-action
    result is only the best among tested redispatch candidates and is not an
    OPF/SCED optimum.
    """
    if slack_mode not in {"single", "distributed"}:
        raise ValueError("slack_mode must be 'single' or 'distributed'.")
    if screening_top_n < 1 or screening_top_n > 20:
        raise ValueError("screening_top_n must be between 1 and 20.")
    if response_top_n < 1 or response_top_n > 10:
        raise ValueError("response_top_n must be between 1 and 10.")
    if delta_mw <= 0:
        raise ValueError("delta_mw must be greater than zero.")

    screening = run_generator_contingency_screening(
        case_path=case_path,
        slack_mode=slack_mode,
        top_n=screening_top_n,
    )

    top_contingencies = screening.get("top_contingencies", [])

    common = {
        "analysis_type": "Generator Risk Response Analysis",
        "slack_mode": slack_mode,
        "screening": screening,
        "screening_top_n": screening_top_n,
        "response_top_n": response_top_n,
        "delta_mw": float(delta_mw),
    }

    if not top_contingencies:
        return {
            **common,
            "analysis_status": "NO_SCREENING_RESULT",
            "selected_contingency": None,
            "response": None,
        }

    selected = top_contingencies[0]
    classification = selected.get("classification")

    if classification != "CONVERGED_VIOLATION":
        return {
            **common,
            "analysis_status": "TOP_CONTINGENCY_NOT_REDISPATCHABLE",
            "selected_contingency": selected,
            "response": None,
        }

    generator_id = selected.get("generator_id")
    if not generator_id:
        raise RuntimeError("Top screening result does not contain generator_id.")

    response = analyze_generator_outage_response(
        case_path=case_path,
        outage_generator_id=generator_id,
        slack_mode=slack_mode,
        delta_mw=delta_mw,
        top_n=response_top_n,
    )

    return {
        **common,
        "analysis_status": "COMPLETED",
        "selected_contingency": selected,
        "response": response,
    }
