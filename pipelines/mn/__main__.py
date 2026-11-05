"""Inspect Mongolia prerequisites and freeze the existing EMS baseline; never migrate data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .inventory import Checks, compare_files, compare_numerical, load_contract, write_report
from .migration import inspect_migration
from .paths import resolve
from .preflight import assemble


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("preflight", "baseline", "migration"))
    for name in ("ems-root", "source-project", "earth-checkout", "work-dir", "output-dir", "bundle-dir",
                 "runtime-python", "build-python", "conversion-python", "runtime-xiidm", "runtime-reports"):
        result.add_argument("--" + name, type=Path)
    result.add_argument("--pixi", help="Pixi executable; only --version is invoked")
    result.add_argument("--profile", choices=("all", "runtime", "build", "conversion"), default="all")
    result.add_argument("--verify-runtime", action="store_true", help="Run the existing EMS PF/N-1/Sensitivity without saving a model")
    result.add_argument("--verify-against", type=Path, help="Compare every file role and byte hash with a previous baseline")
    result.add_argument("--numerical-reference", type=Path, help="Compare live numerical observations with a recorded baseline; requires --verify-runtime")
    result.add_argument("--test-results", type=Path, action="append", default=[], help="Existing JUnit XML; each run stays separate")
    result.add_argument("--report", type=Path, help="New JSON report (existing differing files are refused)")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        paths = resolve(args)
        if args.numerical_reference and not args.verify_runtime:
            raise ValueError("--numerical-reference에는 --verify-runtime이 필요합니다.")
        if args.command == "baseline" and args.profile != "all":
            raise ValueError("전체 기준선은 --profile all로 기록하세요. 부분 점검은 preflight를 사용하세요.")
        def input_path(path):
            return path if path.is_absolute() else paths.ems_root / path
        if args.report:
            paths.safe_destination(args.report)
        if args.command == "migration":
            report = inspect_migration(paths.source_project)
            code = 0 if report["ready"] else 2
            print(f"이관 분석: {len(report['scripts'])}개 스크립트 / 의존 그룹 {len(report['dependency_groups'])}개")
        else:
            contract = load_contract()
            paths, report = assemble(paths, contract, pixi=args.pixi,
                                     verify_runtime=args.verify_runtime, junit=[input_path(p) for p in args.test_results], profile=args.profile)
            if args.verify_against:
                comparison = Checks()
                compare_files(report["files"], json.loads(input_path(args.verify_against).read_text(encoding="utf-8")), comparison)
                report["checks"].extend(comparison.items)
            if args.numerical_reference:
                comparison = Checks()
                if report["numerical"].get("ok"):
                    compare_numerical(report["numerical"]["result"], json.loads(input_path(args.numerical_reference).read_text(encoding="utf-8")), comparison)
                else:
                    comparison.error("numeric_reference", "기존 모델 수치 실행이 성공하지 않았습니다.")
                report["checks"].extend(comparison.items)
            selected = [c for c in report["checks"] if args.profile == "all" or args.profile in c["scopes"]]
            failures = [c for c in selected if c["passed"] is False]
            report["selected_profile"] = args.profile
            report["ready"] = not failures
            code = 2 if failures else 0
            print(f"몽골 {args.command}: {'PASS' if not failures else 'NOT_READY'} / profile={args.profile}")
            for check in failures:
                detail = check.get('error', str(check.get('actual'))+' != '+str(check.get('expected')))
                print(f"  FAIL {check['name']}: {detail[:240]}")
            for check in report["checks"]:
                if check["passed"] is None:
                    print(f"  참고 {check['name']}: {check['warning']}")
            print("원본·런타임 경로 변경 없음. 모델 구축·변환·번들 활성화는 아직 이관되지 않았습니다.")
        if args.report:
            write_report(paths, args.report, report)
            print(f"보고서: {args.report}")
        return code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"몽골 점검 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
