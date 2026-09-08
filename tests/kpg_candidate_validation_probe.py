from pathlib import Path

from ai_ems import (
    generate_redispatch_candidates,
    load_network,
    validate_balanced_redispatch,
)

PROJCECT_ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = PROJCECT_ROOT / "data" / "KPG193_ver2_0_pypowsybl.mat"

network = load_network(CASE_FILE)

candidate_result = generate_redispatch_candidates(
    network,
    outage_line_id="LINE-16-28",
    monitored_line_id="LINE-16-22",
    delta_mw=10.0,
    top_n=3,
)

for candidate in candidate_result["candidates"]:
	result = validate_balanced_redispatch(
			case_path=CASE_FILE,
			outage_line_id="LINE-16-28",
			monitored_line_id="LINE-16-22",
			up_generator_id=candidate["up_generator_id"],
			down_generatorid=candidate["down_generator_id"],
			delta_mw=candidate["delta_mw"],
	)
	
	print()
	print(f"Rank {candidate['rank']}")
	print(
			f"{candidate['up_generator_id']} +{candidate['delta_mw']} MW / "
			f"{candidate['down_generator_id']} -{candidate['delta_mw']} MW"
	)
	print(
			"Predicted active-power reduction:",
			candidate["predicted_abs_p1_reduction_mw"],
			"MW",
	)
	print(
			"AC apparent-power improvement:",
			result["improvement_mva"],
			"MVA",
	)
	print(
			"Loading:",
			result["loading_before_percent"],
			"->",
			result["loading_after_percent"],
			"%",
	)
	print("Violation remaining:", result["violation_remaining"])