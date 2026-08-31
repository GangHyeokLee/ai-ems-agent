import pypowsybl as pp

from ai_ems import load_network

network = load_network("data/KPG193_ver2_0_pypowsybl.mat")

# 운전 중인 발전기만 후보로 사용
generators = network.get_generators()
generator_ids = list(
  generators[generators["connected"]].index
)

print("Connected generators:", len(generator_ids))

analysis = pp.sensitivity.create_ac_analysis()

# LINE-16-28 탈락 정의
analysis.add_single_element_contingency(
  "LINE-16-28",
  "OUT_LINE-16-28",
)

# 사고 후 LINE-16-22 조류에 대한 발전기 민감도
analysis.add_postcontingency_branch_flow_factor_matrix(
  branches_ids=["LINE-16-22"],
  variables_ids=generator_ids,
  contingencies_ids=["OUT_LINE-16-28"],
  matrix_id="GEN_TO_LINE_16_22",
)

result = analysis.run(network)

sensitivity = result.get_sensitivity_matrix(
    "GEN_TO_LINE_16_22",
    "OUT_LINE-16-28",
)

print("\n=== Post-contingency Sensitivity ===")
print(sensitivity)

ranked = sensitivity.copy()
ranked["abs_sensitivity"] = ranked["LINE-16-22"].abs()

ranked = ranked.sort_values(
    "abs_sensitivity",
    ascending=False,
)

print("\n=== Top 10 Generators ===")
print(ranked.head(10))

signed = sensitivity["LINE-16-22"].sort_values(
  ascending=False
)

print("\n=== Highest Positive Sensitivity ===")
print(signed.head(5))

print("\n=== Most Negative Sensitivity ===")
print(signed.tail(5))

candidate_ids = (
  list(signed.head(5).index)
  + list(signed.tail(5).index)
)

generators = network.get_generators()

print("\n=== Candidate Generator Limits ===")
print(
  generators.loc[
    candidate_ids,
    [
      "target_p",
      "min_p",
      "max_p",
      "connected",
      "bus_id",
    ],
  ]
)