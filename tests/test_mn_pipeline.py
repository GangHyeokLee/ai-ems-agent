"""Stage-one Mongolia preparation: injected paths, immutable evidence and isolated probes."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.mn import inventory, preflight
from pipelines.mn.__main__ import main
from pipelines.mn.migration import inspect_migration
from pipelines.mn.paths import active_runtime, resolve


@pytest.fixture
def paths(tmp_path):
    ems, source, earth = (tmp_path / name for name in ("ems", "source", "engine"))
    for path in (ems, source, earth):
        path.mkdir()
    return resolve(SimpleNamespace(ems_root=ems, source_project=source, earth_checkout=earth), {})


def test_source_must_be_explicit():
    with pytest.raises(ValueError, match="source-project"):
        resolve(SimpleNamespace(), {})


def test_cli_precedes_environment_and_relative_paths_use_ems_root(paths, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    actual = resolve(SimpleNamespace(ems_root=paths.ems_root, source_project=paths.source_project,
                                    work_dir=Path("outputs/custom")),
                     {"KEMS_MN_SOURCE_PROJECT": str(tmp_path / "ignored"),
                      "KEMS_MN_WORK_DIR": "outputs/ignored", "KEMS_MN_OUTPUT_DIR": "outputs/env-output"})
    assert actual.source_project == paths.source_project
    assert actual.work_dir == paths.ems_root / "outputs/custom"
    assert actual.output_dir == paths.ems_root / "outputs/env-output"
    assert not actual.work_dir.exists()
    assert not actual.output_dir.exists()
    assert not actual.bundle_dir.exists()
    assert actual.safe_destination(Path("outputs/report.json")) == paths.ems_root / "outputs/report.json"


def test_environment_configuration_can_inject_all_roots(paths):
    actual = resolve(SimpleNamespace(), {"KEMS_MN_EMS_ROOT": str(paths.ems_root),
                     "KEMS_MN_SOURCE_PROJECT": str(paths.source_project),
                     "KEMS_MN_EARTH_CHECKOUT": str(paths.earth_checkout)})
    assert actual == paths


def test_python_symlink_keeps_virtual_environment(paths, tmp_path):
    binary = tmp_path / "base-python";binary.touch()
    venv = tmp_path / "test-venv/bin";venv.mkdir(parents=True)
    executable = venv / "python";executable.symlink_to(binary)
    actual = resolve(SimpleNamespace(ems_root=paths.ems_root, source_project=paths.source_project,
                                    runtime_python=executable), {})
    assert actual.runtime_python == executable
    assert actual.runtime_python != binary


@pytest.mark.parametrize("area", ["source", "engine", "ems-root", "src", ".git", "data", "pipelines"])
def test_writes_into_source_trees_are_rejected(paths, area):
    destination = {"source":paths.source_project, "engine":paths.earth_checkout,
                   "ems-root":paths.ems_root}.get(area, paths.ems_root / area)
    with pytest.raises(ValueError):paths.safe_destination(destination / "report.json")


def test_symlink_cannot_bypass_read_only_boundary(paths):
    link = paths.ems_root / "outputs/link";link.parent.mkdir();link.symlink_to(paths.source_project, target_is_directory=True)
    with pytest.raises(ValueError):paths.safe_destination(link / "report.json")
    assert not (paths.source_project / "report.json").exists()


@pytest.mark.parametrize("option", ["work_dir", "output_dir", "bundle_dir"])
def test_planned_destinations_cannot_point_to_source(paths, option):
    arguments = dict(ems_root=paths.ems_root, source_project=paths.source_project,
                     earth_checkout=paths.earth_checkout, **{option:paths.source_project / "new"})
    with pytest.raises(ValueError):resolve(SimpleNamespace(**arguments), {})


@pytest.mark.parametrize("work,output", [
    ("outputs/mn/work", "outputs/mn/work"),
    ("outputs/mn/work", "outputs/mn/work/generated"),
    ("outputs/mn/generated/work", "outputs/mn/generated"),
])
def test_work_and_output_cannot_overlap(paths, work, output):
    with pytest.raises(ValueError, match="작업 영역.*겹칠 수 없습니다"):
        resolve(SimpleNamespace(ems_root=paths.ems_root, source_project=paths.source_project,
                                work_dir=Path(work), output_dir=Path(output)), {})


@pytest.mark.parametrize("output", ["outputs/mn/generated", "outputs/mn/work-generated"])
def test_separate_work_and_output_are_allowed(paths, output):
    actual = resolve(SimpleNamespace(ems_root=paths.ems_root, source_project=paths.source_project,
                                     work_dir=Path("outputs/mn/work"), output_dir=Path(output)), {})
    assert actual.work_dir == paths.ems_root / "outputs/mn/work"
    assert actual.output_dir == paths.ems_root / output
    assert not actual.work_dir.exists()
    assert not actual.output_dir.exists()


@pytest.mark.parametrize("reverse", [False, True])
def test_symlink_cannot_hide_work_output_overlap(paths, reverse):
    work = paths.work_dir
    work.mkdir(parents=True)
    link = work.parent / "work-link"
    link.symlink_to(work, target_is_directory=True)
    output = link / "generated"
    if reverse:
        work, output = output, work
    with pytest.raises(ValueError, match="작업 영역.*겹칠 수 없습니다"):
        resolve(SimpleNamespace(ems_root=paths.ems_root, source_project=paths.source_project,
                                work_dir=work, output_dir=output), {})


def test_bundle_is_separate_and_existing_files_are_not_workspaces(paths):
    with pytest.raises(ValueError, match="번들"):
        resolve(SimpleNamespace(ems_root=paths.ems_root, source_project=paths.source_project,
                                bundle_dir=paths.work_dir / "bundle"), {})
    file = paths.ems_root / "outputs/file";file.parent.mkdir();file.touch()
    with pytest.raises(ValueError, match="파일"):
        resolve(SimpleNamespace(ems_root=paths.ems_root, source_project=paths.source_project, work_dir=file), {})


def test_runtime_overrides_are_inspected_not_activated_and_protected(paths, tmp_path, monkeypatch):
    monkeypatch.delenv("AI_EMS_MN_XIIDM_FILE", raising=False)
    actual = active_runtime(paths, {"xiidm":str(tmp_path / "model.xiidm"), "reports":str(tmp_path / "reports")})
    assert actual.runtime_xiidm == tmp_path / "model.xiidm"
    with pytest.raises(ValueError):actual.safe_destination(actual.runtime_xiidm)
    with pytest.raises(ValueError):actual.safe_destination(actual.runtime_reports / "missing.json")
    assert "AI_EMS_MN_XIIDM_FILE" not in __import__('os').environ


def test_report_replay_is_identical_and_differing_existing_report_is_never_overwritten(paths):
    target = paths.ems_root / "outputs/report.json"
    inventory.write_report(paths, target, {"schema":"example", "value":1})
    before = target.stat().st_mtime_ns;content = target.read_bytes()
    inventory.write_report(paths, target, {"value":1, "schema":"example"})
    assert target.stat().st_mtime_ns == before
    with pytest.raises(ValueError, match="기존 보고서"):
        inventory.write_report(paths, target, {"value":2})
    assert target.read_bytes() == content
    with pytest.raises(ValueError):inventory.write_report(paths, paths.source_project / "new.json", {})


def test_actual_bytes_are_required_even_when_names_match(paths):
    file = paths.source_project / "evidence.json";file.write_text('{"value":1}')
    contract = {"files":[dict(role="evidence", root="source", path=file.name,
                              sha256=inventory.sha256(file), scopes=["runtime"])]}
    checks = inventory.Checks();first = inventory.collect(paths, contract, checks)
    assert checks.ready()
    file.write_text('{"value":2}')
    checks = inventory.Checks();second = inventory.collect(paths, contract, checks)
    assert not checks.ready()
    compare = inventory.Checks();inventory.compare_files(second, {"schema":inventory.SCHEMA,"files":first}, compare)
    assert not compare.ready()


def test_missing_hashes_are_never_equated_and_role_loss_is_detected():
    checks = inventory.Checks()
    inventory.compare_files({"a":{}}, {"schema":inventory.SCHEMA,"files":{"a":{}}}, checks)
    assert not checks.ready()
    checks = inventory.Checks()
    inventory.compare_files({}, {"schema":inventory.SCHEMA,"files":{"a":{"sha256":"a"*64}}}, checks)
    assert not checks.ready()


@pytest.mark.parametrize("mutation", [lambda c:c['files'][0].update(path='../escape'),
    lambda c:c['files'][0].update(path='/absolute'), lambda c:c['files'][0].update(sha256='bad'),
    lambda c:c['files'].append(copy.deepcopy(c['files'][0]))])
def test_bad_contracts_are_rejected(tmp_path, mutation):
    contract = inventory.load_contract();mutation(contract)
    file = tmp_path / "contract.json";file.write_text(json.dumps(contract))
    with pytest.raises(ValueError):inventory.load_contract(file)


def test_missing_interpreter_is_reported_without_installation(paths):
    result = preflight.run_probe(paths.runtime_python, "versions", paths)
    assert result["ok"] is False and "FileNotFoundError" in result["error"]
    assert not paths.runtime_python.exists()


def test_runtime_and_build_failures_have_independent_scopes():
    checks = inventory.Checks();checks.equal("missing-build", False, True, ("build",))
    assert checks.ready("runtime") and not checks.ready("build") and not checks.ready()


def test_junit_counts_and_skip_reasons_remain_separate(tmp_path):
    one=tmp_path/'one.xml';two=tmp_path/'two.xml'
    one.write_text('<testsuites><testsuite><testcase classname="mn" name="real"/><testcase classname="mn" name="missing"><skipped message="model missing"/></testcase></testsuite></testsuites>')
    two.write_text('<testsuite><testcase classname="mn" name="real"><failure message="mismatch"/></testcase></testsuite>')
    runs = preflight.test_results([one,two])
    assert runs[0]['totals'] == dict(passed=1,failed=0,errors=0,skipped=1)
    assert runs[0]['skipped'][0]['reason']=='model missing'
    assert runs[1]['totals']['failed']==1
    one.write_text('invalid')
    with pytest.raises(ValueError,match='JUnit'):preflight.test_results([one])


def test_migration_audit_does_not_execute_scripts_and_finds_dependencies(paths):
    scripts=paths.source_project/'scripts';scripts.mkdir()
    sentinel=paths.source_project/'must-not-exist'
    (scripts/'a.py').write_text('from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n'+f'Path({str(sentinel)!r}).write_text("side effect")\n')
    (scripts/'b.py').write_text('import a\n')
    report=inspect_migration(paths.source_project)
    assert report['dependency_groups']==[['a'],['b']]
    assert report['scripts'][0]['path_couplings']
    assert not sentinel.exists()
    (scripts/'a.py').write_text('import b\n')
    assert not inspect_migration(paths.source_project)['ready']


def numeric_fixture():
    return dict(loadflow_parameters={'slack':'25'},sensitivity_definition={'step':.1},
        status=dict(scenario_id='synthetic',base_check=dict(provenance={'source_sha256':'a'*64},comparison=dict(slack_p_mw=1.,slack_q_mvar=-90.,reactor_q_absorbed_mvar=20.))),
        n1=[dict(outage_line_id='a',post_status='CONVERGED',isolated_buses=[],isolated_load_mw=0.,violated_equipment_count=0,
                 post_voltage_min_pu=.99,post_voltage_max_pu=1.05,max_line_p_change_mw=.3,max_line_q_change_mvar=20.,post_max_line_loading_percent=10.)],
        sensitivity=[dict(monitored_line_id='b',outage_line_id='a',analysis_status='COMPLETED',candidate_count=1,unit='MW/MW',
                          candidates=[dict(generator_id='g',sensitivity=-1.01)],finite_difference_check={'passed':True})])


@pytest.mark.parametrize("mutation", [lambda n:n['loadflow_parameters'].update(slack='26'),
    lambda n:n['n1'][0].update(post_voltage_min_pu=.5),lambda n:n['sensitivity'][0]['candidates'][0].update(sensitivity=float('nan')),
    lambda n:n['sensitivity'][0]['candidates'].clear(),lambda n:n['status'].update(scenario_id='other')])
def test_numerical_baseline_detects_control_value_and_id_changes(mutation):
    reference=numeric_fixture();current=copy.deepcopy(reference);mutation(current)
    checks=inventory.Checks();inventory.compare_numerical(current, reference, checks)
    assert not checks.ready()


def test_numerical_reference_roundtrip():
    reference=numeric_fixture();checks=inventory.Checks();inventory.compare_numerical(reference,reference,checks)
    assert checks.ready()


def test_cli_missing_requirements_returns_failure_and_does_not_write_source(paths):
    argv=['preflight','--ems-root',str(paths.ems_root),'--source-project',str(paths.source_project)]
    assert main(argv)==2
    assert main(argv+['--report',str(paths.source_project/'bad.json')])==2
    assert not (paths.source_project/'bad.json').exists()
    assert main(argv+['--numerical-reference','reference.json'])==2


@pytest.fixture(scope='module')
def real_paths():
    source=ROOT.parent/'mongolia-grid-poc'
    if not (source/'pypsa-earth/networks/MN_35k_poc/base.nc').is_file():
        pytest.skip('real Mongolia source data absent')
    return resolve(SimpleNamespace(ems_root=ROOT,source_project=source),{})


def test_real_asset_checks_are_deterministic_and_keep_model_roles(real_paths):
    contract=inventory.load_contract()
    paths,first=preflight.assemble(real_paths,contract)
    _,second=preflight.assemble(real_paths,contract)
    assert first==second
    assert first['ready'],[c for c in first['checks'] if c['passed'] is False]
    assert first['facts']['map_counts']['Bus']==173
    assert first['facts']['xiidm_counts']['Bus']==166
    assert first['netcdf_models']['map_model']['result']['format']=='HDF5/NetCDF4'
    assert first['netcdf_models']['pf_model']['result']['format']=='NetCDF3'
    assert first['numerical']['executed'] is False
    assert first['stage_capabilities']['activate_bundle'] is False
    assert first['environment']['runtime']['result']['packages']['pypsa'] is None


def test_build_preflight_is_independent_of_the_ems_virtual_environment(real_paths, tmp_path):
    contract=inventory.load_contract()
    no_runtime=replace(real_paths,runtime_python=tmp_path/'missing-python')
    _,report=preflight.assemble(no_runtime,contract,profile='build')
    assert report['readiness']['build'],[c for c in report['checks'] if c['passed'] is False and 'build' in c['scopes']]
    assert not report['readiness']['runtime']
    assert not report['active_runtime_configuration']['ok']
    assert not report['numerical']['executed']


@pytest.mark.parametrize('role,field',[('pf','map_json_sha256'),('reference','base_map_sha256')])
def test_real_cross_file_hash_relationship_tampering_is_rejected(real_paths,monkeypatch,role,field):
    contract=inventory.load_contract()
    config=preflight.run_probe(real_paths.runtime_python,'configuration',real_paths)
    paths=active_runtime(real_paths,config['result']);checks=inventory.Checks()
    files=inventory.collect(paths,contract,checks)
    original=inventory.read_json
    def altered(records,name):
        value=original(records,name)
        if name==role:
            (value['metadata'] if name=='pf' else value)[field]='0'*64
        return value
    monkeypatch.setattr(inventory,'read_json',altered)
    inventory.inspect_relationships(files,contract,checks)
    assert not checks.ready('runtime')
