from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import sys

import pandas as pd
import pypowsybl as pp

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_ems.config import CASE_FILE
from ai_ems.network import load_network
from ai_ems.tools.generator_contingency_tools import (
    build_generator_loadflow_parameters,
)

GENERATOR_BALANCE_TYPES = [
    "PROPORTIONAL_TO_GENERATION_P_MAX",
    "PROPORTIONAL_TO_GENERATION_P",
    "PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN",
    "PROPORTIONAL_TO_GENERATION_PARTICIPATION_FACTOR",
]


def _print_header(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def inspect_runtime() -> str:
    _print_header("Runtime / Load Flow Provider")

    provider = pp.loadflow.get_default_provider()

    print(f"PyPowSyBl version : {version('pypowsybl')}")
    print(f"Default provider  : {provider}")
    print(f"Case file         : {CASE_FILE}")

    return provider


def inspect_balance_type_enum() -> None:
    _print_header("BalanceType Enum")

    members = pp.loadflow.BalanceType.__members__

    for balance_type in GENERATOR_BALANCE_TYPES:
        print(
            f"{balance_type:<55} "
            f"{'AVAILABLE' if balance_type in members else 'MISSING'}"
        )


def inspect_provider_support(provider: str) -> None:
    _print_header("Provider Parameter Support")

    for balance_type in GENERATOR_BALANCE_TYPES:
        try:
            parameters = build_generator_loadflow_parameters(
                slack_mode="distributed",
                balance_type=balance_type,
            )
            supported = pp.loadflow.check_loadflow_parameters(
                parameters=parameters,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001 - probe should report all modes
            print(f"{balance_type:<55} ERROR: {exc}")
            continue

        print(f"{balance_type:<55} supported={supported}")


def inspect_generator_data(network) -> None:
    _print_header("KPG Generator Data")

    generators = network.get_generators().copy()
    connected = generators[generators["connected"]].copy()

    print(f"Total generators     : {len(generators)}")
    print(f"Connected generators : {len(connected)}")

    for column in ["target_p", "min_p", "max_p"]:
        if column not in connected.columns:
            print(f"{column:<20}: MISSING COLUMN")
            continue

        numeric = pd.to_numeric(connected[column], errors="coerce")
        finite_count = int(numeric.notna().sum())
        print(
            f"{column:<20}: finite={finite_count}/{len(connected)}, "
            f"missing={len(connected) - finite_count}"
        )

    if all(column in connected.columns for column in ["target_p", "min_p", "max_p"]):
        target = pd.to_numeric(connected["target_p"], errors="coerce")
        min_p = pd.to_numeric(connected["min_p"], errors="coerce")
        max_p = pd.to_numeric(connected["max_p"], errors="coerce")

        valid = target.notna() & min_p.notna() & max_p.notna()
        within_limits = valid & (target >= min_p) & (target <= max_p)

        print(
            "target within min/max : "
            f"{int(within_limits.sum())}/{int(valid.sum())} valid generators"
        )


def inspect_active_power_control_extension(network) -> None:
    _print_header("activePowerControl Extension")

    try:
        extension = network.get_extensions("activePowerControl")
    except Exception as exc:  # noqa: BLE001 - probe should report capability
        print(f"Unable to read activePowerControl extension: {exc}")
        return

    if extension.empty:
        print("activePowerControl extension rows: 0")
        print("PARTICIPATION_FACTOR data-ready: False")
        return

    print(f"activePowerControl extension rows: {len(extension)}")
    print(f"columns: {list(extension.columns)}")

    if "participate" in extension.columns:
        participating = extension["participate"].fillna(False).astype(bool)
        print(f"participate=True rows             : {int(participating.sum())}")
    else:
        participating = pd.Series(False, index=extension.index)
        print("participate column                : MISSING")

    if "participation_factor" in extension.columns:
        factors = pd.to_numeric(
            extension["participation_factor"],
            errors="coerce",
        )
        positive = factors.gt(0)
        usable = participating & positive

        print(f"positive participation_factor    : {int(positive.sum())}")
        print(f"participating + positive factor  : {int(usable.sum())}")
        print(f"PARTICIPATION_FACTOR data-ready  : {bool(usable.any())}")
    else:
        print("participation_factor column       : MISSING")
        print("PARTICIPATION_FACTOR data-ready   : False")

    if "droop" in extension.columns:
        droop = pd.to_numeric(extension["droop"], errors="coerce")
        print(f"finite droop rows                 : {int(droop.notna().sum())}")
        if droop.notna().any():
            print(
                "droop range                       : "
                f"{droop.min():.6g} .. {droop.max():.6g}"
            )


def main() -> None:
    provider = inspect_runtime()
    inspect_balance_type_enum()
    inspect_provider_support(provider)

    _print_header("Load KPG-193")
    network = load_network(CASE_FILE)
    print("Network loaded successfully")

    inspect_generator_data(network)
    inspect_active_power_control_extension(network)

    _print_header("Preflight Complete")
    print(
        "Next step: run GEN-124#1 with P_MAX / P / REMAINING_MARGIN, "
        "and include PARTICIPATION_FACTOR only when data-ready."
    )


if __name__ == "__main__":
    main()
