"""Execute original orchestration only in an isolated copied evidence project."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pandas as pd
import pytest
pytest.importorskip("pypsa", reason="Stage 2D comparisons require separate build environment")
from pipelines.mn import diagnostic_inputs as inputs
from pipelines.mn import audit_mn35k_plants as plants, run_mn35k_data_informed_pf as data
from pipelines.mn import diagnose_mn35k_reactive as reactive, audit_mn35k_220kv_lines as lines
from pipelines.mn.inventory import load_contract, sha256
from pipelines.mn.passive_inputs import logical_input

MODULES = (plants, data, reactive, lines)
NAMES = ("plants", "data-informed", "reactive", "220kv")


@pytest.fixture(scope="module")
def original_analysis(source_paths, original_modules, tmp_path_factory):
    root=tmp_path_factory.mktemp("stage2d-original")
    for logical in inputs.contract()["files"]:
        target=root/logical
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(logical_input(source_paths,logical),target)
    modules=dict(vars(original_modules))
    source_pins={s["path"]:s["sha256"] for s in load_contract()["files"] if s["root"]=="source"}
    with pytest.MonkeyPatch.context() as patch:
        for key,module in modules.items():patch.setitem(sys.modules,key,module)
        order=("validate_mn35k_poc","run_mn35k_acpf","run_mn35k_reactive_support",
               "audit_mn35k_plants","run_mn35k_data_informed_pf","diagnose_mn35k_reactive","audit_mn35k_220kv_lines")
        # Existing original helper ROOT is only used by functions that read
        # inputs; override the bound default root for the length-audit call too.
        patch.setattr(modules["audit_mn35k_parameters"],"ROOT",root)
        for name in order:
            path=source_paths.source_project/"scripts"/(name+".py")
            assert sha256(path)==source_pins["scripts/"+name+".py"]
            spec=importlib.util.spec_from_file_location("reference2d_"+name,path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            if hasattr(module,"ROOT"):module.ROOT=root
            modules[name]=module;patch.setitem(sys.modules,name,module)
        modules["audit_mn35k_220kv_lines"].audit_line_lengths=lambda n: modules["mn35k_lengths"].audit_line_lengths(n,root=root)
        yield root,modules


@pytest.mark.parametrize("kind",NAMES)
def test_original_and_migrated_analysis(source_paths,original_analysis,tmp_path,kind):
    root,originals=original_analysis
    index=NAMES.index(kind);migrated=MODULES[index]
    before=inputs.source_manifest(source_paths)
    if kind=="plants":
        originals["audit_mn35k_plants"].audit()
        report_dir="reports/data_informed"
    elif kind=="data-informed":
        assert originals["run_mn35k_data_informed_pf"].run(root/"configs/mn35k_data_informed_scenario.json")==0
        report_dir="reports/data_informed"
    elif kind=="reactive":
        assert originals["diagnose_mn35k_reactive"].run(root/"configs/mn35k_reactive_diagnostics.json")==0
        report_dir="reports/reactive_diagnostics"
    else:
        originals["audit_mn35k_220kv_lines"].run()
        report_dir="reports/220kv_line_audit"
    paths=replace(source_paths,output_dir=tmp_path/"generated")
    result=migrated.analyze_for_verification(paths,paths.output_dir/kind)
    target=Path(result["artifact"])
    assert result["model_usable"] is False and result["model_activated"] is False
    records={}
    for name in migrated.EXPECTED_FILES:
        original=root/report_dir/name;candidate=target/name
        equal=original.read_bytes()==candidate.read_bytes()
        records[name]={"original_sha256":sha256(original),"migrated_sha256":sha256(candidate),
                       "historical_sha256":sha256(source_paths.source_project/report_dir/name),"bytes_equal":equal}
        if name=="source_preservation.json":
            assert json.loads(candidate.read_text())["unchanged"]
        elif name=="diagnostic_assumptions.json":
            a,b=json.loads(original.read_text()),json.loads(candidate.read_text())
            for key in ("protected_hashes_before","protected_hashes_after"):a.pop(key);b.pop(key)
            assert a==b
        elif kind=="220kv" and name.endswith(".md"):
            # Only the enlarged protection-count sentence is allowed to differ.
            import re
            a=re.sub(r"\d+개 파일", "COUNT개 파일", original.read_text())
            b=re.sub(r"\d+개 파일", "COUNT개 파일", candidate.read_text())
            assert a==b
        else:assert equal,name
    if kind=="data-informed":
        assert result["classification"]=="DATA_INFORMED_SCENARIO_NOT_CONVERGED"
        assert result["candidate_record_ids"]==[1,5]
        assert not list(target.glob("*.nc"))
        summary=pd.read_csv(target/"scenario_comparison.csv")
        final=summary.loc[summary.scenario_id.eq("plant_candidates_assumed_connection")].iloc[0]
        assert final.pf_status=="AC_PF_NOT_CONVERGED"
        assert pd.isna(final.total_generation_mw)
    if kind=="reactive":assert result["candidate_q_limited_status"]=="AC_PF_NOT_CONVERGED"
    if kind=="plants":assert result["plant_rows"]==27 and result["electrical_connections_found"]==0
    if kind=="220kv":assert result["line_count"]==19 and result["corrections_applied"] is False
    assert inputs.source_manifest(source_paths)==before
    # Complete repeat, including diagnostic images/provenance, must be deterministic.
    again=migrated.analyze_for_verification(paths,paths.output_dir/(kind+"-again"))
    for p in target.iterdir():assert p.read_bytes()==(Path(again["artifact"])/p.name).read_bytes(),p.name
    with pytest.raises(ValueError,match="existing artifact"):
        migrated.analyze_for_verification(paths,target)
    (tmp_path/"equivalence.json").write_text(json.dumps({"kind":kind,"files":records,"result":result},indent=2)+"\n")
