from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from scipy.io import loadmat

DCLINE_FIELDS = {
    "F_BUS": 0,
    "T_BUS": 1,
    "BR_STATUS": 2,
    "PF": 3,
    "PT": 4,
    "QF": 5,
    "QT": 6,
    "VF": 7,
    "VT": 8,
    "PMIN": 9,
    "PMAX": 10,
    "QMINF": 11,
    "QMAXF": 12,
    "QMINT": 13,
    "QMAXT": 14,
    "LOSS0": 15,
    "LOSS1": 16,
}


def _decode_matlab_char(values: np.ndarray) -> str:
    flat = np.asarray(values).reshape(-1)
    return "".join(chr(int(code)) for code in flat if int(code) != 0)


def _load_v73_mpc(path: Path) -> dict[str, Any]:
    with h5py.File(path, "r") as handle:
        if "mpc" not in handle:
            raise KeyError(f"{path}: top-level 'mpc' not found")

        group = handle["mpc"]
        if not isinstance(group, h5py.Group):
            raise TypeError(f"{path}: 'mpc' is not an HDF5 group")

        result: dict[str, Any] = {}
        for name, dataset in group.items():
            if not isinstance(dataset, h5py.Dataset):
                raise TypeError(
                    f"{path}: unsupported non-dataset field mpc.{name}"
                )

            matlab_class = dataset.attrs.get("MATLAB_class", b"")
            if isinstance(matlab_class, np.ndarray):
                matlab_class = matlab_class.item()
            if isinstance(matlab_class, np.bytes_):
                matlab_class = bytes(matlab_class)

            raw = np.asarray(dataset)

            if matlab_class == b"char":
                result[name] = _decode_matlab_char(raw)
                continue

            # MATLAB v7.3 numeric matrices are stored in HDF5 with dimensions
            # reversed relative to the MATLAB/Python view.
            if raw.ndim >= 2:
                value = raw.T
            elif raw.size == 1:
                value = raw.reshape(-1)[0].item()
            else:
                value = raw.copy()

            result[name] = value

        return result


def _load_level5_mpc(path: Path) -> dict[str, Any]:
    payload = loadmat(
        path,
        squeeze_me=False,
        struct_as_record=False,
    )
    if "mpc" not in payload:
        raise KeyError(f"{path}: top-level 'mpc' not found")

    container = payload["mpc"]
    if not isinstance(container, np.ndarray) or container.size != 1:
        raise TypeError(f"{path}: unsupported 'mpc' structure")

    mpc = container.reshape(-1)[0]
    fieldnames = getattr(mpc, "_fieldnames", None)
    if not fieldnames:
        raise TypeError(f"{path}: unsupported 'mpc' structure")

    result: dict[str, Any] = {}
    for name in fieldnames:
        value = getattr(mpc, name)
        array = np.asarray(value)

        if array.dtype.kind in "US" and array.size == 1:
            result[name] = str(array.reshape(-1)[0])
        elif array.size == 1 and array.dtype.kind in "biufc":
            result[name] = array.reshape(-1)[0].item()
        else:
            result[name] = value

    return result


def load_mpc(path: Path) -> tuple[dict[str, Any], str]:
    if h5py.is_hdf5(path):
        return _load_v73_mpc(path), "MATLAB v7.3 (HDF5)"
    return _load_level5_mpc(path), "MATLAB Level-5/v7"


def normalize_dcline_orientation(dcline: np.ndarray) -> np.ndarray:
    rows = np.asarray(dcline, dtype=float)
    if rows.ndim == 1:
        rows = rows.reshape(1, -1)
    if rows.shape[1] != 17:
        raise ValueError(
            f"dcline must have 17 columns, got shape {rows.shape}"
        )

    normalized = rows.copy()

    for i in range(len(normalized)):
        old = normalized[i].copy()

        if old[DCLINE_FIELDS["PF"]] >= 0.0:
            continue

        normalized[i, DCLINE_FIELDS["F_BUS"]] = old[DCLINE_FIELDS["T_BUS"]]
        normalized[i, DCLINE_FIELDS["T_BUS"]] = old[DCLINE_FIELDS["F_BUS"]]

        normalized[i, DCLINE_FIELDS["PF"]] = -old[DCLINE_FIELDS["PT"]]
        normalized[i, DCLINE_FIELDS["PT"]] = -old[DCLINE_FIELDS["PF"]]

        normalized[i, DCLINE_FIELDS["QF"]] = old[DCLINE_FIELDS["QT"]]
        normalized[i, DCLINE_FIELDS["QT"]] = old[DCLINE_FIELDS["QF"]]

        normalized[i, DCLINE_FIELDS["VF"]] = old[DCLINE_FIELDS["VT"]]
        normalized[i, DCLINE_FIELDS["VT"]] = old[DCLINE_FIELDS["VF"]]

        normalized[i, DCLINE_FIELDS["PMIN"]] = -old[DCLINE_FIELDS["PMAX"]]
        normalized[i, DCLINE_FIELDS["PMAX"]] = -old[DCLINE_FIELDS["PMIN"]]

        normalized[i, DCLINE_FIELDS["QMINF"]] = old[DCLINE_FIELDS["QMINT"]]
        normalized[i, DCLINE_FIELDS["QMAXF"]] = old[DCLINE_FIELDS["QMAXT"]]
        normalized[i, DCLINE_FIELDS["QMINT"]] = old[DCLINE_FIELDS["QMINF"]]
        normalized[i, DCLINE_FIELDS["QMAXT"]] = old[DCLINE_FIELDS["QMAXF"]]

        # BR_STATUS, LOSS0 and LOSS1 are intentionally preserved.

    return normalized


