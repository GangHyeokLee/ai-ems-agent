from ai_ems import get_network_summary, load_network, run_ac_load_flow

def test_kpg_network_summary():
  network = load_network("data/KPG193_ver2_0_pypowsybl.mat")
  summary = get_network_summary(network)

  assert summary["buses"] == 193
  assert summary["lines"] == 385

def test_kpg_ac_load_flow():
  network = load_network("data/KPG193_ver2_0_pypowsybl.mat")

  result = run_ac_load_flow(network)

  assert result["converged"] is True