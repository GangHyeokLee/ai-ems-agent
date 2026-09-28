from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ai_ems.config import CASE_FILE  # noqa: E402
from studies.kpg193_full_batch.reporting import write_study_outputs  # noqa: E402
from studies.kpg193_full_batch.runner import FullBatchStudy, StudyConfig  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the KPG-193 full-batch N-1 Security Analysis study."
    )
    parser.add_argument("--case", type=Path, default=CASE_FILE)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "outputs"
        / "kpg193_full_batch"
        / datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    parser.add_argument("--legacy-csv", type=Path)
    contingency_group = parser.add_mutually_exclusive_group()
    contingency_group.add_argument(
        "--include-generators",
        action="store_true",
        help="Run line N-1 and generator N-1 together.",
    )
    contingency_group.add_argument(
        "--generator-only",
        action="store_true",
        help="Run only connected-generator N-1 contingencies.",
    )

    parser.add_argument(
        "--generator-slack",
        choices=("single", "distributed"),
        default="single",
        help=(
            "Active-power balancing method for generator-only N-1. "
            "Distributed mode uses generator maximum active power."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=0,
        help="0 runs all contingencies in one Security Analysis batch.",
    )
    parser.add_argument("--top-n-sensitivity", type=int, default=10)
    parser.add_argument("--sensitivity-candidates", type=int, default=10)
    parser.add_argument("--no-sensitivity", action="store_true")
    args = parser.parse_args()
    if args.generator_slack == "distributed" and not args.generator_only:
        parser.error(
            "--generator-slack distributed requires --generator-only"
        )

    config = StudyConfig(
        case_file=args.case.expanduser().resolve(),
        output_dir=args.output.expanduser().resolve(),
        include_generators=args.include_generators,
        generator_only=args.generator_only,
        generator_slack=args.generator_slack,
        chunk_size=args.chunk_size,
        top_n_sensitivity_contingencies=args.top_n_sensitivity,
        sensitivity_candidates_per_contingency=args.sensitivity_candidates,
        run_sensitivity=not args.no_sensitivity,
    )
    print(f"[START] case={config.case_file}")
    if config.generator_only:
        mode = (
            "generator N-1 only"
            f"; slack={config.generator_slack}"
        )
    elif config.include_generators:
        mode = "line N-1 + generator N-1; slack=single"
    else:
        mode = "line N-1; slack=single"

    mode += (
        "; single full batch"
        if config.chunk_size <= 0
        else f"; chunk={config.chunk_size}"
    )
    print(f"[MODE] {mode}")
    result = FullBatchStudy(config).run()
    paths = write_study_outputs(result, config.output_dir, args.legacy_csv)

    counts: dict[str, int] = {}
    for row in result["summary"]:
        name = str(row["classification"])
        counts[name] = counts.get(name, 0) + 1
    print(f"[DONE] {counts}")
    for name, path in paths.items():
        print(f"[{name}] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