def _shape(value: Any) -> str:
    array = np.asarray(value)
    return "scalar" if array.ndim == 0 else "x".join(map(str, array.shape))


def compare_values(
    expected: Any,
    actual: Any,
    *,
    atol: float,
    rtol: float,
) -> tuple[bool, str]:
    expected_array = np.asarray(expected)
    actual_array = np.asarray(actual)

    if expected_array.shape != actual_array.shape:
        return False, f"shape {expected_array.shape} != {actual_array.shape}"

    if (
        expected_array.dtype.kind in "biufc"
        and actual_array.dtype.kind in "biufc"
    ):
        equal = np.allclose(
            expected_array,
            actual_array,
            rtol=rtol,
            atol=atol,
            equal_nan=True,
        )

        difference = np.abs(
            expected_array.astype(float) - actual_array.astype(float)
        )
        max_abs_diff = (
            float(np.nanmax(difference)) if difference.size else 0.0
        )
        return equal, f"max_abs_diff={max_abs_diff:.3e}"

    equal = np.array_equal(expected_array, actual_array)
    return equal, "exact" if equal else "value mismatch"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate KPG-193 original MAT against the PyPowSyBl full MAT. "
            "All mpc fields must be preserved; dcline is compared after the "
            "documented orientation normalization."
        )
    )
    parser.add_argument(
        "--original",
        required=True,
        type=Path,
        help="Original KPG MAT file (v7.3/HDF5 supported).",
    )
    parser.add_argument(
        "--full",
        required=True,
        type=Path,
        help="KPG PyPowSyBl full MAT file.",
    )
    parser.add_argument(
        "--atol",
        type=float,
        default=0.0,
        help="Absolute tolerance for numeric comparison (default: 0).",
    )
    parser.add_argument(
        "--rtol",
        type=float,
        default=0.0,
        help="Relative tolerance for numeric comparison (default: 0).",
    )
    args = parser.parse_args()

    for path in (args.original, args.full):
        if not path.is_file():
            print(f"[ERROR] file not found: {path}", file=sys.stderr)
            return 2

    original, original_format = load_mpc(args.original)
    full, full_format = load_mpc(args.full)

    print("=== KPG MAT Validation ===")
    print(f"Original : {args.original} [{original_format}]")
    print(f"Full     : {args.full} [{full_format}]")
    print()

    original_fields = set(original)
    full_fields = set(full)

    missing = sorted(original_fields - full_fields)
    extra = sorted(full_fields - original_fields)

    if not missing and not extra:
        print(f"[PASS] mpc fields ({len(original_fields)}): identical set")
    else:
        print("[FAIL] mpc field set differs")
        if missing:
            print("       missing in full:", ", ".join(missing))
        if extra:
            print("       extra in full  :", ", ".join(extra))

    all_ok = not missing and not extra

    print()
    print("=== Field Comparison ===")
    for name in sorted(original_fields & full_fields):
        expected = original[name]
        note = ""

        if name == "dcline":
            expected = normalize_dcline_orientation(
                np.asarray(original[name], dtype=float)
            )
            note = " (orientation-normalized)"

        ok, detail = compare_values(
            expected,
            full[name],
            atol=args.atol,
            rtol=args.rtol,
        )
        status = "PASS" if ok else "FAIL"
        print(
            f"[{status}] {name:<11} "
            f"original={_shape(original[name]):<8} "
            f"full={_shape(full[name]):<8} "
            f"{detail}{note}"
        )
        all_ok = all_ok and ok

    print()
    if all_ok:
        print("RESULT: PASS")
        print(
            "All mpc fields are preserved. Non-dcline fields match under "
            f"atol={args.atol:g}, rtol={args.rtol:g}; dcline matches the "
            "documented orientation normalization."
        )
        return 0

    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
