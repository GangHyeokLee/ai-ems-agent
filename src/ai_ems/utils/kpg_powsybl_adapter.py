"""KPG-193 -> PyPowSyBl adapter that keeps the shared KPG MAT file unchanged.

The KPG v2.0 MATLAB-v7 case contains two parallel MATPOWER ``dcline`` rows.
Loading them directly with PowSyBl currently fails because:

1. KPG represents reverse HVDC flow with a negative ``PF`` value, while IIDM
   expects a non-negative HVDC active-power setpoint and represents direction
   separately with ``ConvertersMode``.
2. PowSyBl's MATPOWER importer derives HVDC/VSC IDs only from the from/to bus
   pair, so the two parallel 51-53 DC lines collide.

This adapter does not modify PowSyBl and does not rewrite the shared source
file. It imports the AC part through PowSyBl's MATPOWER importer using a
short-lived temporary MAT file with ``dcline``/``dclinecost`` emptied, then
re-creates the original DC lines through the public PyPowSyBl network API with
explicit unique IDs.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pypowsybl as pp
from scipy.io import loadmat, savemat


# MATPOWER dcline columns (0-based Python indices)
F_BUS = 0
T_BUS = 1
BR_STATUS = 2
PF = 3
PT = 4
QF = 5
QT = 6
VF = 7
VT = 8
PMIN = 9
PMAX = 10
QMINF = 11
QMAXF = 12
QMINT = 13
QMAXT = 14
LOSS0 = 15
LOSS1 = 16

TOL = 1e-9


def _get_mpc_field(mpc: np.ndarray, name: str):
    if mpc.dtype.names is None or name not in mpc.dtype.names:
        raise KeyError(f"mpc.{name} field not found")
    return mpc[name][0, 0]


def _copy_mpc(mpc: np.ndarray) -> np.ndarray:
    copied = mpc.copy()
    for field_name in mpc.dtype.names or ():
        value = mpc[field_name][0, 0]
        copied[field_name][0, 0] = (
            value.copy() if isinstance(value, np.ndarray) else value
        )
    return copied


def _create_ac_only_temp_case(
    source_payload: dict,
    temp_case_path: Path,
) -> None:
    """Write a temporary MATPOWER case with only dcline data suppressed.

    Every other top-level variable and every other mpc field is preserved.
    The source file itself is never modified.
    """

    payload = {
        key: value
        for key, value in source_payload.items()
        if not key.startswith("__")
    }

    if "mpc" not in payload:
        raise KeyError("Top-level 'mpc' struct not found")

    mpc = _copy_mpc(payload["mpc"])
    fields = tuple(mpc.dtype.names or ())

    if "dcline" not in fields:
        raise KeyError("mpc.dcline field not found")

    original_dcline = np.asarray(_get_mpc_field(mpc, "dcline"))
    column_count = original_dcline.shape[1] if original_dcline.ndim == 2 else 17
    mpc["dcline"][0, 0] = np.empty((0, column_count), dtype=float)

    if "dclinecost" in fields:
        original_cost = np.asarray(_get_mpc_field(mpc, "dclinecost"))
        cost_columns = (
            original_cost.shape[1]
            if original_cost.ndim == 2 and original_cost.shape[1] > 0
            else 0
        )
        mpc["dclinecost"][0, 0] = np.empty((0, cost_columns), dtype=float)

    payload["mpc"] = mpc

    savemat(
        temp_case_path,
        payload,
        format="5",
        do_compression=False,
        long_field_names=True,
    )


def _bus_context(network: pp.network.Network, bus_number: int) -> tuple[str, str, float]:
    bus_id = f"BUS-{bus_number}"
    # Creation APIs require configured bus IDs, not calculated bus-view IDs.
    buses = network.get_bus_breaker_view_buses()

    if bus_id not in buses.index:
        raise KeyError(f"Imported PowSyBl bus not found: {bus_id}")

    voltage_level_id = str(buses.loc[bus_id, "voltage_level_id"])
    voltage_levels = network.get_voltage_levels()
    nominal_v = float(voltage_levels.loc[voltage_level_id, "nominal_v"])

    return bus_id, voltage_level_id, nominal_v


def _validate_kpg_dcline(row: np.ndarray, row_number: int) -> None:
    """Validate assumptions of the current KPG-193 v2.0 DC-line model."""

    if int(round(row[BR_STATUS])) != 1:
        raise ValueError(
            f"dcline row {row_number}: only in-service KPG DC lines are "
            "currently supported by this adapter"
        )

    if not np.isclose(row[LOSS0], 0.0, atol=TOL):
        raise ValueError(
            f"dcline row {row_number}: LOSS0={row[LOSS0]} is not zero"
        )

    if not np.isclose(row[LOSS1], 0.0, atol=TOL):
        raise ValueError(
            f"dcline row {row_number}: LOSS1={row[LOSS1]} is not zero"
        )

    # For a zero-loss dcline, the KPG case stores PF == PT under its MATPOWER
    # sign convention. Guard that assumption before mapping into IIDM.
    if not np.isclose(row[PF], row[PT], atol=1e-6):
        raise ValueError(
            f"dcline row {row_number}: expected PF == PT for zero-loss KPG "
            f"line, got PF={row[PF]}, PT={row[PT]}"
        )


def _add_kpg_hvdc_line(
    network: pp.network.Network,
    row: np.ndarray,
    row_number: int,
) -> None:
    _validate_kpg_dcline(row, row_number)

    from_bus = int(round(row[F_BUS]))
    to_bus = int(round(row[T_BUS]))

    bus1_id, vl1_id, nominal_v1 = _bus_context(network, from_bus)
    bus2_id, vl2_id, nominal_v2 = _bus_context(network, to_bus)

    base_id = f"KPG-HVDC-{from_bus}-{to_bus}-{row_number}"
    cs1_id = f"{base_id}-CS1"
    cs2_id = f"{base_id}-CS2"

    # MATPOWER's signed PF encodes both magnitude and direction. IIDM instead
    # keeps the setpoint non-negative and stores direction in ConvertersMode.
    if row[PF] >= 0.0:
        converters_mode = "SIDE_1_RECTIFIER_SIDE_2_INVERTER"
    else:
        converters_mode = "SIDE_1_INVERTER_SIDE_2_RECTIFIER"

    target_p = abs(float(row[PF]))

    network.create_vsc_converter_stations(
        id=cs1_id,
        voltage_level_id=vl1_id,
        bus_id=bus1_id,
        loss_factor=0.0,
        voltage_regulator_on=True,
        target_v=float(row[VF]) * nominal_v1,
    )
    network.create_vsc_converter_stations(
        id=cs2_id,
        voltage_level_id=vl2_id,
        bus_id=bus2_id,
        loss_factor=0.0,
        voltage_regulator_on=True,
        target_v=float(row[VT]) * nominal_v2,
    )

    # Match the reactive capability information that PowSyBl's MATPOWER
    # importer normally creates for VSC converter stations.
    network.create_minmax_reactive_limits(
        id=cs1_id,
        min_q=float(row[QMINF]),
        max_q=float(row[QMAXF]),
    )
    network.create_minmax_reactive_limits(
        id=cs2_id,
        min_q=float(row[QMINT]),
        max_q=float(row[QMAXT]),
    )

    network.create_hvdc_lines(
        id=base_id,
        converter_station1_id=cs1_id,
        converter_station2_id=cs2_id,
        r=0.0,
        nominal_v=nominal_v1,
        converters_mode=converters_mode,
        max_p=max(abs(float(row[PMIN])), abs(float(row[PMAX]))),
        target_p=target_p,
    )


def load_kpg_network(
    case_path: str | Path,
    *,
    ignore_base_voltage: bool = False,
) -> pp.network.Network:
    """Load the shared KPG MAT case into PyPowSyBl without modifying the case.

    Parameters
    ----------
    case_path:
        MATLAB-v7 KPG MATPOWER case, normally ``KPG193_ver2_0_v7.mat``.
    ignore_base_voltage:
        Passed to PowSyBl's MATPOWER importer. Keep False for KPG so the
        original 154/345/765 kV base-voltage information is preserved.
    """

    case_path = Path(case_path).resolve()
    if not case_path.exists():
        raise FileNotFoundError(case_path)

    source = loadmat(
        case_path,
        struct_as_record=True,
        squeeze_me=False,
    )

    if "mpc" not in source:
        raise KeyError("Top-level 'mpc' struct not found")

    dcline = np.asarray(
        _get_mpc_field(source["mpc"], "dcline"),
        dtype=float,
    )

    with TemporaryDirectory(prefix="kpg-powsybl-") as temp_dir:
        temp_case = Path(temp_dir) / "KPG193_ac_only.mat"
        _create_ac_only_temp_case(source, temp_case)

        network = pp.network.load(
            temp_case,
            parameters={
                "matpower.import.ignore-base-voltage": (
                    "true" if ignore_base_voltage else "false"
                )
            },
        )

    for index, row in enumerate(dcline, start=1):
        _add_kpg_hvdc_line(network, row, index)

    return network
