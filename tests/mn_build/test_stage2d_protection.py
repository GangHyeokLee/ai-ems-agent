"""Failure/path regressions; mutate copied evidence only, never original files."""
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest
pytest.importorskip("pypsa", reason="Stage 2D protection requires separate build environment")
from pipelines.mn import diagnostic_inputs as inputs
from pipelines.mn import audit_mn35k_plants as plants, run_mn35k_data_informed_pf as data
from pipelines.mn import diagnose_mn35k_reactive as reactive, audit_mn35k_220kv_lines as lines
from pipelines.mn.passive_inputs import logical_input

MODULES=(plants,data,reactive,lines)


@pytest.fixture
def copied_inputs(source_paths,tmp_path):
    p=replace(source_paths,source_project=tmp_path/"source",earth_checkout=tmp_path/"independent-earth",
              output_dir=tmp_path/"generated",work_dir=tmp_path/"work",bundle_dir=tmp_path/"bundle")
    for logical in inputs.contract()["files"]:
        target=logical_input(p,logical);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(logical_input(source_paths,logical),target)
    return p


@pytest.mark.parametrize("module",MODULES)
@pytest.mark.parametrize("issue",["missing_model","bad_hash","missing_config","bad_config","missing_evidence","extra_evidence","escaped_link"])
def test_invalid_inputs(copied_inputs,module,issue,tmp_path):
    p=copied_inputs
    logical={"missing_model":"pypsa-earth/networks/MN_35k_poc/base.nc",
             "bad_hash":"pypsa-earth/resources/MN/powerplants.csv",
             "missing_config":"configs/mn35k_data_informed_scenario.json",
             "bad_config":"configs/mn35k_reactive_diagnostics.json",
             "missing_evidence":"reports/data_informed/plant_identity_audit.csv"}
    if issue in logical:
        file=logical_input(p,logical[issue])
        if issue.startswith("missing"):file.unlink()
        else:file.write_bytes(b"wrong")
    elif issue=="extra_evidence":(p.source_project/"reports/data_informed/extra.json").write_text("{}")
    else:
        file=p.source_project/"configs/mn35k_acpf.json";outside=tmp_path/"outside.json"
        shutil.copyfile(file,outside);file.unlink();file.symlink_to(outside)
    with pytest.raises((ValueError,FileNotFoundError)):
        module.analyze_for_verification(p,p.output_dir/"result")
    assert not (p.output_dir/"result").exists()


@pytest.mark.parametrize("module",MODULES)
@pytest.mark.parametrize("kind",["source","file","directory","symlink","dangling_symlink","hardlink","work","nested"])
def test_bad_output_paths(copied_inputs,module,kind):
    p=copied_inputs;target=p.output_dir/"result";target.parent.mkdir(parents=True)
    source=logical_input(p,"pypsa-earth/networks/MN_35k_poc/base.nc")
    if kind=="source":target=source
    elif kind=="file":target.write_text("keep")
    elif kind=="directory":target.mkdir()
    elif kind=="symlink":target.symlink_to(p.source_project,target_is_directory=True)
    elif kind=="dangling_symlink":target.symlink_to(p.output_dir/"missing",target_is_directory=True)
    elif kind=="hardlink":target.hardlink_to(source)
    elif kind=="work":target=p.work_dir/"result"
    else:p=replace(p,work_dir=p.output_dir/"nested")
    before=inputs.source_manifest(copied_inputs)
    with pytest.raises(ValueError):module.analyze_for_verification(p,target)
    assert inputs.source_manifest(copied_inputs)==before
    if kind=="file":assert target.read_text()=="keep"


@pytest.mark.parametrize("module",MODULES)
def test_actual_report_failure_never_published(source_paths,tmp_path,monkeypatch,module):
    p=replace(source_paths,output_dir=tmp_path/"generated")
    call=0;original=pd.DataFrame.to_csv;fail_at=2 if module is plants else 3
    def failing(frame,*args,**kwargs):
        nonlocal call
        call+=1
        if call==fail_at:raise OSError("injected report failure")
        return original(frame,*args,**kwargs)
    monkeypatch.setattr(pd.DataFrame,"to_csv",failing)
    with pytest.raises(OSError,match="injected"):
        module.analyze_for_verification(p,p.output_dir/"result")
    assert not (p.output_dir/"result").exists()
    assert list(p.output_dir.iterdir())==[]


