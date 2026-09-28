from ai_ems import (
  get_network_summary, 
  load_network, 
  run_ac_load_flow,
  run_line_contingency,
  )

def test_kpg_network_summary():
  network = load_network("data/KPG193_ver2_0_pypowsybl.mat")
  summary = get_network_summary(network)

  assert summary["buses"] == 193
  assert summary["lines"] == 385

def test_kpg_ac_load_flow():
  network = load_network("data/KPG193_ver2_0_pypowsybl.mat")

  result = run_ac_load_flow(network)

  assert result["converged"] is True

def test_kpg_bus_voltage_pu():
  network = load_network("data/KPG193_ver2_0_pypowsybl.mat")

  result = run_ac_load_flow(network)

  v_min = result["bus_voltage_pu"]["min"]
  v_max = result["bus_voltage_pu"]["max"]

  assert 0.95 < v_min < 1.0
  assert 1.0  < v_max < 1.05

def test_kpg_line_contingency():
  network = load_network("data/KPG193_ver2_0_pypowsybl.mat")

  result = run_line_contingency(
    network,
    outage_line_id="LINE-16-28",
    monitored_line_ids=["LINE-16-22"],
  )

  assert result["base_converged"] is True
  assert result["post_status"] == "CONVERGED"

  violations = result["limit_violations"]

  has_target_violation = any(
    violation["subject_id"] == "LINE-16-22"
    and violation["limit_type"] == "APPARENT_POWER"
    for violation in violations
  )

  assert has_target_violation is True


def test_load_flow_uses_default_parameters(monkeypatch):
    from ai_ems import network as network_module

    received = {}

    def fake_run_ac(network, *, parameters):
        received["parameters"] = parameters
        return []

    monkeypatch.setattr(
        network_module.pp.loadflow, "run_ac", fake_run_ac
    )

    network_module.run_ac_load_flow(object())

    assert received["parameters"] is network_module.LOADFLOW_PARAMETERS
    assert received["parameters"].distributed_slack is False


def test_load_flow_accepts_custom_parameters(monkeypatch):
    from ai_ems import network as network_module

    received = {}

    def fake_run_ac(network, *, parameters):
        received["parameters"] = parameters
        return []

    monkeypatch.setattr(
        network_module.pp.loadflow, "run_ac", fake_run_ac
    )

    custom = network_module.pp.loadflow.Parameters(
        distributed_slack=True,
        balance_type=(
            network_module.pp.loadflow.BalanceType
            .PROPORTIONAL_TO_GENERATION_P_MAX
        ),
    )

    result = network_module.run_ac_load_flow(
        object(),
        parameters=custom,
    )

    assert received["parameters"] is custom
    assert result["load_flow_parameters"]["distributed_slack"] is True
    assert (
        result["load_flow_parameters"]["balance_type"]
        == "PROPORTIONAL_TO_GENERATION_P_MAX"
    )
    assert network_module.LOADFLOW_PARAMETERS.distributed_slack is False