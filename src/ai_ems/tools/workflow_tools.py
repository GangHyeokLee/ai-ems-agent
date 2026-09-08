from pathlib import Path
from typing import Any

from ai_ems.network import load_network
from ai_ems.tools.control_tools import (
    generate_redispatch_candidates,
    validate_balanced_redispatch,
)


def analyze_contingency_response(
    case_path,
    outage_line_id: str,
    monitored_line_id: str,
    delta_mw: float = 10.0,
    top_n: int = 3,
) -> dict:
    network = load_network(case_path)

    candidate_result = generate_redispatch_candidates(
        network,
        outage_line_id=outage_line_id,
        monitored_line_id=monitored_line_id,
        delta_mw=delta_mw,
        top_n=top_n,
    )

    validated_candidates = []

    for candidate in candidate_result["candidats"]:
        validation = validate_balanced_redispatch(
            case_path=case_path,
            outage_line_id=outage_line_id,
            monitored_line_id=monitored_line_id,
            up_generator_id=candidate["up_generator_id"],
            down_generator_id=candidate["down_generator_id"],
            delta_mw=candidate["delta_mw"],
        )

        validated_candidates.append(
            {
                "rank": candidate["rank"],
                "redispatch": {
                    "up_generator_id": candidate["up_generator_id"],
                    "down_generator_id": candidate["down_generator_id"],
                    "delta_mw": candidate["delta_mw"],
                },
                "prediction": {
                    "active_power_change_mw": candidate[
                        "predicted_active_power_change_mw"
                    ],
                    "abs_p1_reduction_mw": candidate["predicted_abs_p1_reduction_mw"],
                },
                "ac_validation": validation,
            }
        )

    validated_candidates.sort(
        key=lambda x: (
            x["ac_validation"]["improvement_mva"]
            if x["ac_validation"]["improvement_mva"] is not None
            else float("-inf")
        ),
        reverse=True,
    )

    for rank, item in enumerate(validated_candidates, start=1):
        item["ac_validation_rank"] = rank

    return {
        "analysis_type": "Contingency Response Analysis",
        "outage_line_id": outage_line_id,
        "monitored_line_id": monitored_line_id,
        "delta_mw": delta_mw,
        "candidate_count": len(validated_candidates),
        "candidates": validated_candidates,
        "best_tested_candidate": (
            validated_candidates[0] if validated_candidates else None
        ),
    }
