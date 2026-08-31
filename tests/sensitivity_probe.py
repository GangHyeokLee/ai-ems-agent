import pypowsybl as pp

network =  pp.network.create_ieee14()

pp.loadflow.run_ac(network)

analysis = pp.sensitivity.create_ac_analysis()

generator_ids = list(network.get_generators().index)

analysis.add_branch_flow_factor_matrix(
  branches_ids=["L1-2-1"],
  variables_ids=generator_ids,
  matrix_id="GEN_TO_L12",
)

result = analysis.run(network)

sensitivity  = result.get_sensitivity_matrix("GEN_TO_L12")

print("\n=== Sensitivity Matrix ===")
print(sensitivity)

ranked  = sensitivity.copy()
ranked["abs_sensitivity"] = ranked["L1-2-1"].abs()

ranked = ranked.sort_values(
  "abs_sensitivity",
  ascending=False,
)

print("\n=== Rankend Generators ===")
print(ranked)