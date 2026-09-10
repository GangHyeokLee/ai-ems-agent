import pypowsybl as pp

from ai_ems.config import CASE_FILE
from ai_ems.network import LOADFLOW_PARAMETERS, load_network

network = load_network(CASE_FILE)

contingency_id = "OUT_LINE-16-28"
strategy_id = "REDISPATCH_GEN-19_GEN-36"

analysis = pp.security.create_analysis()

analysis.add_single_element_contingency(
    "LINE-16-28",
    contingency_id,
)

analysis.add_generator_active_power_action(
    "UP_GEN_19",
    "GEN-19#0",
    True,
    10.0,
)

analysis.add_generator_active_power_action(
    "DOWN_GEN_36",
    "GEN-36#0",
    True,
    -10.0,
)

analysis.add_operator_strategy(
    strategy_id,
    contingency_id,
    [
        "UP_GEN_19",
        "DOWN_GEN_36",
    ],
)

result = analysis.run_ac(
    network,
    parameters=LOADFLOW_PARAMETERS,
)

post = result.find_post_contingency_result(
    contingency_id
)

strategy = result.find_operator_strategy_results(
    strategy_id
)

print("=== Post Contingency ===")
print("status:", post.status)
print("violation count:", len(post.limit_violations))

for violation in post.limit_violations:
    print(violation)

print()

print("=== Operator Strategy ===")
print("status:", strategy.status)
print("violation count:", len(strategy.limit_violations))

for violation in strategy.limit_violations:
    print(violation)
