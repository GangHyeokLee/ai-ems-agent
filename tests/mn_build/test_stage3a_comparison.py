"""Same-environment original converter and isolated EMS compatibility checks."""
from dataclasses import replace
import ast
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import types
import xml.etree.ElementTree as ET

import pandas as pd
import pytest
pytest.importorskip("pypsa",reason="Stage 3A requires separate conversion environment")
pp=pytest.importorskip("pypowsybl",reason="Stage 3A requires PyPowSyBl")
from pipelines.mn import convert_mn35k_to_powsybl as migrated
from pipelines.mn import conversion_inputs as inputs
from pipelines.mn.conversion_checks import runtime_observation, runtime_comparison
from pipelines.mn.inventory import sha256
from pipelines.mn.passive_inputs import logical_input


def xml_model(path):
    root=ET.parse(path).getroot()
    date=root.attrib.pop("caseDate",None)
    return ET.canonicalize(ET.tostring(root,encoding="unicode")),date


@pytest.fixture(scope="module")
def isolated_conversion(source_paths,original_modules,tmp_path_factory):
    config=inputs.read_settings(source_paths)
    root=tmp_path_factory.mktemp("stage3a-original")
    for logical in inputs.contract()["source_files"]:
        target=root/logical;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(logical_input(source_paths,logical),target)
    original=source_paths.source_project/"scripts/convert_mn35k_to_powsybl.py"
    assert sha256(original)==inputs.contract()["source_files"]["scripts/convert_mn35k_to_powsybl.py"]
    tree=ast.parse(original.read_text())
    # Only remove the original top-level sys.path.insert for module loading;
    # package production code never imports or modifies the source project.
    removed=[n for n in tree.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
             and ast.unparse(n.value.func)=="sys.path.insert"]
    assert len(removed)==1
    tree.body=[n for n in tree.body if n not in removed]
    module=types.ModuleType("reference3a_converter");module.__file__=str(original)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules,"mn35k_powsybl",original_modules.mn35k_powsybl)
        exec(compile(tree,str(original),"exec"),module.__dict__)
        module.ROOT=root
        module.AI_EMS_PREVIEW=source_paths.ems_root/"data/grids/mn35k-synthetic-pf-preview.json"
        # Existing copied XIIDM is only reference evidence, not a write target.
        original_xiidm=root/config["powsybl_output"];original_xiidm.unlink()
        original_summary=module.run(root/inputs.contract()["config"])
    assert original_summary["validation_passed"]
    paths=replace(source_paths,output_dir=root/"new-output")
    result=migrated.convert_for_verification(paths,paths.output_dir/"converted")
    return root,paths,config,module,result


def test_original_and_migrated_full_converter(isolated_conversion):
    root,paths,config,reference,result=isolated_conversion
    output=Path(result["artifact"]);oldreports=root/config["reports_dir"]
    differences={}
    for name in ("element_mapping.csv","conversion_parameters.csv","pf_bus_comparison.csv",
                 "pf_line_comparison.csv","pf_transformer_comparison.csv","pf_generator_comparison.csv",
                 "pf_shunt_comparison.csv","n1_feasibility_results.csv","n1_ui_compatible_results.json","pf_summary.json"):
        assert (output/name).read_bytes()==(oldreports/name).read_bytes(),name
        historical=logical_input(paths,config["reports_dir"]+"/"+name)
        differences[name]={"original":sha256(oldreports/name),"migrated":sha256(output/name),
                           "historical":sha256(historical),"historical_bytes_equal":historical.read_bytes()==(output/name).read_bytes()}
    a=json.loads((oldreports/"conversion_validation.json").read_text())
    b=json.loads((output/"conversion_validation.json").read_text())
    for value in (a,b):
        value["xiidm_reload"].pop("path");value["xiidm_reload"].pop("sha256")
    assert a==b
    source=logical_input(paths,config["powsybl_output"])
    created=output/"mn35k_synthetic_powsybl.xiidm";original=root/config["powsybl_output"]
    assert xml_model(created)[0]==xml_model(original)[0]
    # Existing XIIDM differs in date; require the full XML tree to agree after
    # removing exactly that one volatile root field, without numerical rounding.
    assert xml_model(created)[0]==xml_model(source)[0]
    hashes=json.loads((output/"input_hashes.json").read_text())
    assert hashes["input_model"]=={config["input_model"]:config["input_model_sha256"]}
    assert hashes["protected_unchanged"] is True
    report={"csv_json":differences,"xiidm":{str(p):{"sha256":sha256(p),"caseDate":xml_model(p)[1]}
            for p in (source,original,created)},"xml_equal_except_caseDate":True,
            "input_sha256":config["input_model_sha256"],"runtime_checks":result["runtime_checks"]}
    (root/"equivalence.json").write_text(json.dumps(report,indent=2)+"\n")


def test_runtime_modelfiles_n1_and_sensitivity(isolated_conversion):
    root,paths,config,reference,result=isolated_conversion
    p=Path(result["artifact"])
    data=json.loads((p/"runtime_crosscheck.json").read_text())
    assert data["comparison"]["passed"] and len(data["comparison"]["checks"])==188
    assert all(c["passed"] is True for c in data["comparison"]["checks"])
    assert len(data["candidate"]["n1"])==3
    assert [r["isolated_bus_count"] for r in data["candidate"]["n1"]]==[8,0,11]
    assert all(r["post_status"]=="CONVERGED" for r in data["candidate"]["n1"])
    assert sum(r["candidate_count"] for r in data["candidate"]["sensitivity"])==132
    assert all(r["finite_difference_check"]["passed"] for r in data["candidate"]["sensitivity"])
    assert data["candidate"]["loadflow_parameters"]==data["reference"]["loadflow_parameters"]


def test_repeat_integrity_and_existing_result_protection(isolated_conversion):
    root,paths,config,reference,result=isolated_conversion
    original=Path(result["artifact"])
    before={p.name:sha256(p) for p in original.iterdir()}
    with pytest.raises(ValueError,match="existing artifact"):
        migrated.convert_for_verification(paths,original)
    again=migrated.convert_for_verification(paths,paths.output_dir/"repeat")
    repeat=Path(again["artifact"])
    assert xml_model(original/"mn35k_synthetic_powsybl.xiidm")[0]==xml_model(repeat/"mn35k_synthetic_powsybl.xiidm")[0]
    for p in original.glob("*.csv"):assert p.read_bytes()==(repeat/p.name).read_bytes()
    assert before=={p.name:sha256(p) for p in original.iterdir()}


def test_regenerated_netcdf_is_not_an_implicit_substitute(source_paths,tmp_path):
    config=inputs.read_settings(source_paths)
    regenerated=source_paths.ems_root/"outputs/mn_stage2c/generated/reactive-01/synthetic_reactive_support_base.nc"
    assert sha256(regenerated)!=config["input_model_sha256"]
    selected=tmp_path/"regenerated-config.json"
    config["input_model"]=str(regenerated);config["input_model_sha256"]=sha256(regenerated)
    selected.write_text(json.dumps(config))
    with pytest.raises(ValueError,match="pinned bytes"):
        migrated.convert_for_verification(source_paths,source_paths.output_dir/"wrong-model",config_path=selected)
