"""Stage-one Mongolia preparation: injected paths, immutable evidence and isolated probes."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
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


STAGE3C_CODE_HASHES=ROOT/'docs/validation/mongolia-pipeline-stage3c-code-hashes.json'
PHASE2_CODE_HASHES=ROOT/'docs/validation/kems-console-phase2-code-hashes.json'
PHASE3_CODE_HASHES=ROOT/'docs/validation/kems-console-phase3-code-hashes.json'
PHASE4_CODE_HASHES=ROOT/'docs/validation/kems-console-phase4-code-hashes.json'
WEB_APP='ems_code.web_app.py'


def phase2_code_changes(stage3c,phase2):
    """Phase 2 pins extend, never rewrite, the Stage 1 contract and the exact Stage 3C record bytes."""
    audit,later=(json.loads(path.read_text()) for path in (stage3c,phase2))
    assert later['schema']=='kems-phase2-code-change-audit-1'
    assert later['stage1_contract_sha256']==inventory.sha256(inventory.CONTRACT)
    assert later['stage3c_record_sha256']==inventory.sha256(stage3c)
    assert set(later['files'])=={WEB_APP}
    for role,change in later['files'].items():
        assert change['path']==audit['files'][role]['path']
        assert change['before']==audit['files'][role]['after']!=change['after']
        assert (change['commits'][0]['sha256'],change['commits'][-1]['sha256'])==(change['before'],change['after'])
    return later['files']


@pytest.mark.parametrize('target,mutation',[
    (0,lambda r,o:r['files'][WEB_APP].update(after=o['files'][WEB_APP]['after'])),
    (1,lambda r,o:r['files'][WEB_APP].update(before=o['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after=r['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(path='ui/console/api-map.js')),
    (1,lambda r,o:r['files'][WEB_APP]['commits'].pop()),
    (1,lambda r,o:r['files'].update({'ems_code.src/ai_ems/config.py':o['files']['ems_code.src/ai_ems/config.py']})),
    (1,lambda r,o:r.update(stage1_contract_sha256='0'*64)),
    (1,lambda r,o:r.update(stage3c_record_sha256='0'*64))],
    ids=['stage3c-after-rewritten','phase2-skips-stage3c','phase2-no-change','phase2-other-path',
         'phase2-stale-commits','phase2-extra-role','phase2-other-contract','phase2-other-stage3c-record'])
def test_phase2_code_record_cannot_rewrite_or_bypass_stage3c(tmp_path,target,mutation):
    files=[tmp_path/'stage3c.json',tmp_path/'phase2.json']
    for file,source in zip(files,(STAGE3C_CODE_HASHES,PHASE2_CODE_HASHES)):file.write_bytes(source.read_bytes())
    assert phase2_code_changes(*files)
    records=[json.loads(file.read_text()) for file in files]
    mutation(records[target],records[1-target])
    files[target].write_text(json.dumps(records[target],indent=2)+'\n')
    with pytest.raises(AssertionError):phase2_code_changes(*files)


def phase3_code_changes(phase2,phase3):
    """Phase 3 pins extend, never rewrite, the Stage 1 contract and the exact Phase 2 record bytes."""
    previous,later=(json.loads(path.read_text()) for path in (phase2,phase3))
    assert later['schema']=='kems-phase3-code-change-audit-1'
    assert later['stage1_contract_sha256']==inventory.sha256(inventory.CONTRACT)
    assert later['phase2_record_sha256']==inventory.sha256(phase2)
    assert set(later['files'])=={WEB_APP}
    for role,change in later['files'].items():
        assert change['path']==previous['files'][role]['path']
        assert change['before']==previous['files'][role]['after']!=change['after']
        # Written before its own commit: the base commit holds the 'before' bytes.
        assert re.fullmatch('[0-9a-f]{40}',change['base_commit'])
    return later['files']


@pytest.mark.parametrize('target,mutation',[
    (0,lambda r,o:r['files'][WEB_APP].update(after=o['files'][WEB_APP]['after'])),
    (1,lambda r,o:r['files'][WEB_APP].update(before=o['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after=r['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(path='ui/console/api-analysis.js')),
    (1,lambda r,o:r['files'][WEB_APP].update(base_commit='d542ba3')),
    (1,lambda r,o:r['files'].update({'ems_code.src/ai_ems/config.py':{**r['files'][WEB_APP],'path':'src/ai_ems/config.py'}})),
    (1,lambda r,o:r.update(stage1_contract_sha256='0'*64)),
    (1,lambda r,o:r.update(phase2_record_sha256='0'*64))],
    ids=['phase2-after-rewritten','phase3-skips-phase2','phase3-no-change','phase3-other-path',
         'phase3-short-commit','phase3-extra-role','phase3-other-contract','phase3-other-phase2-record'])
def test_phase3_code_record_cannot_rewrite_or_bypass_phase2(tmp_path,target,mutation):
    files=[tmp_path/'phase2.json',tmp_path/'phase3.json']
    for file,source in zip(files,(PHASE2_CODE_HASHES,PHASE3_CODE_HASHES)):file.write_bytes(source.read_bytes())
    assert phase3_code_changes(*files)
    records=[json.loads(file.read_text()) for file in files]
    mutation(records[target],records[1-target])
    files[target].write_text(json.dumps(records[target],indent=2)+'\n')
    with pytest.raises(AssertionError):phase3_code_changes(*files)


def phase4_code_changes(phase3,phase4):
    """PH4-01 extends the exact Phase 3 bytes; all older evidence stays immutable."""
    previous,later=(json.loads(path.read_text()) for path in (phase3,phase4))
    assert previous['schema']=='kems-phase3-code-change-audit-1'
    assert later['schema']=='kems-phase4-code-change-audit-1'
    assert later['stage1_contract_sha256']==inventory.sha256(inventory.CONTRACT)
    assert later['phase3_record_sha256']==inventory.sha256(phase3)
    assert set(later['files'])=={WEB_APP}
    for role,change in later['files'].items():
        assert change['path']==previous['files'][role]['path']=='web_app.py'
        assert change['before']==previous['files'][role]['after']!=change['after']
        assert re.fullmatch('[0-9a-f]{64}',change['after'])
        assert re.fullmatch('[0-9a-f]{40}',change['base_commit'])
    return later['files']


@pytest.mark.parametrize('target,mutation',[
    (0,lambda r,o:r['files'][WEB_APP].update(after=o['files'][WEB_APP]['after'])),
    (1,lambda r,o:r['files'][WEB_APP].update(before=o['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after=r['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(path='ui/console/api-chat.js')),
    (1,lambda r,o:r['files'][WEB_APP].update(base_commit='3f7b179')),
    (1,lambda r,o:r['files'].update({'ems_code.src/ai_ems/config.py':{**r['files'][WEB_APP],'path':'src/ai_ems/config.py'}})),
    (1,lambda r,o:r.update(stage1_contract_sha256='0'*64)),
    (1,lambda r,o:r.update(phase3_record_sha256='0'*64)),
    (1,lambda r,o:r['files'][WEB_APP].update(after='invalid-hash')),
    (1,lambda r,o:r.update(schema='kems-phase3-code-change-audit-1'))],
    ids=['phase3-after-rewritten','phase4-skips-phase3','phase4-no-change','phase4-other-path',
         'phase4-short-commit','phase4-extra-role','phase4-other-contract','phase4-other-phase3-record',
         'phase4-invalid-after','phase4-wrong-schema'])
def test_phase4_code_record_cannot_rewrite_or_bypass_phase3(tmp_path,target,mutation):
    # Mutate isolated JSON copies only; never the working tree or original evidence.
    files=[tmp_path/'phase3.json',tmp_path/'phase4.json']
    for file,source in zip(files,(PHASE3_CODE_HASHES,PHASE4_CODE_HASHES)):file.write_bytes(source.read_bytes())
    assert phase4_code_changes(*files)
    records=[json.loads(file.read_text()) for file in files]
    mutation(records[target],records[1-target])
    files[target].write_text(json.dumps(records[target],indent=2)+'\n')
    with pytest.raises(AssertionError):phase4_code_changes(*files)


def test_phase4_code_record_pins_only_console_chat_asset(tmp_path):
    change=phase4_code_changes(PHASE3_CODE_HASHES,PHASE4_CODE_HASHES)[WEB_APP]
    # PH4-02 changed web_app.py later: keep this historical check on the PH4-01
    # bytes recovered through the newer records' edits, not on the current HEAD.
    later=ph402_code_changes(PHASE4_CODE_HASHES,PH402_CODE_HASHES)[WEB_APP]
    current=tmp_path/'ph401-web_app.py'
    current.write_bytes(ph402_versions(later,ph402_after_bytes())[0])
    assert inventory.sha256(current)==change['after']
    source=current.read_bytes()
    addition=b'"api-chat.js", '
    assert source.count(addition)==1
    previous=tmp_path/'phase3-web_app.py'
    previous.write_bytes(source.replace(addition,b'',1))
    assert inventory.sha256(previous)==change['before']


PH402_CODE_HASHES=ROOT/'docs/validation/kems-console-ph402-code-hashes.json'


def git_blob_id(data):
    return hashlib.sha1(b'blob %d\0'%len(data)+data).hexdigest()


def ph402_code_changes(phase4,ph402):
    """PH4-02B1/B2 extend the exact PH4-01 record bytes; all older evidence stays immutable."""
    previous,later=(json.loads(path.read_text()) for path in (phase4,ph402))
    assert previous['schema']=='kems-phase4-code-change-audit-1'
    assert later['schema']=='kems-ph402-code-change-audit-1'
    assert later['stage1_contract_sha256']==inventory.sha256(inventory.CONTRACT)
    assert later['phase4_record_sha256']==inventory.sha256(phase4)
    assert set(later['files'])=={WEB_APP}
    for role,change in later['files'].items():
        assert change['path']==previous['files'][role]['path']=='web_app.py'
        assert change['before']==previous['files'][role]['after']!=change['after']
        steps=change['commits']
        # Ordered versions: the PH4-01 pin, then every later commit with its exact edits.
        assert len(steps)>=2 and 'edits' not in steps[0] and all(step['edits'] for step in steps[1:])
        assert (steps[0]['sha256'],steps[-1]['sha256'])==(change['before'],change['after'])
        for step in steps:
            assert re.fullmatch('[0-9a-f]{40}',step['commit']) and re.fullmatch('[0-9a-f]{40}',step['blob'])
            assert re.fullmatch('[0-9a-f]{64}',step['sha256']) and step['change']
        assert len({s['commit'] for s in steps})==len({s['blob'] for s in steps})==len({s['sha256'] for s in steps})==len(steps)
    return later['files']


def ph402_versions(change,after_bytes):
    """Recover every pinned version from the newest bytes by reversing only the recorded edits."""
    versions=[after_bytes]
    for step in reversed(change['commits'][1:]):
        assert (hashlib.sha256(versions[0]).hexdigest(),git_blob_id(versions[0]))==(step['sha256'],step['blob'])
        text=versions[0].decode('utf-8')
        for edit in reversed(step['edits']):
            assert all(line and line[0] in ' -+' for line in edit) and any(line[0]!=' ' for line in edit)
            new='\n'.join(line[1:] for line in edit if line[0] in ' +')
            old='\n'.join(line[1:] for line in edit if line[0] in ' -')
            assert new and text.count(new)==1
            text=text.replace(new,old,1)
        versions.insert(0,text.encode('utf-8'))
    first=change['commits'][0]
    assert (hashlib.sha256(versions[0]).hexdigest(),git_blob_id(versions[0]))==(first['sha256'],first['blob'])
    return versions


def ph402_chain(phase4,ph402,after_bytes):
    change=ph402_code_changes(phase4,ph402)[WEB_APP]
    return change,ph402_versions(change,after_bytes)


def test_ph402_code_record_recovers_every_pinned_web_app_version():
    change,versions=ph402_chain(PHASE4_CODE_HASHES,PH402_CODE_HASHES,ph402_after_bytes())
    # PH4-02 'after' is a historical pin now: the newer PH4-02B3-1 record recovers its bytes.
    assert hashlib.sha256(ph402_after_bytes()).hexdigest()==change['after']
    assert [hashlib.sha256(version).hexdigest() for version in versions]==[step['sha256'] for step in change['commits']]
    assert len(versions)==len(change['commits'])==3 and versions[0]!=versions[-1]
    assert change['before']==phase4_code_changes(PHASE3_CODE_HASHES,PHASE4_CODE_HASHES)[WEB_APP]['after']


@pytest.mark.parametrize('edit',[
    lambda source:source+b'\n# unrecorded later change\n',
    lambda source:source.replace(b'@guarded(lambda: network)\ndef network_map',b'def network_map',1),
    lambda source:source.replace(b'"api-chat.js", ',b'',1)],
    ids=['appended-change','reverted-b1-guard','reverted-ph401-asset'])
def test_ph402_chain_rejects_unrecorded_web_app_bytes(edit):
    source=ph402_after_bytes()
    changed=edit(source)
    assert changed!=source
    with pytest.raises(AssertionError):ph402_chain(PHASE4_CODE_HASHES,PH402_CODE_HASHES,changed)


@pytest.mark.parametrize('target,mutation',[
    (0,lambda r,o:r['files'][WEB_APP].update(after=o['files'][WEB_APP]['after'])),
    (1,lambda r,o:r['files'][WEB_APP].update(before=o['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after=r['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after=r['files'][WEB_APP]['commits'][1]['sha256'])),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][1].update(sha256='0'*64)),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][1].update(blob='0'*40)),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][1].update(sha256=r['files'][WEB_APP]['commits'][2]['sha256'])),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][1].update(commit=r['files'][WEB_APP]['commits'][2]['commit'])),
    (1,lambda r,o:r['files'][WEB_APP]['commits'].insert(1,r['files'][WEB_APP]['commits'].pop(2))),
    (1,lambda r,o:r['files'][WEB_APP]['commits'].reverse()),
    (1,lambda r,o:r['files'][WEB_APP]['commits'].pop(1)),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][2]['edits'].pop()),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][1]['edits'][0].insert(1,'+# unrecorded')),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][2]['edits'].append(['- )','+ )'])),
    (1,lambda r,o:r['files'][WEB_APP]['commits'][0].update(edits=[[' )']])),
    (1,lambda r,o:r['files'][WEB_APP].update(path='ui/console/api-chat.js')),
    (1,lambda r,o:r.update(phase4_record_sha256='0'*64)),
    (1,lambda r,o:r.update(phase4_record_sha256=inventory.sha256(PHASE3_CODE_HASHES))),
    (1,lambda r,o:r.update(stage1_contract_sha256='0'*64)),
    (1,lambda r,o:r.update(schema='kems-phase4-code-change-audit-1')),
    (1,lambda r,o:r['files'].update({'ems_code.src/ai_ems/config.py':{**r['files'][WEB_APP],'path':'src/ai_ems/config.py'}}))],
    ids=['ph401-after-rewritten','ph402-skips-ph401','ph402-no-change','ph402-after-not-newest','ph402-intermediate-hash',
         'ph402-intermediate-blob','ph402-intermediate-hash-reused','ph402-commit-reused','ph402-steps-swapped','ph402-steps-reversed',
         'ph402-b1-step-omitted','ph402-edit-omitted','ph402-edit-altered','ph402-ambiguous-edit','ph402-first-step-edits',
         'ph402-other-path','ph402-other-phase4-record','ph402-older-phase3-record','ph402-other-contract',
         'ph402-wrong-schema','ph402-extra-role'])
def test_ph402_code_record_cannot_rewrite_or_bypass_ph401(tmp_path,target,mutation):
    # Mutate isolated JSON copies only; never the working tree or original evidence.
    files=[tmp_path/'phase4.json',tmp_path/'ph402.json']
    for file,source in zip(files,(PHASE4_CODE_HASHES,PH402_CODE_HASHES)):file.write_bytes(source.read_bytes())
    current=ph402_after_bytes()
    assert ph402_chain(*files,current)
    records=[json.loads(file.read_text()) for file in files]
    mutation(records[target],records[1-target])
    files[target].write_text(json.dumps(records[target],indent=2)+'\n')
    with pytest.raises(AssertionError):ph402_chain(*files,current)



PH402B31_CODE_HASHES=ROOT/'docs/validation/kems-console-ph402b31-code-hashes.json'


def undo_edits(data,edits):
    """Reverse recorded line edits; each recorded result text must occur exactly once."""
    text=data.decode('utf-8')
    for edit in reversed(edits):
        assert all(line and line[0] in ' -+' for line in edit) and any(line[0]!=' ' for line in edit)
        new='\n'.join(line[1:] for line in edit if line[0] in ' +')
        old='\n'.join(line[1:] for line in edit if line[0] in ' -')
        assert new and text.count(new)==1
        text=text.replace(new,old,1)
    return text.encode('utf-8')


def ph402b31_code_changes(ph402,b31):
    """PH4-02B3-1 extends the exact PH4-02 record bytes; all older evidence stays immutable."""
    previous,later=(json.loads(path.read_text()) for path in (ph402,b31))
    assert previous['schema']=='kems-ph402-code-change-audit-1'
    assert later['schema']=='kems-ph402b31-code-change-audit-1'
    assert later['stage1_contract_sha256']==inventory.sha256(inventory.CONTRACT)
    assert later['ph402_record_sha256']==inventory.sha256(ph402)
    assert set(later['files'])=={WEB_APP}
    for role,change in later['files'].items():
        assert change['path']==previous['files'][role]['path']=='web_app.py'
        assert change['before']==previous['files'][role]['after']!=change['after']
        assert change['before_blob']==previous['files'][role]['commits'][-1]['blob']!=change['after_blob']
        assert re.fullmatch('[0-9a-f]{40}',change['base_commit']) and re.fullmatch('[0-9a-f]{40}',change['after_blob'])
        assert re.fullmatch('[0-9a-f]{64}',change['after']) and change['change'] and change['edits']
    return later['files']


def ph402b31_versions(change,after_bytes):
    """[PH4-02 'after', PH4-02B3-1 'after'] bytes, recovered by reversing only the recorded edits."""
    assert (hashlib.sha256(after_bytes).hexdigest(),git_blob_id(after_bytes))==(change['after'],change['after_blob'])
    before=undo_edits(after_bytes,change['edits'])
    assert (hashlib.sha256(before).hexdigest(),git_blob_id(before))==(change['before'],change['before_blob'])
    return [before,after_bytes]


def ph402_after_bytes():
    """PH4-02 'after' web_app.py bytes, recovered from HEAD through every newer record."""
    change=ph402b31_code_changes(PH402_CODE_HASHES,PH402B31_CODE_HASHES)[WEB_APP]
    return ph402b31_versions(change,ph402b31_after_bytes())[0]


def full_web_app_chain(phase4,ph402,b31,head_bytes):
    """HEAD -> PH4-02B3-1 -> PH4-02 -> PH4-01 bytes; every version hash- and blob-pinned."""
    newest=ph402b31_code_changes(ph402,b31)[WEB_APP]
    b2,head=ph402b31_versions(newest,head_bytes)
    older=ph402_code_changes(phase4,ph402)[WEB_APP]
    return [*ph402_versions(older,b2)[:-1],b2,head]


def test_ph402b31_code_record_chains_head_back_to_ph401():
    change=ph402b31_code_changes(PH402_CODE_HASHES,PH402B31_CODE_HASHES)[WEB_APP]
    # PH4-02B3-1 'after' is a historical pin now: the newer PH4-02B3-2 record recovers its
    # bytes. PH4-01 and PH4-02 'after' stay historical pins as before.
    b31_bytes=ph402b31_after_bytes()
    assert hashlib.sha256(b31_bytes).hexdigest()==change['after']
    versions=full_web_app_chain(PHASE4_CODE_HASHES,PH402_CODE_HASHES,PH402B31_CODE_HASHES,b31_bytes)
    pins=[step['sha256'] for step in json.loads(PH402_CODE_HASHES.read_text())['files'][WEB_APP]['commits']]
    assert [hashlib.sha256(version).hexdigest() for version in versions]==[*pins,change['after']]
    assert pins[0]==phase4_code_changes(PHASE3_CODE_HASHES,PHASE4_CODE_HASHES)[WEB_APP]['after']


def test_ph402b31_versions_verify_their_own_before_pin():
    # Used alone (ph402_after_bytes), the record must reject a wrong reconstruction itself.
    change=copy.deepcopy(ph402b31_code_changes(PH402_CODE_HASHES,PH402B31_CODE_HASHES)[WEB_APP])
    head=ph402b31_after_bytes()
    assert ph402b31_versions(change,head)[0]==ph402_after_bytes()
    change['edits'].pop()
    with pytest.raises(AssertionError):ph402b31_versions(change,head)


@pytest.mark.parametrize('edit',[
    lambda source:source+b'\n# unrecorded later change\n',
    lambda source:source.replace(b'    if _analysis_opt_in(request):\n        return _analysis_chat(grid_id, request)\n',b'',1),
    lambda source:source.replace(b'"api-chat.js", ',b'',1)],
    ids=['appended-change','reverted-b31-dispatch','reverted-ph401-asset'])
def test_ph402b31_chain_rejects_unrecorded_web_app_bytes(edit):
    source=ph402b31_after_bytes()
    changed=edit(source)
    assert changed!=source
    with pytest.raises(AssertionError):
        full_web_app_chain(PHASE4_CODE_HASHES,PH402_CODE_HASHES,PH402B31_CODE_HASHES,changed)


@pytest.mark.parametrize('target,mutation',[
    (0,lambda r,o:r['files'][WEB_APP].update(after=o['files'][WEB_APP]['after'])),
    (1,lambda r,o:r['files'][WEB_APP].update(before=o['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after=r['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after='0'*64)),
    (1,lambda r,o:r['files'][WEB_APP].update(after_blob='0'*40)),
    (1,lambda r,o:r['files'][WEB_APP].update(before_blob='0'*40)),
    (1,lambda r,o:r['files'][WEB_APP].update(base_commit='b0fbeb9')),
    (1,lambda r,o:r['files'][WEB_APP]['edits'].pop()),
    (1,lambda r,o:r['files'][WEB_APP]['edits'][0].insert(1,'+# unrecorded')),
    (1,lambda r,o:r['files'][WEB_APP]['edits'].append(['- )','+ )'])),
    (1,lambda r,o:r['files'][WEB_APP].update(edits=[])),
    (1,lambda r,o:r['files'][WEB_APP].update(path='ui/console/api-chat.js')),
    (1,lambda r,o:r.update(ph402_record_sha256='0'*64)),
    (1,lambda r,o:r.update(ph402_record_sha256=inventory.sha256(PHASE4_CODE_HASHES))),
    (1,lambda r,o:r.update(stage1_contract_sha256='0'*64)),
    (1,lambda r,o:r.update(schema='kems-ph402-code-change-audit-1')),
    (1,lambda r,o:r['files'].update({'ems_code.src/ai_ems/config.py':{**r['files'][WEB_APP],'path':'src/ai_ems/config.py'}}))],
    ids=['ph402-after-rewritten','b31-skips-ph402','b31-no-change','b31-after-hash','b31-after-blob',
         'b31-before-blob','b31-short-base-commit','b31-edit-omitted','b31-edit-altered','b31-ambiguous-edit',
         'b31-no-edits','b31-other-path','b31-other-ph402-record','b31-older-phase4-record','b31-other-contract',
         'b31-wrong-schema','b31-extra-role'])
def test_ph402b31_code_record_cannot_rewrite_or_bypass_ph402(tmp_path,target,mutation):
    # Mutate isolated JSON copies only; never the working tree or original evidence.
    files=[tmp_path/'ph402.json',tmp_path/'ph402b31.json']
    for file,source in zip(files,(PH402_CODE_HASHES,PH402B31_CODE_HASHES)):file.write_bytes(source.read_bytes())
    head=ph402b31_after_bytes()
    assert full_web_app_chain(PHASE4_CODE_HASHES,*files,head)
    records=[json.loads(file.read_text()) for file in files]
    mutation(records[target],records[1-target])
    files[target].write_text(json.dumps(records[target],indent=2)+'\n')
    with pytest.raises(AssertionError):full_web_app_chain(PHASE4_CODE_HASHES,*files,head)


PH402B32_CODE_HASHES=ROOT/'docs/validation/kems-console-ph402b32-code-hashes.json'


def ph402b32_code_changes(b31,b32):
    """PH4-02B3-2 extends the exact PH4-02B3-1 record bytes; all older evidence stays immutable."""
    previous,later=(json.loads(path.read_text()) for path in (b31,b32))
    assert previous['schema']=='kems-ph402b31-code-change-audit-1'
    assert later['schema']=='kems-ph402b32-code-change-audit-1'
    assert later['stage1_contract_sha256']==inventory.sha256(inventory.CONTRACT)
    assert later['ph402b31_record_sha256']==inventory.sha256(b31)
    assert set(later['files'])=={WEB_APP}
    for role,change in later['files'].items():
        assert change['path']==previous['files'][role]['path']=='web_app.py'
        assert change['before']==previous['files'][role]['after']!=change['after']
        assert change['before_blob']==previous['files'][role]['after_blob']!=change['after_blob']
        assert re.fullmatch('[0-9a-f]{40}',change['base_commit']) and re.fullmatch('[0-9a-f]{40}',change['after_blob'])
        assert re.fullmatch('[0-9a-f]{64}',change['after']) and change['change'] and change['edits']
    return later['files']


def ph402b31_after_bytes():
    """PH4-02B3-1 'after' web_app.py bytes, recovered from HEAD through the PH4-02B3-2 record."""
    change=ph402b32_code_changes(PH402B31_CODE_HASHES,PH402B32_CODE_HASHES)[WEB_APP]
    # Same pin/edit format as the PH4-02B3-1 record: verifies both pins and every edit.
    return ph402b31_versions(change,(ROOT/'web_app.py').read_bytes())[0]


def full_web_app_chain_b32(phase4,ph402,b31,b32,head_bytes):
    """HEAD -> PH4-02B3-2 -> PH4-02B3-1 -> PH4-02 -> PH4-01 bytes; every version hash- and blob-pinned."""
    newest=ph402b32_code_changes(b31,b32)[WEB_APP]
    b31_bytes,head=ph402b31_versions(newest,head_bytes)
    return [*full_web_app_chain(phase4,ph402,b31,b31_bytes),head]


def test_ph402b32_code_record_chains_head_back_to_ph401():
    change=ph402b32_code_changes(PH402B31_CODE_HASHES,PH402B32_CODE_HASHES)[WEB_APP]
    # Only the newest record pins the current bytes; PH4-01, PH4-02 and PH4-02B3-1 'after' stay historical pins.
    assert inventory.sha256(ROOT/change['path'])==change['after']
    versions=full_web_app_chain_b32(PHASE4_CODE_HASHES,PH402_CODE_HASHES,PH402B31_CODE_HASHES,PH402B32_CODE_HASHES,
                                    (ROOT/'web_app.py').read_bytes())
    pins=[step['sha256'] for step in json.loads(PH402_CODE_HASHES.read_text())['files'][WEB_APP]['commits']]
    b31=json.loads(PH402B31_CODE_HASHES.read_text())['files'][WEB_APP]
    assert [hashlib.sha256(version).hexdigest() for version in versions]==[*pins,b31['after'],change['after']]
    assert change['before']==b31['after'] and b31['before']==pins[-1]
    assert pins[0]==phase4_code_changes(PHASE3_CODE_HASHES,PHASE4_CODE_HASHES)[WEB_APP]['after']


def test_ph402b32_versions_verify_their_own_before_pin():
    # Used alone (ph402b31_after_bytes), the record must reject a wrong reconstruction itself.
    change=copy.deepcopy(ph402b32_code_changes(PH402B31_CODE_HASHES,PH402B32_CODE_HASHES)[WEB_APP])
    head=(ROOT/'web_app.py').read_bytes()
    assert ph402b31_versions(change,head)[0]==ph402b31_after_bytes()
    change['edits'].pop()
    with pytest.raises(AssertionError):ph402b31_versions(change,head)


@pytest.mark.parametrize('mutation',[
    lambda r,o:r['files'][WEB_APP].update(before=o['files'][WEB_APP]['before']),
    lambda r,o:r['files'][WEB_APP].update(before_blob=o['files'][WEB_APP]['before_blob'])],
    ids=['before-not-b31-after','before-blob-not-b31-after-blob'])
def test_ph402b32_record_check_links_the_b31_pins_by_itself(tmp_path,mutation):
    # The downstream chain also catches these; the record check must not depend on it.
    files=[tmp_path/'ph402b31.json',tmp_path/'ph402b32.json']
    for file,source in zip(files,(PH402B31_CODE_HASHES,PH402B32_CODE_HASHES)):file.write_bytes(source.read_bytes())
    assert ph402b32_code_changes(*files)
    records=[json.loads(file.read_text()) for file in files]
    mutation(records[1],records[0])
    files[1].write_text(json.dumps(records[1],indent=2)+'\n')
    with pytest.raises(AssertionError):ph402b32_code_changes(*files)


@pytest.mark.parametrize('edit',[
    lambda source:source+b'\n# unrecorded later change\n',
    lambda source:source.replace(b'            return _run_envelope(url_grid_id, parsed)\n',
                                 b'            raise AnalysisApiError(409, "ANALYSIS_RUN_NOT_READY", "")\n',1),
    lambda source:source.replace(b'    if _analysis_opt_in(request):\n        return _analysis_chat(grid_id, request)\n',b'',1),
    lambda source:source.replace(b'"api-chat.js", ',b'',1)],
    ids=['appended-change','reverted-b32-run','reverted-b31-dispatch','reverted-ph401-asset'])
def test_ph402b32_chain_rejects_unrecorded_web_app_bytes(edit):
    source=(ROOT/'web_app.py').read_bytes()
    changed=edit(source)
    assert changed!=source
    with pytest.raises(AssertionError):
        full_web_app_chain_b32(PHASE4_CODE_HASHES,PH402_CODE_HASHES,PH402B31_CODE_HASHES,PH402B32_CODE_HASHES,changed)


@pytest.mark.parametrize('target,mutation',[
    (0,lambda r,o:r['files'][WEB_APP].update(after=o['files'][WEB_APP]['after'])),
    (1,lambda r,o:r['files'][WEB_APP].update(before=o['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after=r['files'][WEB_APP]['before'])),
    (1,lambda r,o:r['files'][WEB_APP].update(after='0'*64)),
    (1,lambda r,o:r['files'][WEB_APP].update(after_blob='0'*40)),
    (1,lambda r,o:r['files'][WEB_APP].update(before_blob='0'*40)),
    (1,lambda r,o:r['files'][WEB_APP].update(before_blob=o['files'][WEB_APP]['before_blob'])),
    (1,lambda r,o:r['files'][WEB_APP].update(base_commit='00f6edc')),
    (1,lambda r,o:r['files'][WEB_APP]['edits'].pop()),
    (1,lambda r,o:r['files'][WEB_APP]['edits'][0].insert(1,'+# unrecorded')),
    (1,lambda r,o:r['files'][WEB_APP]['edits'].append(['- )','+ )'])),
    (1,lambda r,o:r['files'][WEB_APP].update(edits=[])),
    (1,lambda r,o:r['files'][WEB_APP].update(path='ui/console/api-chat.js')),
    (1,lambda r,o:r.update(ph402b31_record_sha256='0'*64)),
    (1,lambda r,o:r.update(ph402b31_record_sha256=inventory.sha256(PH402_CODE_HASHES))),
    (1,lambda r,o:r.update(stage1_contract_sha256='0'*64)),
    (1,lambda r,o:r.update(schema='kems-ph402b31-code-change-audit-1')),
    (1,lambda r,o:r['files'].update({'ems_code.src/ai_ems/config.py':{**r['files'][WEB_APP],'path':'src/ai_ems/config.py'}}))],
    ids=['b31-after-rewritten','b32-skips-b31','b32-no-change','b32-after-hash','b32-after-blob',
         'b32-before-blob','b32-before-blob-from-b31','b32-short-base-commit','b32-edit-omitted','b32-edit-altered',
         'b32-ambiguous-edit','b32-no-edits','b32-other-path','b32-other-b31-record','b32-older-ph402-record',
         'b32-other-contract','b32-wrong-schema','b32-extra-role'])
def test_ph402b32_code_record_cannot_rewrite_or_bypass_ph402b31(tmp_path,target,mutation):
    # Mutate isolated JSON copies only; never the working tree or original evidence.
    files=[tmp_path/'ph402b31.json',tmp_path/'ph402b32.json']
    for file,source in zip(files,(PH402B31_CODE_HASHES,PH402B32_CODE_HASHES)):file.write_bytes(source.read_bytes())
    head=(ROOT/'web_app.py').read_bytes()
    assert full_web_app_chain_b32(PHASE4_CODE_HASHES,PH402_CODE_HASHES,*files,head)
    records=[json.loads(file.read_text()) for file in files]
    mutation(records[target],records[1-target])
    files[target].write_text(json.dumps(records[target],indent=2)+'\n')
    with pytest.raises(AssertionError):full_web_app_chain_b32(PHASE4_CODE_HASHES,PH402_CODE_HASHES,*files,head)

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
    # Stage 3C intentionally changes these three runtime entry files. Keep the
    # original 87-role contract STRICT: it must reject their old byte pins,
    # while every data/model/helper identity continues to match. The separate
    # review record pins both old and new bytes; no data tolerance is relaxed.
    audit=json.loads(STAGE3C_CODE_HASHES.read_text())
    assert audit['stage1_contract_sha256']==inventory.sha256(inventory.CONTRACT)
    expected={'ems_code.src/ai_ems/config.py','ems_code.src/ai_ems/grid_catalog.py','ems_code.web_app.py'}
    assert set(audit['files'])==expected
    failures=[c for c in first['checks'] if c['passed'] is False]
    assert not first['ready'] and {c['name'] for c in failures}=={'file:'+role for role in expected},failures
    assert len(first['files'])==87
    # Phases 2, 3, 4, PH4-02, PH4-02B3-1 and PH4-02B3-2 changed web_app.py again. Each
    # record chains onto the exact bytes of the one before it, Stage 3C, Phase 2/3/4,
    # PH4-02 and PH4-02B3-1 'after' stay the historical pins, and only the newest
    # record may pin the current bytes.
    later=phase2_code_changes(STAGE3C_CODE_HASHES,PHASE2_CODE_HASHES)
    latest=phase3_code_changes(PHASE2_CODE_HASHES,PHASE3_CODE_HASHES)
    latest={**latest,**phase4_code_changes(PHASE3_CODE_HASHES,PHASE4_CODE_HASHES)}
    latest={**latest,**ph402_code_changes(PHASE4_CODE_HASHES,PH402_CODE_HASHES)}
    latest={**latest,**ph402b31_code_changes(PH402_CODE_HASHES,PH402B31_CODE_HASHES)}
    latest={**latest,**ph402b32_code_changes(PH402B31_CODE_HASHES,PH402B32_CODE_HASHES)}
    for role,spec in audit['files'].items():
        assert first['files'][role]['sha256']==latest.get(role,later.get(role,spec))['after']
        assert first['files'][role]['expected_sha256']==spec['before']
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
