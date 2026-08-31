from math import hypot

import pypowsybl as pp

from ai_ems import load_network, run_ac_load_flow

CASE = "data/KPG193_ver2_0_pypowsybl.mat"

OUTAGE_LINE = "LINE-16-28"
MONITORED_LINE = "LINE-16-22"

UP_GENERATOR = "GEN-19#0"
DOWN_GENERATOR = "GEN-36#0"
DELTA_MW = 10.0

# ============================================================
# 1. Post-contingency sensitivity
# ============================================================

network = load_network(CASE)

analysis = pp.sensitivity.create_ac_analysis()

analysis.add_single_element_contingency(
  OUTAGE_LINE,
  "OUT_LINE-16-28",
)

analysis.add_postcontingency_branch_flow_factor_matrix(
  branches_ids=[MONITORED_LINE],
  variables_ids=[UP_GENERATOR, DOWN_GENERATOR],
  contingencies_ids=["OUT_LINE-16-28"],
  matrix_id="REDISPATCH",
)

result = analysis.run(network)

sensitivity = result.get_sensitivity_matrix(
  "REDISPATCH",
  "OUT_LINE-16-28",
)

s_up = sensitivity.loc[UP_GENERATOR, MONITORED_LINE]
s_down = sensitivity.loc[DOWN_GENERATOR, MONITORED_LINE]

predicted_change = (
  s_up * DELTA_MW
  + s_down * (-DELTA_MW)
)

print("=== Redispatch Sensitivities ===")
print(sensitivity)
print(f"Predicted p1 change: {predicted_change:.6f} MW")

# ============================================================
# 2. Create actual post-contingency network
# ============================================================

control_network = load_network(CASE)

control_network.update_lines(
  id=OUTAGE_LINE,
  connected1=False,
  connected2=False,
)

post_result = run_ac_load_flow(control_network)

if not post_result["converged"]:
  raise RuntimeError("Post-contingency AC load flow did not converge.")

line = control_network.get_lines().loc[MONITORED_LINE]

post_p1 = float(line["p1"])
post_q1 = float(line["q1"])
post_p2 = float(line["p2"])
post_q2 = float(line["q2"])

post_mva = max(
  hypot(post_p1, post_q1),
  hypot(post_p2, post_q2),
)

print("\n=== Post-contingency State ===")
print(f"p1            : {post_p1:.6f} MW")
print(f"p2            : {post_p2:.6f} MW")
print(f"Apparent power: {post_mva:.6f} MVA")

# ============================================================
# 3. Apply balanced redispatch
# ============================================================

generators = control_network.get_generators()

up_target = float(generators.loc[UP_GENERATOR, "target_p"])
down_target = float(generators.loc[DOWN_GENERATOR, "target_p"])

control_network.update_generators(
  id=[UP_GENERATOR, DOWN_GENERATOR],
  target_p=[
    up_target + DELTA_MW,
    down_target - DELTA_MW,
  ],
)

corrected_result = run_ac_load_flow(control_network)

if not corrected_result["converged"]:
  raise RuntimeError("Redispatch AC load flow did not converge.")

line = control_network.get_lines().loc[MONITORED_LINE]

corrected_p1 = float(line["p1"])
corrected_q1 = float(line["q1"])
corrected_p2 = float(line["p2"])
corrected_q2 = float(line["q2"])

corrected_mva = max(
    hypot(corrected_p1, corrected_q1),
    hypot(corrected_p2, corrected_q2),
)

# ============================================================
# 4. Prediction vs AC validation
# ============================================================

actual_change = corrected_p1 - post_p1
predicted_p1 = post_p1 + predicted_change

print("\n=== Prediction vs Actual ===")
print(f"Post-contingency p1 : {post_p1:.6f} MW")
print(f"Predicted change    : {predicted_change:.6f} MW")
print(f"Actual change       : {actual_change:.6f} MW")
print(f"Predicted p1        : {predicted_p1:.6f} MW")
print(f"Actual p1           : {corrected_p1:.6f} MW")

print("\n=== Apparent Power Validation ===")
print(f"Before redispatch : {post_mva:.6f} MVA")
print(f"After redispatch  : {corrected_mva:.6f} MVA")
print(f"Change            : {corrected_mva - post_mva:.6f} MVA")