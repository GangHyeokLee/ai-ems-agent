from pathlib import Path
from pprint import pprint

from ai_ems.tools.workflow_tools import analyze_contingency_response
from ai_ems.config import CASE_FILE


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_FILE = PROJECT_ROOT / CASE_FILE

result = analyze_contingency_response(
    CASE_FILE,
    "LINE-16-28",
    delta_mw=10.0,
    top_n=3,
)

print("Selected monitored line:", result["monitored_line_id"])
print("Target selection:", result["target_selection"])
print()

print("=== Best Tested Candidate ===")
pprint(result["best_tested_candidate"])

print()
print("=== All Candidates ===")

for candidate in result["candidates"]:
    print(
        "Sensitivity Rank:",
        candidate["rank"],
        "/ AC Rank:",
        candidate["ac_validation_rank"],
    )
    print(
        candidate["redispatch"]["up_generator_id"],
        "+",
        candidate["redispatch"]["delta_mw"],
        "MW /",
        candidate["redispatch"]["down_generator_id"],
        "-",
        candidate["redispatch"]["delta_mw"],
        "MW",
    )
    print(
        "Predicted reduction:",
        candidate["prediction"]["abs_p1_reduction_mw"],
        "MW",
    )
    print(
        "AC improvement:",
        candidate["ac_validation"]["improvement_mva"],
        "MVA",
    )
    print()