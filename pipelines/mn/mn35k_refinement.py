"""Explicit passive-network assumptions for MN_35k; no dispatch or power flow."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

from .artifacts import new_artifact
from .paths import Paths


def _positive(values, label):
    if not all(math.isfinite(float(value)) and float(value) > 0 for value in values):
        raise ValueError(label + " must be finite and positive")


def line_type_parameters(profile, common):
    """Single-circuit, transposed horizontal overhead-line equivalent in SI units.

    Solid-round GMR is an explicit approximation, not stranded ACSR test data.
    The current rating has separate catalogue/weather uncertainty and is not
    calculated from the chosen resistance temperature.
    """
    _positive([profile[key] for key in ["r20_ohm_per_km", "diameter_mm", "adjacent_spacing_m", "i_nom_ka"]], "Conductor parameters")
    _positive([common[key] for key in ["frequency_hz", "epsilon0_f_per_m", "gmr_radius_factor"]], "Line constants")
    if not math.isfinite(profile["temperature_c"]) or not math.isfinite(common["resistance_alpha_per_k"]):
        raise ValueError("Temperature and coefficient must be finite")
    radius = profile["diameter_mm"] / 2000
    gmr = common["gmr_radius_factor"] * radius
    gmd = 2 ** (1 / 3) * profile["adjacent_spacing_m"]
    if gmd <= max(radius, gmr):
        raise ValueError("Phase spacing must exceed conductor radius and GMR")
    resistance = profile["r20_ohm_per_km"] * (1 + common["resistance_alpha_per_k"] * (profile["temperature_c"] - 20))
    _positive([resistance], "Temperature-adjusted resistance")
    return {
        "f_nom": common["frequency_hz"],
        "r_per_length": resistance,
        "x_per_length": 2 * math.pi * common["frequency_hz"] * 2e-4 * math.log(gmd / gmr),
        "c_per_length": 2 * math.pi * common["epsilon0_f_per_m"] * 1e12 / math.log(gmd / radius),
        "i_nom": profile["i_nom_ka"],
        "mounting": "ol", "cross_section": profile["cross_section_mm2"],
        "references": "SYNTHETIC 35kV; configs/mn35k_poc.json; reports/model_refinement/technical_sources.md; not Mongolia equipment data",
    }


def transformer_parameters(profile, common):
    """Return PyPSA inputs on the transformer's own MVA base, not on 1 MVA."""
    _positive([profile[key] for key in ["s_nom_mva", "uk_percent", "load_loss_kw"]], "Transformer test parameters")
    _positive([common["tap_ratio"]], "Tap ratio")
    if common["tap_side"] not in (0, 1):
        raise ValueError("tap_side must be 0 or 1")
    if not all(math.isfinite(common[k]) for k in ["phase_shift_deg", "g_pu", "b_pu"]):
        raise ValueError("Transformer shunt/phase values must be finite")
    z = profile["uk_percent"] / 100
    r = profile["load_loss_kw"] / (1000 * profile["s_nom_mva"])
    if r >= z:
        raise ValueError("Load-loss resistance must be smaller than short-circuit impedance")
    return {"type": "", "s_nom": profile["s_nom_mva"], "r": r, "x": math.sqrt(z*z - r*r),
            "g": common["g_pu"], "b": common["b_pu"], "tap_ratio": common["tap_ratio"],
            "tap_side": common["tap_side"], "phase_shift": common["phase_shift_deg"], "model": common["model"]}


