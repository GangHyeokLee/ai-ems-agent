import pandas as pd
import pypowsybl as pp

from math import hypot

from ai_ems.config import CASE_FILE
from ai_ems.network import load_network, run_ac_load_flow

OUTAGE_GEN = "GEN-124#1"


def parameters(mode: str):
    if mode == "distributed":
        return pp.loadflow.Parameters(
            distributed_slack=True,
            balance_type=pp.loadflow.BalanceType.PROPORTIONAL_TO_GENERATION_P_MAX,
        )

    return pp.loadflow.Parameters(
        distributed_slack=False,
    )


def generator_snapshot(network):
    df = network.get_generators().copy()

    df["actual_generation_mw"] = -df["p"]
    df["headroom_mw"] = df["max_p"] - df["actual_generation_mw"]

    return df[
        [
            "connected",
            "bus_id",
            "target_p",
            "actual_generation_mw",
            "min_p",
            "max_p",
            "headroom_mw",
        ]
    ]


TARGET_LINE = "LINE-134-193"
LINE_LIMIT_MVA = 2172.0


def inspect_case(mode: str):
    params = parameters(mode)

    network = load_network(CASE_FILE)

    # GEN-124#1 탈락
    network.update_generators(
        id=OUTAGE_GEN,
        connected=False,
    )

    results = pp.loadflow.run_ac(
        network,
        parameters=params,
    )

    print()
    print("=" * 80)
    print(f"{mode.upper()} DETAIL")
    print("=" * 80)

    for component in results:
        print(f"Status                  : {component.status.name}")
        print(f"Reference bus           : {component.reference_bus_id}")
        print(
            f"Distributed active power: " f"{component.distributed_active_power:.6f} MW"
        )

        for slack in component.slack_bus_results:
            print(f"Slack bus                : {slack.id}")
            print(
                f"Slack active mismatch    : " f"{slack.active_power_mismatch:.6f} MW"
            )

    # LINE-134-193 결과
    line = network.get_lines().loc[TARGET_LINE]

    s1 = hypot(float(line["p1"]), float(line["q1"]))
    s2 = hypot(float(line["p2"]), float(line["q2"]))
    max_mva = max(s1, s2)

    print()
    print(f"{TARGET_LINE}")
    print(f"Side 1 apparent power    : {s1:.3f} MVA")
    print(f"Side 2 apparent power    : {s2:.3f} MVA")
    print(f"Max apparent power       : {max_mva:.3f} MVA")
    print(f"Loading                  : " f"{max_mva / LINE_LIMIT_MVA * 100:.3f}%")

    # Slack bus에 붙어 있는 발전기/부하 확인
    generators = network.get_generators()
    loads = network.get_loads()

    slack_bus_ids = {
        slack.id for component in results for slack in component.slack_bus_results
    }

    print("\nEquipment on slack bus:")

    for slack_id in slack_bus_ids:
        print(f"\n[{slack_id}]")

        gens = generators[generators["bus_id"].astype(str) == str(slack_id)]

        if not gens.empty:
            print("Generators:")
            cols = [
                "target_p",
                "p",
                "min_p",
                "max_p",
                "connected",
            ]
            print(gens[cols].to_string())
        else:
            print("Generators: none")

        bus_loads = loads[loads["bus_id"].astype(str) == str(slack_id)]

        if not bus_loads.empty:
            print("Loads:")
            cols = [c for c in ["p0", "p", "q0", "q"] if c in bus_loads.columns]
            print(bus_loads[cols].to_string())
        else:
            print("Loads: none")


