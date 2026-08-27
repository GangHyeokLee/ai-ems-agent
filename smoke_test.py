from __future__ import annotations

import argparse
import json

from ai_ems import (
    get_network_summary,
    load_network,
    run_ac_load_flow,
    run_line_contingency,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI-EMS Agent plain PyPowSyBl tool smoke test"
    )
    parser.add_argument("--case", required=True, help="MATPOWER case path")
    parser.add_argument("--outage", required=True, help="outage line id")
    parser.add_argument(
        "--monitor",
        action="append",
        default=[],
        help="monitored line id; repeat for multiple lines",
    )
    args = parser.parse_args()

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
        monitored_line_ids=args.monitor,
    )
    print(json.dumps(contingency, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
