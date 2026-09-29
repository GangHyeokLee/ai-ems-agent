from typing import Any

import pypowsybl as pp


def rank_generator_sensitivities(
    network,
    outage_line_id: str,
    monitored_line_id: str,
    top_n: int = 10,
) -> dict[str, Any]:
    """Rank connected generators by post-line-contingency flow sensitivity."""

    lines = network.get_lines()

    if outage_line_id not in lines.index:
        raise ValueError(f"Unknown outage line: {outage_line_id}")

    result = _rank_generator_sensitivities_after_contingency(
        network=network,
        outage_element_id=outage_line_id,
        monitored_line_id=monitored_line_id,
        contingency_id=f"OUT_{outage_line_id}",
        top_n=top_n,
    )
    result["outage_line_id"] = outage_line_id
    return result


def rank_generator_sensitivities_after_generator_outage(
    network,
    outage_generator_id: str,
    monitored_line_id: str,
    loadflow_parameters: pp.loadflow.Parameters,
    top_n: int = 10,
) -> dict[str, Any]:
    """Rank surviving generators by post-generator-outage flow sensitivity.

    The sensitivity calculation uses the supplied Load Flow parameters so the
    post-contingency linearization is evaluated under the same balancing
    assumption used for the generator-outage analysis. The tripped generator is
    excluded from the sensitivity variables.
    """

    generators = network.get_generators()

    if outage_generator_id not in generators.index:
        raise ValueError(f"Unknown outage generator: {outage_generator_id}")

    if not bool(generators.loc[outage_generator_id, "connected"]):
        raise ValueError(
            f"Generator is already disconnected: {outage_generator_id}"
        )

    result = _rank_generator_sensitivities_after_contingency(
        network=network,
        outage_element_id=outage_generator_id,
        monitored_line_id=monitored_line_id,
        contingency_id=f"GEN_OUT_{outage_generator_id}",
        top_n=top_n,
        excluded_generator_ids={outage_generator_id},
        loadflow_parameters=loadflow_parameters,
    )
    result["outage_generator_id"] = outage_generator_id
    return result


def _rank_generator_sensitivities_after_contingency(
    network,
    outage_element_id: str,
    monitored_line_id: str,
    contingency_id: str,
    top_n: int,
    excluded_generator_ids: set[str] | None = None,
    loadflow_parameters: pp.loadflow.Parameters | None = None,
) -> dict[str, Any]:
    if top_n <= 0:
        raise ValueError("top_n must be greater than zero.")

    lines = network.get_lines()

    if monitored_line_id not in lines.index:
        raise ValueError(f"Unknown monitored line: {monitored_line_id}")

    generators = network.get_generators()
    connected_generators = generators[generators["connected"]]

    excluded = excluded_generator_ids or set()
    generator_ids = [
        str(generator_id)
        for generator_id in connected_generators.index
        if str(generator_id) not in excluded
    ]

    if not generator_ids:
        raise ValueError("No connected generator is available for sensitivity analysis.")

    matrix_id = "GENERATOR_SENSITIVITY"

    analysis = pp.sensitivity.create_ac_analysis()

    analysis.add_single_element_contingency(
        outage_element_id,
        contingency_id,
    )

    analysis.add_postcontingency_branch_flow_factor_matrix(
        branches_ids=[monitored_line_id],
        variables_ids=generator_ids,
        contingencies_ids=[contingency_id],
        matrix_id=matrix_id,
    )

    if loadflow_parameters is None:
        result = analysis.run(network)
    else:
        sensitivity_parameters = pp.sensitivity.Parameters(
            load_flow_parameters=loadflow_parameters,
        )
        result = analysis.run(
            network,
            parameters=sensitivity_parameters,
        )

    sensitivity = result.get_sensitivity_matrix(
        matrix_id,
        contingency_id,
    )

    ranked = sensitivity.copy()
    ranked["abs_sensitivity"] = ranked[monitored_line_id].abs()
    ranked = ranked.sort_values(
        "abs_sensitivity",
        ascending=False,
    )

    candidates = []

    for generator_id, row in ranked.head(top_n).iterrows():
        candidates.append(
            {
                "generator_id": str(generator_id),
                "sensitivity": float(row[monitored_line_id]),
                "abs_sensitivity": float(row["abs_sensitivity"]),
            }
        )

    return {
        "contingency_id": contingency_id,
        "monitored_line_id": monitored_line_id,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