def run_case(mode: str):
    params = parameters(mode)

    # 1. 동일 Slack 설정의 기준상태
    base = load_network(CASE_FILE)
    base_result = run_ac_load_flow(base, parameters=params)

    if not base_result["converged"]:
        raise RuntimeError(f"{mode}: base case did not converge")

    base_gen = generator_snapshot(base)

    # 2. GEN-124#1 탈락
    post = load_network(CASE_FILE)
    post.update_generators(
        id=OUTAGE_GEN,
        connected=False,
    )

    post_result = run_ac_load_flow(post, parameters=params)

    if not post_result["converged"]:
        raise RuntimeError(f"{mode}: post contingency did not converge")

    post_gen = generator_snapshot(post)

    # 3. 사고 전후 발전량 변화
    result = base_gen.add_suffix("_base").join(
        post_gen.add_suffix("_post"),
        how="outer",
    )

    result["delta_generation_mw"] = (
        result["actual_generation_mw_post"] - result["actual_generation_mw_base"]
    )

    result["headroom_before_mw"] = (
        result["max_p_base"] - result["actual_generation_mw_base"]
    )

    result["headroom_after_mw"] = (
        result["max_p_post"] - result["actual_generation_mw_post"]
    )

    lost_generation = float(base_gen.loc[OUTAGE_GEN, "actual_generation_mw"])

    surviving = result.index != OUTAGE_GEN

    positive_compensation = result.loc[
        surviving & (result["delta_generation_mw"] > 1e-3)
    ].copy()

    positive_compensation = positive_compensation.sort_values(
        "delta_generation_mw",
        ascending=False,
    )

    total_compensation = positive_compensation["delta_generation_mw"].sum()

    positive_compensation["share_percent"] = (
        positive_compensation["delta_generation_mw"] / total_compensation * 100
    )

    print()
    print("=" * 80)
    print(mode.upper())
    print("=" * 80)

    print(f"Lost generator       : {OUTAGE_GEN}")
    print(f"Lost generation      : {lost_generation:.3f} MW")
    print(f"Positive compensation: {total_compensation:.3f} MW")
    print(f"Difference           : " f"{total_compensation - lost_generation:+.3f} MW")

    print("\nTop compensation generators:")
    print(
        positive_compensation[
            [
                "bus_id_base",
                "actual_generation_mw_base",
                "delta_generation_mw",
                "actual_generation_mw_post",
                "max_p_base",
                "headroom_before_mw",
                "headroom_after_mw",
                "share_percent",
            ]
        ]
        .head(20)
        .to_string()
    )

    return {
        "mode": mode,
        "lost_generation_mw": lost_generation,
        "total_positive_compensation_mw": total_compensation,
        "table": result,
        "compensation": positive_compensation,
    }


def power_balance_snapshot(network, params, label: str):
    results = pp.loadflow.run_ac(
        network,
        parameters=params,
    )

    generators = network.get_generators()
    loads = network.get_loads()

    # PyPowSyBl generator p는 load convention이므로 발전량은 -p
    connected_gen = generators[generators["connected"] & generators["p"].notna()]

    connected_load = loads[loads["connected"] & loads["p"].notna()]

    total_generation_mw = float((-connected_gen["p"]).sum())

    total_load_mw = float(connected_load["p"].sum())

    slack_mismatch_mw = sum(
        float(slack.active_power_mismatch)
        for component in results
        for slack in component.slack_bus_results
    )

    distributed_active_power_mw = sum(
        float(component.distributed_active_power) for component in results
    )

    # Single slack에서는 slack 보상분이 generator p에 직접 나타나지 않을 수 있으므로
    # 남아 있는 slack mismatch까지 포함해 전력수지를 본다.
    effective_generation_mw = total_generation_mw + slack_mismatch_mw

    effective_loss_mw = effective_generation_mw - total_load_mw

    # AC line에서 직접 계산한 선로 손실
    lines = network.get_lines()

    line_loss_mw = float((lines["p1"] + lines["p2"]).sum())

    print()
    print("-" * 80)
    print(label)
    print("-" * 80)
    print(f"Observed generator output : " f"{total_generation_mw:.3f} MW")
    print(f"Total load                : " f"{total_load_mw:.3f} MW")
    print(f"Distributed active power  : " f"{distributed_active_power_mw:.3f} MW")
    print(f"Slack active mismatch     : " f"{slack_mismatch_mw:.3f} MW")
    print(f"Effective generation      : " f"{effective_generation_mw:.3f} MW")
    print(f"Generation - Load         : " f"{effective_loss_mw:.3f} MW")
    print(f"AC line P loss sum        : " f"{line_loss_mw:.3f} MW")

    return {
        "generation_mw": total_generation_mw,
        "load_mw": total_load_mw,
        "distributed_active_power_mw": distributed_active_power_mw,
        "slack_mismatch_mw": slack_mismatch_mw,
        "effective_generation_mw": effective_generation_mw,
        "effective_loss_mw": effective_loss_mw,
        "line_loss_mw": line_loss_mw,
    }


def inspect_power_balance(mode: str):
    params = parameters(mode)

    # Base
    base = load_network(CASE_FILE)

    base_balance = power_balance_snapshot(
        base,
        params,
        f"{mode.upper()} - BASE",
    )

    # Post-contingency
    post = load_network(CASE_FILE)

    post.update_generators(
        id=OUTAGE_GEN,
        connected=False,
    )

    post_balance = power_balance_snapshot(
        post,
        params,
        f"{mode.upper()} - GEN-124#1 OUTAGE",
    )

    print()
    print(f"=== {mode.upper()} CHANGE ===")

    print(
        "Effective loss change : "
        f"{post_balance['effective_loss_mw'] - base_balance['effective_loss_mw']:+.3f} MW"
    )

    print(
        "AC line loss change   : "
        f"{post_balance['line_loss_mw'] - base_balance['line_loss_mw']:+.3f} MW"
    )


single = run_case("single")
distributed = run_case("distributed")

inspect_case("single")
inspect_case("distributed")

inspect_power_balance("single")
inspect_power_balance("distributed")