@pytest.mark.parametrize("module",MODULES)
def test_unexpected_execution_error_publishes_nothing(source_paths,tmp_path,monkeypatch,module):
    p=replace(source_paths,output_dir=tmp_path/"generated")
    def fail(*a,**kw):raise RuntimeError("injected PF or audit execution error")
    monkeypatch.setattr(module,"_generate",fail)
    with pytest.raises(RuntimeError,match="injected"):
        module.analyze_for_verification(p,p.output_dir/"result")
    assert list(p.output_dir.iterdir())==[]


@pytest.mark.parametrize("failure",["third","fifth","generated_symlink","generated_hardlink","missing_file","input_change","publication_race"])
def test_private_bundle_validation_and_retry(copied_inputs,monkeypatch,failure):
    p=copied_inputs;target=p.output_dir/"result"
    expected=tuple(f"part-{i}.json" for i in range(6))
    source=logical_input(p,"pypsa-earth/resources/MN/powerplants.csv")
    def generate(output):
        for index,name in enumerate(expected,1):
            if (failure=="third" and index==3) or (failure=="fifth" and index==5):
                raise OSError("injected partial failure")
            (output/name).write_text("{}")
        if failure=="generated_symlink":
            (output/expected[-1]).unlink();(output/expected[-1]).symlink_to(source)
        elif failure=="generated_hardlink":
            (output/expected[-1]).unlink();(output/expected[-1]).hardlink_to(source)
        elif failure=="missing_file":(output/expected[-1]).unlink()
        elif failure=="input_change":source.write_bytes(b"changed input")
        elif failure=="publication_race":target.mkdir();(target/"keep").write_text("keep")
        return {"classification":"DIAGNOSTIC_ONLY"}
    with pytest.raises((OSError,ValueError)):
        inputs.analysis_bundle(p,target,expected,generate)
    if failure=="publication_race":assert (target/"keep").read_text()=="keep"
    else:
        assert not target.exists()
        assert list(p.output_dir.iterdir())==[]
    if failure in ("third","fifth"):
        def normal(output):
            for name in expected:(output/name).write_text("{}")
            return {"classification":"DIAGNOSTIC_ONLY"}
        inputs.analysis_bundle(p,target,expected,normal)
        assert (target/"analysis_provenance.json").is_file()


@pytest.mark.parametrize("module",[data,reactive])
def test_selected_config_must_match_pinned_bytes(source_paths,tmp_path,module):
    path=tmp_path/"settings.json";path.write_text("{}")
    p=replace(source_paths,output_dir=tmp_path/"generated")
    with pytest.raises(ValueError,match="SHA256"):
        module.analyze_for_verification(p,p.output_dir/"result",config_path=path)
    assert not p.output_dir.exists()


def test_evidence_works_without_original_git_branches(copied_inputs):
    assert not (copied_inputs.source_project/".git").exists()
    result=plants.analyze_for_verification(copied_inputs,copied_inputs.output_dir/"without-git")
    assert result["plant_rows"]==27


def test_unexpected_converged_experiment_stays_unusable(copied_inputs,monkeypatch):
    """Exercise the conditional model path without changing PF or active inputs."""
    p=copied_inputs;before=inputs.source_manifest(p)
    def converged(paths,output,settings,base_config,support_settings):
        for name in data.EXPECTED_FILES:
            shutil.copyfile(p.source_project/"reports/data_informed"/name,output/name)
        filename=Path(settings["output_model"]).name
        shutil.copyfile(logical_input(p,base_config["input_model"]),output/filename)
        return {"classification":"DATA_INFORMED_SCENARIO_CONVERGED_WITHIN_ASSUMED_LIMITS"}
    monkeypatch.setattr(data,"_generate",converged)
    result=data.analyze_for_verification(p,p.output_dir/"unexpected-convergence")
    assert result["model_usable"] is False and result["model_activated"] is False
    assert len(list(Path(result["artifact"]).glob("*.nc")))==1
    assert inputs.source_manifest(p)==before