def build_model(original, config, lengths, scenario="reference"):
    """Copy source and apply auditable passive parameters only."""
    if pypsa.__version__ != config["required_pypsa_version"]:
        raise ValueError("Revalidate against the configured PyPSA version before upgrading")
    if scenario not in config["line_35kv"]:
        raise ValueError("Unknown parameter scenario: " + scenario)
    if not lengths.index.is_unique or set(lengths.index) != set(original.lines.index):
        raise ValueError("Length audit must contain exactly one row per original line")
    unresolved = ~lengths.correction_applied.astype(bool)
    if not np.allclose(lengths.loc[unresolved, "length_after_km"], lengths.loc[unresolved, "length_before_km"]):
        raise ValueError("Unresolved length must retain original value")
    if not np.allclose(lengths.length_before_km, original.lines.loc[lengths.index, "length"]):
        raise ValueError("Length audit is for a different source model")
    _positive(lengths.length_after_km, "Audited lengths")
    model = original.copy()
    model.name = "MN_35k synthetic PoC electrical model; no operating scenario"
    type_name = "POC_35kV_SYNTHETIC_ACSR_" + scenario
    model.add("LineType", type_name, **line_type_parameters(config["line_35kv"][scenario], config["line_common"]))
    model.lines.loc[model.lines.v_nom.eq(35), "type"] = type_name
    model.lines["length"] = lengths.length_after_km.reindex(model.lines.index)
    model.lines["poc_parameter_class"] = "STANDARD_ASSUMPTION"
    model.lines["poc_length_class"] = lengths.status.reindex(model.lines.index)
    model.lines["poc_field_verified"] = False
    for name, line in model.lines.iterrows():
        _positive([line.num_parallel, line.length, line.v_nom], "Line circuit/length/voltage")
        model.lines.at[name, "s_nom"] = math.sqrt(3) * line.v_nom * model.line_types.at[line.type, "i_nom"] * line.num_parallel
    for name, row in model.transformers.iterrows():
        pair = "_".join(str(int(v)) for v in sorted(model.buses.loc[[row.bus0, row.bus1], "v_nom"]))
        params = transformer_parameters(config["transformers"][pair][scenario], config["transformer_common"])
        for field, value in params.items():
            model.transformers.at[name, field] = value
    model.transformers["poc_topology_class"] = "SYNTHETIC_TOPOLOGY"
    model.transformers["poc_parameter_class"] = "STANDARD_ASSUMPTION"
    model.transformers["poc_field_verified"] = False
    model.meta = dict(original.meta)
    model.meta["poc_refinement"] = {
        "scenario": scenario, "schema_version": config["schema_version"],
        "source_model_sha256": config["protected_models"][config["input_model"]],
        "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "classification": "SYNTHETIC_PASSIVE_PARAMETER_MODEL",
        "physical_equipment_validated": False, "operating_scenario_defined": False,
        "ac_power_flow_executed": False, "convergence_verified": False,
        "length_unresolved_count": int(unresolved.sum()),
        "assumptions": "reports/model_refinement/assumption_register.csv",
    }
    model.calculate_dependent_values()
    return model


def export_deterministic(model, path, *, paths: Paths, protected_models=None):
    """Keep the original NetCDF3 encoding; publish only a new, protected output.

    Paths is mandatory: module relocation must never change source protection.
    No existing model, report, symlink or hardlink destination is overwritten.
    """
    with new_artifact(paths, Path(path), protected_models=protected_models) as staged:
        dataset = model.export_to_netcdf()
        # PyPSA normally omits these two derived fields. Store them explicitly so
        # both raw reload and recalculation can be checked against the same values.
        for component in ["lines", "transformers"]:
            frame = getattr(model, component)
            for field in ["g_pu", "b_pu"]:
                dataset[component + "_" + field] = ((component + "_i",), frame[field].to_numpy())
        dataset = dataset[sorted(dataset.data_vars)]
        dataset.to_netcdf(staged, engine="scipy", format="NETCDF3_64BIT")


def parameter_tables(original, refined, lengths):
    before = original.copy()
    before.calculate_dependent_values()
    lines, transformers = [], []
    for name, line in refined.lines.iterrows():
        old, derived = original.lines.loc[name], before.lines.loc[name]
        row = {"line_id": name, "voltage_kv": line.v_nom, "bus0": line.bus0, "bus1": line.bus1,
               "parameter_class": "STANDARD_ASSUMPTION", "field_verified": False}
        for key in ["type", "length", "num_parallel", "s_nom", "r", "x", "b", "g", "r_pu", "x_pu", "b_pu", "g_pu"]:
            row[key + "_before_stored"] = old[key]
            row[key + "_before_calculated"] = derived[key]
            row[key + "_after"] = line[key]
        for field in ["r_per_length", "x_per_length", "c_per_length", "i_nom", "f_nom"]:
            row[field + "_before"] = before.line_types.at[old.type, field]
            row[field + "_after"] = refined.line_types.at[line.type, field]
        row["length_status"] = lengths.at[name, "status"]
        lines.append(row)
    for name, row in refined.transformers.iterrows():
        old = original.transformers.loc[name]
        result = {"transformer_id": name, "bus0": row.bus0, "bus1": row.bus1,
                  "bus0_voltage_kv": refined.buses.at[row.bus0, "v_nom"], "bus1_voltage_kv": refined.buses.at[row.bus1, "v_nom"],
                  "topology_class": "SYNTHETIC_TOPOLOGY", "parameter_class": "STANDARD_ASSUMPTION", "field_verified": False}
        for key in ["s_nom", "r", "x", "g", "b", "tap_ratio", "tap_side", "phase_shift", "type", "model"]:
            result[key + "_before"] = old[key]
            result[key + "_after"] = row[key]
        result.update(uk_percent_after=100*np.hypot(row.r, row.x), load_loss_kw_after=row.r*row.s_nom*1000,
                      r_1mva_pu_after=row.r_pu, x_1mva_pu_after=row.x_pu,
                      x_1mva_pu_before=before.transformers.at[name, "x_pu"])
        transformers.append(result)
    return pd.DataFrame(lines), pd.DataFrame(transformers)


