"""Explicit create/validate/runtime commands; no install, activate, or rollback."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bundle import validate_bundle, validate_runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--version", required=True)
    create.add_argument("--model-source", choices=("historical", "stage3a"), default="historical")
    create.add_argument("--conversion-directory", type=Path)
    for name in ("ems-root", "source-project", "earth-checkout", "work-dir", "output-dir", "bundle-dir", "runtime-python"):
        create.add_argument("--"+name, type=Path)
    for name in ("validate", "runtime"):
        command = sub.add_parser(name)
        command.add_argument("--bundle", type=Path, required=True)
        command.add_argument("--manifest-sha256", required=True, help="Trusted external receipt/approved hash; never read from the bundle itself")
        if name == "runtime":
            command.add_argument("--runtime-python", type=Path, required=True)
            command.add_argument("--ems-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            from .bundle_build import create_bundle
            from .paths import resolve
            result = create_bundle(resolve(args), version=args.version, model_source=args.model_source,
                                   conversion_directory=args.conversion_directory)
        elif args.command == "validate":
            result = validate_bundle(args.bundle, expected_manifest_sha256=args.manifest_sha256)
        else:
            result = validate_runtime(args.bundle, expected_manifest_sha256=args.manifest_sha256,
                                      runtime_python=args.runtime_python, ems_root=args.ems_root)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"passed": False, "activated": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
