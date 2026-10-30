#!/usr/bin/env python3
"""PyPSA -> PyPowSyBl (IIDM) conversion and AC PF result extraction for MN_35k_poc.

Electrical mapping (PyPSA 1.2.4 -> IIDM / PyPowSyBl 1.16.1):

- Line: PyPSA effective ``r``/``x`` [Ohm] and total ``b``/``g`` [S] after
  ``calculate_dependent_values()`` (line types x length / num_parallel). IIDM pi
  model: same ``r``/``x``; ``b1 = b2 = b/2`` and ``g1 = g2 = g/2`` (PyPSA builds
  ``Y00 = Y11 = y_se + y_sh/2``).
- Two-winding transformer: PyPSA ``r``/``x`` are per unit on ``s_nom`` with the bus
  nominal voltages as voltage bases. IIDM takes Ohm referred to side 2, so
  ``R = r * rated_u2**2 / s_nom`` with ``rated_u1 = v_nom(bus0)``,
  ``rated_u2 = v_nom(bus1)``. Only ``tap_ratio == 1``, ``phase_shift == 0`` and
  zero magnetising shunt are supported (true for every MN_35k transformer);
  anything else is rejected instead of approximated.
- Generator: PyPSA ``p_set``/``q_set`` are injections; IIDM ``target_p``/``target_q``
  are injections too. PV/Slack -> ``voltage_regulator_on`` with
  ``target_v = v_mag_pu_set * v_nom``; PQ -> fixed ``target_q``.
- Load: PyPSA ``p_set``/``q_set`` (sign -1, consumption positive) -> IIDM ``p0``/``q0``.
- Shunt: PyPSA ShuntImpedance ``b`` [S] (``b < 0`` inductive) -> IIDM linear shunt
  compensator ``b_per_section`` [S], one section in service.

Result conventions used in every comparison (all "physical"):

- branch ``p0/q0`` = power entering the branch at bus0 (IIDM side 1 ``p1/q1``),
  ``p1/q1`` = entering at bus1 (IIDM side 2). PyPSA and IIDM share this sign.
- generator ``p/q`` = injection into the bus. IIDM terminal flows use the load
  convention, so injection = ``-p``/``-q``.
- shunt ``q_absorbed_mvar`` = reactive power absorbed by the shunt (> 0 inductive).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ATOL_PARAMETER = 1e-12


class UnsupportedConversion(ValueError):
    """Raised when a PyPSA attribute cannot be represented exactly in IIDM here."""


def _finite(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    values = frame[columns].astype(float).to_numpy()
    if not np.isfinite(values).all():
        raise ValueError(f"{label}: non-finite value in {columns}")


def substation_groups(buses: list[str], transformers: pd.DataFrame) -> dict[str, str]:
    """IIDM requires both transformer ends in one substation: group by transformer graph."""
    parent = {bus: bus for bus in buses}

    def find(bus: str) -> str:
        while parent[bus] != bus:
            parent[bus] = parent[parent[bus]]
            bus = parent[bus]
        return bus

    for bus0, bus1 in zip(transformers.bus0.astype(str), transformers.bus1.astype(str)):
        root0, root1 = find(bus0), find(bus1)
        if root0 != root1:
            parent[max(root0, root1, key=_bus_sort_key)] = min(root0, root1, key=_bus_sort_key)
    members: dict[str, list[str]] = {}
    for bus in buses:
        members.setdefault(find(bus), []).append(bus)
    result = {}
    for group in members.values():
        anchor = min(group, key=_bus_sort_key)
        for bus in group:
            result[bus] = "S_" + anchor
    return result


def _bus_sort_key(bus: str):
    return (0, int(bus), bus) if bus.isdigit() else (1, 0, bus)


@dataclass
class ConversionModel:
    """Neutral, explicit electrical data taken from a PyPSA network (physical units)."""

    buses: pd.DataFrame
    lines: pd.DataFrame
    transformers: pd.DataFrame
    generators: pd.DataFrame
    loads: pd.DataFrame
    shunts: pd.DataFrame
    slack_bus: str
    mapping: pd.DataFrame = field(default_factory=pd.DataFrame)


def extract_model(n, q_limits: tuple[float, float] | None = None) -> ConversionModel:
    """Read PyPSA effective parameters; reject anything not representable exactly."""
    n.calculate_dependent_values()
    if len(n.sub_networks) == 0:
        n.determine_network_topology()
    unsupported = [c for c in ("links", "storage_units", "stores") if len(getattr(n, c))]
    if unsupported:
        raise UnsupportedConversion(f"Unsupported PyPSA components present: {unsupported}")
    if len(n.sub_networks) != 1:
        raise UnsupportedConversion("Exactly one AC sub-network is required")
    slack_bus = str(n.sub_networks.slack_bus.iloc[0])

    buses = pd.DataFrame({
        "bus_id": n.buses.index.astype(str),
        "v_nom_kv": n.buses.v_nom.astype(float).to_numpy(),
        "control": n.buses.control.astype(str).to_numpy(),
        "v_mag_pu_set": n.buses.v_mag_pu_set.astype(float).to_numpy(),
    })
    _finite(buses, ["v_nom_kv", "v_mag_pu_set"], "buses")
    if (buses.v_nom_kv <= 0).any():
        raise ValueError("Bus nominal voltage must be positive")

    lines = n.lines.loc[n.lines.active.astype(bool)].copy()
    v0 = lines.bus0.map(n.buses.v_nom)
    v1 = lines.bus1.map(n.buses.v_nom)
    if not np.allclose(v0, v1, rtol=0, atol=0):
        raise UnsupportedConversion("Line between different nominal voltages")
    line_frame = pd.DataFrame({
        "line_id": lines.index.astype(str),
        "bus0": lines.bus0.astype(str).to_numpy(),
        "bus1": lines.bus1.astype(str).to_numpy(),
        "v_nom_kv": v0.astype(float).to_numpy(),
        "r_ohm": lines.r.astype(float).to_numpy(),
        "x_ohm": lines.x.astype(float).to_numpy(),
        "g_s": lines.g.astype(float).to_numpy(),
        "b_s": lines.b.astype(float).to_numpy(),
        "s_nom_mva": lines.s_nom.astype(float).to_numpy(),
        "num_parallel": lines.num_parallel.astype(float).to_numpy(),
        "length_km": lines.length.astype(float).to_numpy(),
        "type": lines.type.astype(str).to_numpy(),
    })
    _finite(line_frame, ["r_ohm", "x_ohm", "g_s", "b_s", "s_nom_mva"], "lines")
    if (line_frame.x_ohm <= 0).any() or (line_frame.r_ohm < 0).any() or (line_frame.s_nom_mva <= 0).any():
        raise ValueError("Line r/x/s_nom outside physical range")

    trafos = n.transformers.loc[n.transformers.active.astype(bool)].copy()
    if (trafos.tap_ratio.astype(float) != 1.0).any() or (trafos.phase_shift.astype(float) != 0.0).any():
        raise UnsupportedConversion("Transformer tap_ratio != 1 or phase_shift != 0 is not implemented")
    if (trafos.g.astype(float) != 0.0).any() or (trafos.b.astype(float) != 0.0).any():
        raise UnsupportedConversion("Transformer magnetising shunt (T/pi model difference) is not implemented")
    if (trafos.num_parallel.astype(float) != 1.0).any() or (trafos.type.astype(str) != "").any():
        raise UnsupportedConversion("Typed or parallel transformers are not implemented")
    rated_u1 = trafos.bus0.map(n.buses.v_nom).astype(float)
    rated_u2 = trafos.bus1.map(n.buses.v_nom).astype(float)
    s_nom = trafos.s_nom.astype(float)
    trafo_frame = pd.DataFrame({
        "transformer_id": trafos.index.astype(str),
        "bus0": trafos.bus0.astype(str).to_numpy(),
        "bus1": trafos.bus1.astype(str).to_numpy(),
        "rated_u1_kv": rated_u1.to_numpy(),
        "rated_u2_kv": rated_u2.to_numpy(),
        "s_nom_mva": s_nom.to_numpy(),
        "r_pu_snom": trafos.r.astype(float).to_numpy(),
        "x_pu_snom": trafos.x.astype(float).to_numpy(),
        "r_ohm_side2": (trafos.r.astype(float) * rated_u2**2 / s_nom).to_numpy(),
        "x_ohm_side2": (trafos.x.astype(float) * rated_u2**2 / s_nom).to_numpy(),
        "tap_ratio": trafos.tap_ratio.astype(float).to_numpy(),
        "phase_shift_deg": trafos.phase_shift.astype(float).to_numpy(),
        "pypsa_model": trafos.model.astype(str).to_numpy(),
    })
    _finite(trafo_frame, ["r_ohm_side2", "x_ohm_side2", "s_nom_mva"], "transformers")
    if (trafo_frame.x_pu_snom <= 0).any() or (trafo_frame.s_nom_mva <= 0).any():
        raise ValueError("Transformer x/s_nom outside physical range")

    gens = n.generators.loc[n.generators.active.astype(bool)].copy()
    if (gens.sign.astype(float) != 1.0).any():
        raise UnsupportedConversion("Generator sign != 1")
    controls = gens.control.astype(str)
    if not controls.isin(["PV", "PQ", "Slack"]).all():
        raise UnsupportedConversion("Unknown generator control")
    gen_frame = pd.DataFrame({
        "generator_id": gens.index.astype(str),
        "bus": gens.bus.astype(str).to_numpy(),
        "control": controls.to_numpy(),
        "p_set_mw": gens.p_set.astype(float).to_numpy(),
        "q_set_mvar": gens.q_set.astype(float).to_numpy(),
        "p_min_mw": (gens.p_nom * gens.p_min_pu).astype(float).to_numpy(),
        "p_max_mw": (gens.p_nom * gens.p_max_pu).astype(float).to_numpy(),
    })
    gen_frame["v_set_pu"] = gen_frame.bus.map(dict(zip(buses.bus_id, buses.v_mag_pu_set)))
    gen_frame["v_nom_kv"] = gen_frame.bus.map(dict(zip(buses.bus_id, buses.v_nom_kv)))
    if q_limits is not None:
        gen_frame["q_min_mvar"], gen_frame["q_max_mvar"] = float(q_limits[0]), float(q_limits[1])
    else:
        gen_frame["q_min_mvar"], gen_frame["q_max_mvar"] = np.nan, np.nan
    _finite(gen_frame, ["p_set_mw", "q_set_mvar", "p_min_mw", "p_max_mw", "v_set_pu"], "generators")
    if (gen_frame.control == "Slack").sum() != 1 or gen_frame.loc[gen_frame.control == "Slack", "bus"].iloc[0] != slack_bus:
        raise UnsupportedConversion("Exactly one Slack generator on the sub-network slack bus is required")
    regulated = gen_frame.loc[gen_frame.control != "PQ"]
    if regulated.bus.duplicated().any():
        raise UnsupportedConversion("Several voltage-regulating generators on one bus")
    bus_controls = dict(zip(buses.bus_id, buses.control))
    for row in gen_frame.itertuples():
        expected = row.control
        if expected != "PQ" and bus_controls[row.bus] != expected:
            raise UnsupportedConversion(f"Bus/generator control mismatch at {row.bus}")

    loads = n.loads.loc[n.loads.active.astype(bool)].copy()
    if (loads.sign.astype(float) != -1.0).any():
        raise UnsupportedConversion("Load sign != -1")
    load_frame = pd.DataFrame({
        "load_id": loads.index.astype(str),
        "bus": loads.bus.astype(str).to_numpy(),
        "p_mw": loads.p_set.astype(float).to_numpy(),
        "q_mvar": loads.q_set.astype(float).to_numpy(),
    })
    _finite(load_frame, ["p_mw", "q_mvar"], "loads")

    shunts = n.shunt_impedances.loc[n.shunt_impedances.active.astype(bool)].copy()
    shunt_frame = pd.DataFrame({
        "shunt_id": shunts.index.astype(str),
        "bus": shunts.bus.astype(str).to_numpy(),
        "g_s": shunts.g.astype(float).to_numpy(),
        "b_s": shunts.b.astype(float).to_numpy(),
    })
    shunt_frame["v_nom_kv"] = shunt_frame.bus.map(dict(zip(buses.bus_id, buses.v_nom_kv)))
    shunt_frame["q_absorbed_at_1pu_mvar"] = -shunt_frame.b_s * shunt_frame.v_nom_kv**2
    _finite(shunt_frame, ["g_s", "b_s"], "shunts")
    if (shunts.sign.astype(float) != -1.0).any():
        raise UnsupportedConversion("Shunt sign != -1")

    model = ConversionModel(buses, line_frame, trafo_frame, gen_frame, load_frame, shunt_frame, slack_bus)
    model.mapping = build_mapping(model)
    return model


def build_mapping(model: ConversionModel) -> pd.DataFrame:
    """One row per converted object; every original ID is kept and mapped to an IIDM ID."""
    substations = substation_groups(list(model.buses.bus_id), model.transformers)
    rows = []
    for bus in model.buses.itertuples():
        rows.append({"component": "Bus", "original_id": bus.bus_id, "iidm_id": "B_" + bus.bus_id,
                     "iidm_voltage_level_id": "VL_" + bus.bus_id, "iidm_substation_id": substations[bus.bus_id],
                     "original_bus0": bus.bus_id, "original_bus1": "", "nominal_kv": bus.v_nom_kv})
    for line in model.lines.itertuples():
        rows.append({"component": "Line", "original_id": line.line_id, "iidm_id": "L_" + line.line_id,
                     "iidm_voltage_level_id": "VL_" + line.bus0, "iidm_substation_id": substations[line.bus0],
                     "original_bus0": line.bus0, "original_bus1": line.bus1, "nominal_kv": line.v_nom_kv})
    for item in model.transformers.itertuples():
        rows.append({"component": "Transformer", "original_id": item.transformer_id, "iidm_id": "T_" + item.transformer_id,
                     "iidm_voltage_level_id": "VL_" + item.bus0, "iidm_substation_id": substations[item.bus0],
                     "original_bus0": item.bus0, "original_bus1": item.bus1, "nominal_kv": item.rated_u1_kv})
    for gen in model.generators.itertuples():
        rows.append({"component": "Generator", "original_id": gen.generator_id, "iidm_id": "G_" + gen.generator_id,
                     "iidm_voltage_level_id": "VL_" + gen.bus, "iidm_substation_id": substations[gen.bus],
                     "original_bus0": gen.bus, "original_bus1": "", "nominal_kv": gen.v_nom_kv})
    for load in model.loads.itertuples():
        rows.append({"component": "Load", "original_id": load.load_id, "iidm_id": "D_" + load.load_id,
                     "iidm_voltage_level_id": "VL_" + load.bus, "iidm_substation_id": substations[load.bus],
                     "original_bus0": load.bus, "original_bus1": "", "nominal_kv": np.nan})
    for shunt in model.shunts.itertuples():
        rows.append({"component": "Shunt", "original_id": shunt.shunt_id, "iidm_id": "SH_" + shunt.shunt_id,
                     "iidm_voltage_level_id": "VL_" + shunt.bus, "iidm_substation_id": substations[shunt.bus],
                     "original_bus0": shunt.bus, "original_bus1": "", "nominal_kv": shunt.v_nom_kv})
    mapping = pd.DataFrame(rows)
    if mapping.iidm_id.duplicated().any():
        raise ValueError("IIDM ID collision")
    return mapping


def build_network(model: ConversionModel, network_id: str = "MN_35k_poc_synthetic",
                  voltage_limits_pu: tuple[float, float] | None = None):
    """Create a BUS_BREAKER IIDM network with one voltage level and one bus per PyPSA bus."""
    import pypowsybl.network as pn

    net = pn.create_empty(network_id)
    mapping = model.mapping
    ids = {(row.component, row.original_id): row.iidm_id for row in mapping.itertuples()}
    bus_vl = {row.original_id: row.iidm_voltage_level_id for row in mapping.itertuples() if row.component == "Bus"}
    bus_iidm = {row.original_id: row.iidm_id for row in mapping.itertuples() if row.component == "Bus"}
    bus_rows = mapping.loc[mapping.component == "Bus"]

    subs = sorted(bus_rows.iidm_substation_id.unique())
    net.create_substations(id=subs)
    vl = pd.DataFrame({
        "id": bus_rows.iidm_voltage_level_id.to_numpy(),
        "substation_id": bus_rows.iidm_substation_id.to_numpy(),
        "topology_kind": "BUS_BREAKER",
        "nominal_v": bus_rows.nominal_kv.astype(float).to_numpy(),
        "name": bus_rows.original_id.to_numpy(),
    }).set_index("id")
    if voltage_limits_pu is not None:
        vl["low_voltage_limit"] = vl.nominal_v * float(voltage_limits_pu[0])
        vl["high_voltage_limit"] = vl.nominal_v * float(voltage_limits_pu[1])
    net.create_voltage_levels(vl)
    net.create_buses(pd.DataFrame({
        "id": bus_rows.iidm_id.to_numpy(),
        "voltage_level_id": bus_rows.iidm_voltage_level_id.to_numpy(),
        "name": bus_rows.original_id.to_numpy(),
    }).set_index("id"))

    lines = model.lines
    if len(lines):
        net.create_lines(pd.DataFrame({
            "id": [ids[("Line", i)] for i in lines.line_id],
            "name": lines.line_id.to_numpy(),
            "voltage_level1_id": lines.bus0.map(bus_vl).to_numpy(),
            "bus1_id": lines.bus0.map(bus_iidm).to_numpy(),
            "voltage_level2_id": lines.bus1.map(bus_vl).to_numpy(),
            "bus2_id": lines.bus1.map(bus_iidm).to_numpy(),
            "r": lines.r_ohm.to_numpy(),
            "x": lines.x_ohm.to_numpy(),
            "g1": (lines.g_s / 2).to_numpy(),
            "g2": (lines.g_s / 2).to_numpy(),
            "b1": (lines.b_s / 2).to_numpy(),
            "b2": (lines.b_s / 2).to_numpy(),
        }).set_index("id"))

    trafos = model.transformers
    if len(trafos):
        net.create_2_windings_transformers(pd.DataFrame({
            "id": [ids[("Transformer", i)] for i in trafos.transformer_id],
            "name": trafos.transformer_id.to_numpy(),
            "voltage_level1_id": trafos.bus0.map(bus_vl).to_numpy(),
            "bus1_id": trafos.bus0.map(bus_iidm).to_numpy(),
            "voltage_level2_id": trafos.bus1.map(bus_vl).to_numpy(),
            "bus2_id": trafos.bus1.map(bus_iidm).to_numpy(),
            "rated_u1": trafos.rated_u1_kv.to_numpy(),
            "rated_u2": trafos.rated_u2_kv.to_numpy(),
            "rated_s": trafos.s_nom_mva.to_numpy(),
            "r": trafos.r_ohm_side2.to_numpy(),
            "x": trafos.x_ohm_side2.to_numpy(),
            "g": 0.0,
            "b": 0.0,
        }).set_index("id"))

    branch_limits = []
    for frame, component, key in ((lines, "Line", "line_id"), (trafos, "Transformer", "transformer_id")):
        for row in frame.itertuples():
            for side in ("ONE", "TWO"):
                branch_limits.append({
                    "element_id": ids[(component, getattr(row, key))], "side": side,
                    "name": "SYNTHETIC_S_NOM", "type": "APPARENT_POWER",
                    "value": float(row.s_nom_mva), "acceptable_duration": -1,
                })
    if branch_limits:
        net.create_loading_limits(pd.DataFrame(branch_limits).set_index("element_id"))

    gens = model.generators
    if len(gens):
        regulating = gens.control != "PQ"
        net.create_generators(pd.DataFrame({
            "id": [ids[("Generator", i)] for i in gens.generator_id],
            "name": gens.generator_id.to_numpy(),
            "voltage_level_id": gens.bus.map(bus_vl).to_numpy(),
            "bus_id": gens.bus.map(bus_iidm).to_numpy(),
            "energy_source": "OTHER",
            "min_p": gens.p_min_mw.to_numpy(),
            "max_p": gens.p_max_mw.to_numpy(),
            "target_p": gens.p_set_mw.to_numpy(),
            "target_q": gens.q_set_mvar.to_numpy(),
            "target_v": (gens.v_set_pu * gens.v_nom_kv).to_numpy(),
            "voltage_regulator_on": regulating.to_numpy(),
        }).set_index("id"))
        if gens.q_min_mvar.notna().all():
            net.create_minmax_reactive_limits(pd.DataFrame({
                "id": [ids[("Generator", i)] for i in gens.generator_id],
                "min_q": gens.q_min_mvar.to_numpy(),
                "max_q": gens.q_max_mvar.to_numpy(),
            }).set_index("id"))

    loads = model.loads
    if len(loads):
        net.create_loads(pd.DataFrame({
            "id": [ids[("Load", i)] for i in loads.load_id],
            "name": loads.load_id.to_numpy(),
            "voltage_level_id": loads.bus.map(bus_vl).to_numpy(),
            "bus_id": loads.bus.map(bus_iidm).to_numpy(),
            "p0": loads.p_mw.to_numpy(),
            "q0": loads.q_mvar.to_numpy(),
        }).set_index("id"))

    shunts = model.shunts
    if len(shunts):
        shunt_ids = [ids[("Shunt", i)] for i in shunts.shunt_id]
        net.create_shunt_compensators(
            pd.DataFrame({
                "id": shunt_ids,
                "name": shunts.shunt_id.to_numpy(),
                "voltage_level_id": shunts.bus.map(bus_vl).to_numpy(),
                "bus_id": shunts.bus.map(bus_iidm).to_numpy(),
                "model_type": "LINEAR",
                "section_count": 1,
            }).set_index("id"),
            pd.DataFrame({
                "id": shunt_ids,
                "g_per_section": shunts.g_s.to_numpy(),
                "b_per_section": shunts.b_s.to_numpy(),
                "max_section_count": 1,
            }).set_index("id"),
        )
    return net


def loadflow_parameters(slack_bus_iidm_id: str, use_reactive_limits: bool = False,
                        conv_eps_per_eq: float = 1e-8, max_iterations: int = 30):
    """Explicit OpenLoadFlow settings mirroring PyPSA pf(distribute_slack=False, use_seed=False)."""
    import pypowsybl.loadflow as lf

    return lf.Parameters(
        voltage_init_mode=lf.VoltageInitMode.UNIFORM_VALUES,
        transformer_voltage_control_on=False,
        use_reactive_limits=use_reactive_limits,
        phase_shifter_regulation_on=False,
        twt_split_shunt_admittance=False,
        shunt_compensator_voltage_control_on=False,
        read_slack_bus=False,
        write_slack_bus=False,
        distributed_slack=False,
        component_mode=lf.ComponentMode.MAIN_CONNECTED,
        provider_parameters={
            "slackBusSelectionMode": "NAME",
            "slackBusesIds": slack_bus_iidm_id,
            "referenceBusSelectionMode": "FIRST_SLACK",
            "newtonRaphsonConvEpsPerEq": repr(conv_eps_per_eq),
            "newtonRaphsonStoppingCriteriaType": "UNIFORM_CRITERIA",
            "maxNewtonRaphsonIterations": str(max_iterations),
        },
    )


def run_ac(net, slack_bus_iidm_id: str, **kwargs) -> dict:
    """Run OLF AC PF. ``slack_bus_iidm_id`` is the slack *voltage level* ID (bus IDs are not matched).

    With ``distributed_slack=False`` OLF keeps the slack generator at ``target_p`` and reports
    the residual as ``active_power_mismatch`` on the slack bus; the physical slack injection is
    ``target_p + mismatch`` (verified against PyPSA on the minimal model).
    """
    import pypowsybl.loadflow as lf

    results = lf.run_ac(net, parameters=loadflow_parameters(slack_bus_iidm_id, **kwargs))
    out = []
    for item in results:
        slack = [
            {"bus_id": s.id, "active_power_mismatch_mw": float(s.active_power_mismatch)}
            for s in (getattr(item, "slack_bus_results", None) or [])
        ]
        out.append({
            "connected_component_num": int(item.connected_component_num),
            "synchronous_component_num": int(item.synchronous_component_num),
            "status": item.status.name,
            "status_text": str(item.status_text),
            "iteration_count": int(item.iteration_count),
            "reference_bus_id": str(item.reference_bus_id),
            "slack_bus_results": slack,
        })
    converged = bool(out) and out[0]["status"] == "CONVERGED"
    main = out[0] if out else {}
    expected_prefix = slack_bus_iidm_id + "_"
    slack_ok = bool(main) and main["reference_bus_id"].startswith(expected_prefix) and all(
        item["bus_id"].startswith(expected_prefix) for item in main["slack_bus_results"]
    )
    if converged and not slack_ok:
        raise RuntimeError(
            f"OpenLoadFlow did not use the requested slack {slack_bus_iidm_id}: {main}"
        )
    mismatch = sum(item["active_power_mismatch_mw"] for item in main.get("slack_bus_results", []))
    return {"converged": converged, "components": out, "slack_active_power_mismatch_mw": mismatch}


def pypsa_results(n) -> dict[str, pd.DataFrame]:
    """Extract one snapshot of PyPSA PF results in the shared physical convention."""
    snap = n.snapshots[0]
    buses = pd.DataFrame({
        "bus_id": n.buses.index.astype(str),
        "v_mag_pu": n.buses_t.v_mag_pu.loc[snap].reindex(n.buses.index).to_numpy(),
        "v_ang_deg": np.degrees(n.buses_t.v_ang.loc[snap].reindex(n.buses.index).to_numpy()),
    })
    out = {"buses": buses}
    for component, key in (("lines", "line_id"), ("transformers", "transformer_id")):
        static, dynamic = getattr(n, component), getattr(n, component + "_t")
        out[component] = pd.DataFrame({
            key: static.index.astype(str),
            **{f: dynamic[f].loc[snap].reindex(static.index).to_numpy() for f in ("p0", "q0", "p1", "q1")},
        })
    out["generators"] = pd.DataFrame({
        "generator_id": n.generators.index.astype(str),
        "p_mw": n.generators_t.p.loc[snap].reindex(n.generators.index).to_numpy(),
        "q_mvar": n.generators_t.q.loc[snap].reindex(n.generators.index).to_numpy(),
    })
    # PyPSA ShuntImpedance has sign=-1: q < 0 means absorption (reported as consumption * sign).
    out["shunts"] = pd.DataFrame({
        "shunt_id": n.shunt_impedances.index.astype(str),
        "q_absorbed_mvar": -n.shunt_impedances_t.q.loc[snap].reindex(n.shunt_impedances.index).to_numpy()
        if len(n.shunt_impedances) else [],
    })
    return out


def powsybl_results(net, mapping: pd.DataFrame, slack_generator_id: str | None = None,
                    slack_mismatch_mw: float = 0.0) -> dict[str, pd.DataFrame]:
    """Extract IIDM results keyed by original PyPSA IDs in the shared physical convention.

    ``slack_generator_id`` (original ID) receives ``slack_mismatch_mw`` on top of its terminal
    injection, because OLF without slack distribution does not write the residual back.
    """
    back = {row.iidm_id: row.original_id for row in mapping.itertuples()}
    vls = net.get_voltage_levels()
    b = net.get_buses()
    bus_ids = mapping.loc[mapping.component == "Bus", "iidm_id"]
    # BUS_BREAKER configured bus -> bus view (calculated) bus
    bb = net.get_bus_breaker_view_buses()
    view_bus = bb.loc[bus_ids, "bus_id"]
    rows = []
    for iidm_bus, calc_bus in view_bus.items():
        if not calc_bus or calc_bus not in b.index:
            rows.append({"bus_id": back[iidm_bus], "v_mag_pu": np.nan, "v_ang_deg": np.nan})
            continue
        vl_id = b.at[calc_bus, "voltage_level_id"]
        rows.append({
            "bus_id": back[iidm_bus],
            "v_mag_pu": float(b.at[calc_bus, "v_mag"]) / float(vls.at[vl_id, "nominal_v"]),
            "v_ang_deg": float(b.at[calc_bus, "v_angle"]),
        })
    out = {"buses": pd.DataFrame(rows)}
    for getter, key, component in ((net.get_lines, "line_id", "Line"),
                                   (net.get_2_windings_transformers, "transformer_id", "Transformer")):
        frame = getter()
        ids = mapping.loc[mapping.component == component, "iidm_id"]
        frame = frame.reindex(ids)
        out["lines" if component == "Line" else "transformers"] = pd.DataFrame({
            key: [back[i] for i in ids],
            "p0": frame.p1.to_numpy(dtype=float), "q0": frame.q1.to_numpy(dtype=float),
            "p1": frame.p2.to_numpy(dtype=float), "q1": frame.q2.to_numpy(dtype=float),
        })
    gens = net.get_generators().reindex(mapping.loc[mapping.component == "Generator", "iidm_id"])
    out["generators"] = pd.DataFrame({
        "generator_id": [back[i] for i in gens.index],
        "p_mw": -gens.p.to_numpy(dtype=float),
        "q_mvar": -gens.q.to_numpy(dtype=float),
    })
    if slack_generator_id is not None:
        mask = out["generators"].generator_id == slack_generator_id
        if mask.sum() != 1:
            raise ValueError(f"Slack generator {slack_generator_id} not found")
        out["generators"].loc[mask, "p_mw"] += float(slack_mismatch_mw)
    shunt_ids = mapping.loc[mapping.component == "Shunt", "iidm_id"]
    shunts = net.get_shunt_compensators().reindex(shunt_ids)
    out["shunts"] = pd.DataFrame({
        "shunt_id": [back[i] for i in shunt_ids],
        "q_absorbed_mvar": shunts.q.to_numpy(dtype=float) if len(shunt_ids) else [],
    })
    return out


def compare(reference: pd.DataFrame, candidate: pd.DataFrame, key: str, fields: list[str]) -> pd.DataFrame:
    """Join on original ID; keep non-comparable rows (missing/non-finite) explicitly."""
    merged = reference.merge(candidate, on=key, how="outer", suffixes=("_pypsa", "_powsybl"), indicator=True)
    for f in fields:
        a, c = merged[f + "_pypsa"].astype(float), merged[f + "_powsybl"].astype(float)
        ok = np.isfinite(a) & np.isfinite(c)
        merged[f + "_diff"] = np.where(ok, c - a, np.nan)
    finite = np.ones(len(merged), dtype=bool)
    for f in fields:
        finite &= np.isfinite(merged[f + "_diff"].to_numpy())
    merged["comparable"] = (merged["_merge"] == "both") & finite
    merged["non_comparable_reason"] = np.where(
        merged["_merge"] == "left_only", "missing_in_powsybl",
        np.where(merged["_merge"] == "right_only", "missing_in_pypsa",
                 np.where(finite, "", "non_finite_value")))
    return merged.drop(columns="_merge")


def error_stats(frame: pd.DataFrame, key: str, field: str) -> dict:
    diff = frame.loc[frame.comparable, field + "_diff"].astype(float)
    if diff.empty:
        return {"count": 0, "mean_error": None, "max_abs_error": None, "rmse": None,
                "max_abs_error_id": None, "non_comparable": int((~frame.comparable).sum())}
    idx = diff.abs().idxmax()
    return {
        "count": int(len(diff)),
        "mean_error": float(diff.mean()),
        "max_abs_error": float(diff.abs().max()),
        "rmse": float(math.sqrt(float((diff**2).mean()))),
        "max_abs_error_id": str(frame.at[idx, key]),
        "non_comparable": int((~frame.comparable).sum()),
    }