def osm_line_evidence(network, raw_path):
    """Trace explicit source tags separately from cleaning/default assumptions."""
    raw = json.loads(Path(raw_path).read_text())
    records = {}
    for feature in raw["features"]:
        props = feature["properties"]
        records[str(props["id"])] = props
    rows = []
    for name, line in network.lines.iterrows():
        way = str(name).split("-", 1)[0].split("_", 1)[0]
        props = records.get(way, {})
        other = props.get("other_tags", {})
        tags = other if isinstance(other, dict) else dict(re.findall(r'"([^"]+)"=>"([^"]*)"', str(other)))
        circuits = tags.get("tags.circuits") or tags.get("circuits") or props.get("tags.circuits")
        cables = props.get("tags.cables")
        wires = props.get("tags.wires") or tags.get("tags.wires") or tags.get("wires")
        category = "EXPLICIT_OSM_CIRCUITS" if circuits else ("INFERRED_CABLES_DIVIDED_BY_THREE" if cables else "DEFAULT_SINGLE_CIRCUIT")
        raw_voltage = str(props.get("tags.voltage", ""))
        if ";" in raw_voltage: category = "UNRESOLVED_MULTI_VOLTAGE_CIRCUITS"
        rows.append({"line_id": str(name), "osm_way_id": way, "voltage_kv": line.v_nom,
                     "raw_power": props.get("tags.power"), "raw_voltage": raw_voltage,
                     "raw_cables": cables, "raw_circuits": circuits, "raw_wires": wires,
                     "model_num_parallel": line.num_parallel, "circuit_evidence": category,
                     "bundle_uncertainty": bool(wires), "field_verified": False})
    return pd.DataFrame(rows).set_index("line_id")


