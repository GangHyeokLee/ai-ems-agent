from typing import Any

import pypowsybl as pp


def rank_generator_sensitivities(
    network,
    outage_line_id: str,
    monitored_line_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank connected generators by post-contingency branch-flow sensitivity."""

    lines = network.get_lines()

    if outage_line_id not in lines.index:
        raise ValueError(f"Unknown outage line: {outage_line_id}")

    if monitored_line_id not in lines.index:
        raise ValueError(f"Unknown monitored line: {monitored_line_id}")

    generators = network.get_generators()

    connected_generators = generators[
        generators["connected"]
    ]

    generator_ids = list(connected_generators.index)

    contingency_id = f"OUT_{outage_line_id}"
    matrix_id = "GENERATOR_SENSITIVITY"

    analysis = pp.sensitivity.create_ac_analysis()

    analysis.add_single_element_contingency(
        outage_line_id,
        contingency_id,
    )

    analysis.add_postcontingency_branch_flow_factor_matrix(
        branches_ids=[monitored_line_id],
        variables_ids=generator_ids,
        contingencies_ids=[contingency_id],
        matrix_id=matrix_id,
    )

    result = analysis.run(network)

    sensitivity = result.get_sensitivity_matrix(
        matrix_id,
        contingency_id,
    )

    ranked = sensitivity.copy()

    ranked["abs_sensitivity"] = (
        ranked[monitored_line_id].abs()
    )

    ranked = ranked.sort_values(
        "abs_sensitivity",
        ascending=False,
    )

    candidates = []

    for generator_id, row in ranked.head(limit).iterrows():
        candidates.append(
            {
                "generator_id": str(generator_id),
                "sensitivity": float(
                    row[monitored_line_id]
                ),
                "abs_sensitivity": float(
                    row["abs_sensitivity"]
                ),
            }
        )

    return {
        "contingency_id": contingency_id,
        "outage_line_id": outage_line_id,
        "monitored_line_id": monitored_line_id,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }