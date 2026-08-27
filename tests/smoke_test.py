from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from ai_ems import (  # noqa: E402
    get_network_summary,
    load_network,
    run_ac_load_flow,
    run_line_contingency,
)


DEFAULT_CASE = PROJECT_ROOT / "data" / "KPG193_ver2_0_pypowsybl.mat"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI-EMS Agent plain PyPowSyBl tool smoke test"
    )
    parser.add_argument(
        "--case",
        default=str(DEFAULT_CASE),
        help="MATPOWER case path",
    )
    parser.add_argument(
        "--outage",
        default="LINE-16-28",
        help="outage line id",
    )
    parser.add_argument(
        "--monitor",
        action="append",
        default=None,
        help="monitored line id; repeat for multiple lines",
    )
    args = parser.parse_args()

    monitored = args.monitor or ["LINE-16-22"]
    network = load_network(args.case)

    print("=== Network Summary ===")
    print(json.dumps(get_network_summary(network), ensure_ascii=False, indent=2))

    print("\n=== AC Load Flow ===")
    loadflow = run_ac_load_flow(network)
    print(json.dumps(loadflow, ensure_ascii=False, indent=2))

    if not loadflow["converged"]:
        raise SystemExit("Base AC load flow did not converge.")

    print("\n=== Line Contingency ===")
    contingency = run_line_contingency(
        network,
        outage_line_id=args.outage,
        monitored_line_ids=monitored,
    )
    print(json.dumps(contingency, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