def assumption_register(original, refined, config, lengths, evidence):
    """Per-equipment parameters, unchanged assumptions and unresolved identities."""
    rows = []
    def add(kind, name, param, old, new, unit, source, reason, category="STANDARD_ASSUMPTION", confidence="LOW"):
        rows.append({"equipment_kind": kind, "equipment_id": str(name), "parameter": param,
                     "original_value": old, "changed_value": new, "unit": unit, "source": source,
                     "selection_reason": reason, "assumption_type": category, "confidence": confidence,
                     "replace_when_measured_data_available": True, "field_verified": False})
    src = config["sources"]
    line_units = {"type": "identifier", "length": "km", "num_parallel": "circuits", "s_nom": "MVA", "r": "ohm", "x": "ohm", "b": "S", "g": "S", "r_pu": "pu on 1 MVA", "x_pu": "pu on 1 MVA", "b_pu": "pu on 1 MVA", "g_pu": "pu on 1 MVA"}
    for name, row in refined.lines.iterrows():
        old = original.lines.loc[name]
        length = lengths.loc[name]
        for field, unit in line_units.items():
            category, reason, source = "STANDARD_ASSUMPTION", "Custom 35kV conductor/geometry assumptions" if row.v_nom == 35 else "Retained unverified PyPSA standard type; recalculated using audited length", src["pypsa_lines"]
            if field == "length":
                category, reason, source = length.status, str(length.unresolved_reason) if length.status == "UNRESOLVED" else "WGS84 final simple bus-to-bus chain; includes documented synthetic connectors, not surveyed length", length.source_code_lineage
            if field == "num_parallel":
                tags = evidence.loc[name]
                reason, source = str(tags.circuit_evidence) + "; source-model count retained, not a field circuit inventory", "OSM way " + str(tags.osm_way_id) + "; raw other_tags/cables; PyPSA-Earth clean_osm_data"
                if tags.circuit_evidence == "UNRESOLVED_MULTI_VOLTAGE_CIRCUITS":
                    category, reason = "UNRESOLVED", "Mixed 220000;110000 source with cables6 gives num_parallel3 on EACH voltage; actual allocation unverified; original retained"
                elif tags.circuit_evidence == "EXPLICIT_OSM_CIRCUITS":
                    category, reason = "SOURCE_VERIFIED", "Raw OSM circuits=" + str(tags.raw_circuits) + " observed; source tag verified only, not field equipment"
            add("Line", name, field, old[field], row[field], unit, source, reason, category)
        old_type, new_type = original.line_types.loc[old.type], refined.line_types.loc[row.type]
        for field, unit, source_key in [("r_per_length", "ohm/km", "conductor"), ("x_per_length", "ohm/km", "inductance"), ("c_per_length", "nF/km", "capacitance"), ("i_nom", "kA", "ampacity"), ("f_nom", "Hz", "pypsa_lines")]:
            source = src[source_key] if row.v_nom == 35 else "PyPSA1.2.4 standard_types/line_types.csv: " + old.type
            reason = "Synthetic conductor/temperature/spacing equivalent; catalogue ampacity weather unspecified" if row.v_nom == 35 else "Retained library proxy; actual conductor/bundling/current rating unresolved"
            add("Line", name, field, old_type[field], new_type[field], unit, source, reason)
        if row.v_nom == 35:
            profile = config["line_35kv"][refined.meta["poc_refinement"]["scenario"]]
            for key, unit, source_key in [("r20_ohm_per_km", "ohm/km", "conductor"), ("diameter_mm", "mm", "conductor"), ("adjacent_spacing_m", "m", "spacing"), ("temperature_c", "degC", "temperature")]:
                add("Line", name, key, "UNRESOLVED", profile[key], unit, src[source_key], "Analyst-selected synthetic35kV scenario; spacing uses33kV foreign design analogue; temperature is scenario, not observation")
            for key, unit, source_key in [("resistance_alpha_per_k", "1/K", "temperature"), ("gmr_radius_factor", "ratio", "inductance"), ("epsilon0_f_per_m", "F/m", "capacitance")]:
                add("Line", name, key, "Not specified", config["line_common"][key], unit, src[source_key], "Explicit analytical constant/round-equivalent approximation, not measured conductor GMR")
        if evidence.at[name, "bundle_uncertainty"]:
            add("Line", name, "bundle_and_type_consistency", evidence.at[name, "raw_wires"], "Original standard type retained", "conductors per phase", "OSM way " + str(evidence.at[name, "osm_way_id"]), "Raw wires tag indicates bundle; conductor and bundle spacing unverified; cannot calibrate existing single-conductor type", "UNRESOLVED")
        add("Line", name, "virtual_connector_length", "Not separately stored", length.virtual_connector_geodesic_km, "km", length.source_code_lineage, "Final geometry segments unsupported by exact clean-parent route; may be synthetic additions, not verified conductor", "SYNTHETIC_TOPOLOGY")
        add("Line", name, "actual_conductor_and_route", "Unknown", "Unknown", "identity", "OSM raw tags and clean/build lineage", "Actual conductor, thermal rating, in-service state and physical route need replacement/verification", "UNRESOLVED")
    for name, row in refined.transformers.iterrows():
        old = original.transformers.loc[name]
        pair = sorted(refined.buses.loc[[row.bus0, row.bus1], "v_nom"])
        source = src["trafo_35_110"] if pair[0] == 35 else src["trafo_110_220"]
        for field, unit in [("s_nom", "MVA"), ("r", "pu on s_nom"), ("x", "pu on s_nom"), ("g", "pu on s_nom"), ("b", "pu on s_nom"), ("r_pu", "pu on 1 MVA"), ("x_pu", "pu on 1 MVA"), ("g_pu", "pu on 1 MVA"), ("b_pu", "pu on 1 MVA"), ("tap_ratio", "pu"), ("tap_side", "bus side"), ("phase_shift", "deg"), ("type", "identifier"), ("model", "model")]:
            reason = "Representative equivalent; r=Pcu/(1000*S), x=sqrt((uk/100)^2-r^2); no equipment match"
            if field in ["g", "b"]: reason = "Core losses/magnetizing current omitted; actual physical losses are not zero"
            if field in ["tap_ratio", "tap_side", "phase_shift"]: reason = "Neutral tap and zero phase shift assumed; bus0=LV preserved; catalog YNd11 vector group NOT adopted"
            add("Transformer", name, field, old[field], row[field], unit, source + "; " + src["pypsa_transformers"], reason)
        for param, value, unit in [("uk_percent", 100*np.hypot(row.r,row.x), "%"), ("load_loss", row.r*row.s_nom*1000, "kW")]:
            add("Transformer", name, param, "Unverified default", value, unit, source, "Catalogue representative only; paired voltage branch is not matched to actual unit")
        add("Transformer", name, "connection", old.bus0+"->"+old.bus1, row.bus0+"->"+row.bus1, "bus IDs", "pypsa-earth/scripts/build_osm_network.py:get_transformers", "Same-station adjacent-voltage synthetic connection retained", "SYNTHETIC_TOPOLOGY")
        add("Transformer", name, "nameplate_vector_group_units_tertiary", "Unknown", "Unknown", "identity", src["mongolia_projects"], "Public Mongolia project ratings cannot be assigned to synthetic branch IDs; actual tap/group/unit count/third winding unknown", "UNRESOLVED")
    return pd.DataFrame(rows)
