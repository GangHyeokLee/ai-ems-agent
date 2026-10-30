"""Conversion failure gates and publication safety, using copied inputs only."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest
pytest.importorskip("pypsa",reason="Stage 3A requires separate conversion environment")
pytest.importorskip("pypowsybl",reason="Stage 3A requires PyPowSyBl")
from pipelines.mn import convert_mn35k_to_powsybl as converter
from pipelines.mn import conversion_inputs as inputs
from pipelines.mn import conversion_checks as checks
from pipelines.mn.passive_inputs import logical_input


@pytest.fixture
def copied_inputs(source_paths,tmp_path):
    paths=replace(source_paths,source_project=tmp_path/"source",earth_checkout=tmp_path/"earth",
                  output_dir=tmp_path/"output",work_dir=tmp_path/"work",bundle_dir=tmp_path/"bundles")
    for logical in inputs.contract()["source_files"]:
        target=logical_input(paths,logical);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(logical_input(source_paths,logical),target)
    return paths


@pytest.mark.parametrize("issue",["missing_model","bad_model_hash","missing_config","bad_config","missing_mapping","missing_validation","missing_generator_reference","extra_report","input_link_escape","wrong_selected_config"])
def test_bad_inputs_fail_before_conversion(copied_inputs,tmp_path,issue):
    p=copied_inputs;config=inputs.read_settings(p);kwargs={}
    names={"missing_model":config["input_model"],"bad_model_hash":config["input_model"],
           "missing_config":inputs.contract()["config"],"bad_config":inputs.contract()["config"],
           "missing_mapping":config["reports_dir"]+"/element_mapping.csv",
           "missing_validation":config["reports_dir"]+"/conversion_validation.json",
           "missing_generator_reference":config["reports_dir"]+"/pf_generator_comparison.csv"}
    if issue in names:
        file=logical_input(p,names[issue])
        if issue.startswith("missing"):file.unlink()
        else:file.write_bytes(b"changed bytes")
    elif issue=="extra_report":(p.source_project/"reports/extra.json").write_text("{}")
    elif issue=="input_link_escape":
        file=logical_input(p,config["input_model"]);outside=tmp_path/"outside.nc"
        shutil.copyfile(file,outside);file.unlink();file.symlink_to(outside)
    else:
        cfg=tmp_path/"config.json";cfg.write_text("{}");kwargs["config_path"]=cfg
    with pytest.raises((ValueError,FileNotFoundError)):
        converter.convert_for_verification(p,p.output_dir/"result",**kwargs)
    assert not (p.output_dir/"result").exists()


@pytest.mark.parametrize("kind",["source","file","directory","symlink","dangling_symlink","hardlink","work","nested"])
def test_bad_output_paths(copied_inputs,kind):
    p=copied_inputs;target=p.output_dir/"result";target.parent.mkdir(parents=True)
    source=logical_input(p,inputs.read_settings(p)["input_model"])
    if kind=="source":target=source
    elif kind=="file":target.write_text("keep")
    elif kind=="directory":target.mkdir()
    elif kind=="symlink":target.symlink_to(p.source_project,target_is_directory=True)
    elif kind=="dangling_symlink":target.symlink_to(p.output_dir/"missing",target_is_directory=True)
    elif kind=="hardlink":target.hardlink_to(source)
    elif kind=="work":target=p.work_dir/"result"
    else:p=replace(p,work_dir=p.output_dir/"nested")
    before=inputs.source_manifest(copied_inputs)
    with pytest.raises(ValueError):converter.convert_for_verification(p,target)
    assert inputs.source_manifest(copied_inputs)==before
    if kind=="file":assert target.read_text()=="keep"


@pytest.mark.parametrize("kind",["exception","missing_equipment","mapping_duplicate","wrong_connection","nonconvergence","failed_verdict"])
def test_conversion_failure_is_never_public(source_paths,tmp_path,monkeypatch,kind):
    p=replace(source_paths,output_dir=tmp_path/"output")
    if kind=="exception":
        def fail(*a,**kw):raise RuntimeError("injected conversion exception")
        monkeypatch.setattr(converter.conv,"extract_model",fail)
    elif kind in ("missing_equipment","mapping_duplicate"):
        original=converter.conv.extract_model
        def corrupted(*a,**kw):
            model=original(*a,**kw)
            if kind=="missing_equipment":model.generators=model.generators.iloc[:-1].copy()
            else:model.mapping=pd.concat([model.mapping,model.mapping.iloc[:1]],ignore_index=True)
            return model
        monkeypatch.setattr(converter.conv,"extract_model",corrupted)
    elif kind=="wrong_connection":
        original=converter.conv.build_network
        def wrong(*a,**kw):
            model=a[0]
            model.generators.loc[model.generators.control.eq("Slack"),"bus"]="10"
            return original(*a,**kw)
        monkeypatch.setattr(converter.conv,"build_network",wrong)
    elif kind=="nonconvergence":
        monkeypatch.setattr(converter.conv,"run_ac",lambda *a,**kw:{"converged":False,"components":[]})
    else:
        original=converter.stats_and_verdict
        def failed(*a,**kw):
            value=original(*a,**kw);value["within_all_tolerances"]=False;return value
        monkeypatch.setattr(converter,"stats_and_verdict",failed)
    with pytest.raises((ValueError,RuntimeError,AssertionError)):
        converter.convert_for_verification(p,p.output_dir/"result")
    assert not (p.output_dir/"result").exists()
    assert list(p.output_dir.iterdir())==[]


@pytest.mark.parametrize("at",[3,5])
def test_report_failure_cleans_private_bundle(source_paths,tmp_path,monkeypatch,at):
    p=replace(source_paths,output_dir=tmp_path/"output")
    original=pd.DataFrame.to_csv;count=0
    def failing(frame,*a,**kw):
        nonlocal count
        count+=1
        if count==at:raise OSError("injected report failure")
        return original(frame,*a,**kw)
    monkeypatch.setattr(pd.DataFrame,"to_csv",failing)
    with pytest.raises(OSError,match="injected"):
        converter.convert_for_verification(p,p.output_dir/"result")
    assert list(p.output_dir.iterdir())==[]


@pytest.mark.parametrize("issue",["input_changed","publication_collision","generated_hardlink","runtime_failure","parameter_mismatch","tampered_xiidm_bytes"])
def test_post_conversion_failures(copied_inputs,monkeypatch,issue):
    p=copied_inputs;target=p.output_dir/"result"
    # Read-only verified bundle is fixture evidence for testing post-generation
    # gates, not a freshly converted model or a substitute calculation.
    config=inputs.read_settings(p)
    fixture=logical_input(p,config["reports_dir"])
    def staged(paths,config,output):
        for name in converter.EXPECTED_FILES:
            if name not in ("runtime_crosscheck.json","verification_report.md","conversion_provenance.json"):
                source=logical_input(p,config["powsybl_output"]) if name.endswith(".xiidm") else fixture/name
                shutil.copyfile(source,output/name)
        hashes=json.loads((output/"input_hashes.json").read_text())
        from pipelines.mn.inventory import sha256
        hashes["xiidm_output"]={"mn35k_synthetic_powsybl.xiidm":sha256(output/"mn35k_synthetic_powsybl.xiidm")}
        (output/"input_hashes.json").write_text(json.dumps(hashes))
        if issue=="tampered_xiidm_bytes":
            with (output/"mn35k_synthetic_powsybl.xiidm").open("ab") as handle:handle.write(b"\n")
        elif issue=="input_changed":logical_input(p,config["input_model"]).write_bytes(b"changed input")
        elif issue=="publication_collision":target.mkdir();(target/"keep").write_text("keep")
        elif issue=="generated_hardlink":
            file=output/"pf_bus_comparison.csv";file.unlink()
            file.hardlink_to(logical_input(p,config["reports_dir"]+"/pf_bus_comparison.csv"))
        return json.loads((fixture/"pf_summary.json").read_text())
    monkeypatch.setattr(converter,"_generate",staged)
    if issue=="runtime_failure":
        actual=converter.runtime_observation;count=0
        def fail(*a,**kw):
            nonlocal count
            count+=1
            if count>1:raise ValueError("injected candidate runtime failure")
            return actual(*a,**kw)
        monkeypatch.setattr(converter,"runtime_observation",fail)
    elif issue=="parameter_mismatch":
        actual=converter.runtime_observation
        def mismatch(*a,**kw):
            value=actual(*a,**kw);value["loadflow_parameters"]["distributed_slack"]=True;return value
        monkeypatch.setattr(converter,"runtime_observation",mismatch)
    with pytest.raises((ValueError,OSError),match="byte-integrity" if issue=="tampered_xiidm_bytes" else None):
        converter.convert_for_verification(p,target)
    if issue=="publication_collision":assert (target/"keep").read_text()=="keep"
    else:assert not target.exists() and list(p.output_dir.iterdir())==[]
