"""B1: real isolated models and scripted graph/REST races; no LLM requests."""
import asyncio
import faulthandler
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pandas as pd
import pypowsybl as pp
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

import web_app
from ai_ems import network as network_module
from ai_ems.agent import graph as graph_module
from ai_ems.agent import tools as agent_tools
from ai_ems.network import load_identified_network, load_network, run_ac_load_flow
from ai_ems.network_access import (
    ModelIdentityUnavailable, ModelRevisionChanged, NetworkBusy,
    access_for, network_access,
)
from ai_ems.tools.network_tools import get_network_summary
from ai_ems.tools.security_tools import run_line_contingency
from ai_ems.tools.sensitivity_tools import rank_generator_sensitivities

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / 'data/KPG193_ver2_0_powsybl_full.mat'


@pytest.fixture(autouse=True)
def bounded_test():
    # A native call or a deadlocked worker cannot extend a test indefinitely.
    faulthandler.dump_traceback_later(45, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture
def model_file(tmp_path):
    path = tmp_path / 'ieee14.xiidm'
    path.write_text(pp.network.create_ieee14().save_to_string('XIIDM'))
    return path


@pytest.fixture
def model(model_file):
    return load_identified_network(model_file)


def tool_graph(tools):
    graph = StateGraph(MessagesState)
    graph.add_node('tools', ToolNode(tools, handle_tool_errors=True))
    graph.add_edge(START, 'tools')
    graph.add_edge('tools', END)
    return graph.compile()


def call(name, args=None):
    return {'messages': [AIMessage(content='', tool_calls=[{
        'name': name, 'args': args or {}, 'id': 'b1-call',
    }])]}


def test_exact_loaded_bytes_and_original_path_replacement(model_file, monkeypatch):
    payload = model_file.read_bytes()
    loader = network_module.load_network
    paths = []

    def replace_source(frozen):
        paths.append(Path(frozen))
        assert Path(frozen).read_bytes() == payload
        model_file.write_bytes(b'replaced after byte capture')
        return loader(frozen)

    monkeypatch.setattr(network_module, 'load_network', replace_source)
    n = load_identified_network(model_file)
    owner = access_for(n)
    assert owner._provenance['source_sha256'] == hashlib.sha256(payload).hexdigest()
    assert len(n.get_buses()) == 14
    first = owner.capture_revision()
    assert owner.capture_revision() == first
    assert paths[0] != model_file and not paths[0].exists()


def test_revision_reload_variant_and_first_ac_policy(model_file):
    n = load_identified_network(model_file)
    owner = access_for(n)
    buses = n.get_buses().copy(deep=True)
    first = owner.capture_revision()
    assert first.initial_state
    pd.testing.assert_frame_equal(n.get_buses(), buses)  # capture did not prepare/run AC
    assert first == owner.capture_revision()
    assert run_ac_load_flow(n)['converged']
    after = owner.capture_revision()
    assert not after.initial_state and after.revision != first.revision
    with pytest.raises(ModelRevisionChanged):
        owner.require_revision(first.revision)
    assert run_ac_load_flow(n)['converged']
    assert owner.capture_revision() == after
    new_network = load_identified_network(model_file)
    reloaded = access_for(new_network).capture_revision()
    assert reloaded.generation != after.generation and reloaded.revision != after.revision
    with network_access(n):
        n.clone_variant(n.get_working_variant_id(), 'another')
        n.set_working_variant('another')
    assert owner.capture_revision().revision != after.revision


@pytest.mark.parametrize('change', ['load', 'line', 'generator', 'connection', 'limit', 'settings'])
def test_meaningful_model_and_settings_changes(model, change):
    owner = access_for(model)
    before = owner.capture_revision()
    with network_access(model):
        if change == 'load':
            row = model.get_loads().iloc[0]
            model.update_loads(id=row.name, p0=float(row.p0) + 1)
        elif change == 'line':
            row = model.get_lines().iloc[0]
            model.update_lines(id=row.name, x=float(row.x) + 1)
        elif change == 'generator':
            row = model.get_generators().iloc[0]
            model.update_generators(id=row.name, target_p=float(row.target_p) + 1)
        elif change == 'connection':
            model.update_lines(id=model.get_lines().index[0], connected1=False)
        elif change == 'limit':
            model.create_loading_limits(element_id=model.get_lines().index[0], side='ONE',
                name='B1', type='APPARENT_POWER', value=50, acceptable_duration=-1)
        else:
            # Trusted configuration change under owner lock; actual execution uses these objects.
            owner._parameters['sensitivity'].load_flow_parameters.distributed_slack = False
    after = owner.capture_revision()
    assert after.revision != before.revision
    assert (after.settings_sha256 != before.settings_sha256) == (change == 'settings')


def test_snapshot_errors_are_closed_and_release_lock(model, monkeypatch):
    owner = access_for(model)
    before = owner.capture_revision()
    original = model.save_to_string

    def broken(*args, **kwargs):
        raise RuntimeError('no snapshot')

    monkeypatch.setattr(model, 'save_to_string', broken)
    with pytest.raises(ModelIdentityUnavailable): owner.capture_revision()
    with pytest.raises(ModelIdentityUnavailable): owner.require_revision(before.revision)
    assert get_network_summary(model)['buses'] == 14
    monkeypatch.setattr(model, 'save_to_string', original)
    assert owner.capture_revision() == before
    n = pp.network.create_ieee14()
    with pytest.raises(ModelIdentityUnavailable): access_for(n).capture_revision()
    monkeypatch.setattr(n, 'save_to_string', broken)
    access_for(n).identify({k: owner._provenance[k] for k in
        ['source_sha256', 'conversion_code', 'loader', 'import_parameters']}, network_module.LOADFLOW_PARAMETERS)
    monkeypatch.setattr(n, 'save_to_string', original)
    with pytest.raises(ModelIdentityUnavailable): access_for(n).capture_revision()
    assert run_ac_load_flow(n)['converged']  # revision failure does not break legacy PF


def test_explicit_custom_lf_executes_but_closes_default_identity(model):
    before = access_for(model).capture_revision()
    custom = pp.loadflow.Parameters(distributed_slack=True)
    result = run_ac_load_flow(model, parameters=custom)
    assert result['converged'] and result['load_flow_parameters']['distributed_slack']
    with pytest.raises(ModelIdentityUnavailable): access_for(model).require_revision(before.revision)
    assert get_network_summary(model)['buses'] == 14


def test_export_round_trip_preserves_electrical_data_and_limits(model):
    model.create_loading_limits(element_id='L2-4-1', side='ONE', name='B1',
                                type='APPARENT_POWER', value=100, acceptable_duration=-1)
    from ai_ems.network_access import EXPORT_PARAMETERS
    restored = pp.network.load_from_string('roundtrip.xiidm',
        model.save_to_string('XIIDM', parameters=EXPORT_PARAMETERS))
    for get in ['get_lines', 'get_loads', 'get_generators', 'get_2_windings_transformers', 'get_loading_limits']:
        pd.testing.assert_frame_equal(getattr(model, get)(all_attributes=True).sort_index(),
                                      getattr(restored, get)(all_attributes=True).sort_index(),
                                      check_exact=True)


def test_bound_settings_are_distinct_detached_and_executed(model, monkeypatch):
    owner = access_for(model)
    assert owner.parameters('loadflow').distributed_slack is False
    assert owner.parameters('security').load_flow_parameters.distributed_slack is False
    assert owner.parameters('sensitivity').load_flow_parameters.distributed_slack is True
    detached = owner.parameters('loadflow')
    detached.distributed_slack = True
    assert owner.parameters('loadflow').distributed_slack is False
    received = []
    real = pp.loadflow.run_ac

    def spy(n, **kwargs):
        received.append(kwargs)
        return real(n, **kwargs)

    monkeypatch.setattr(pp.loadflow, 'run_ac', spy)
    assert run_ac_load_flow(model)['converged']
    assert received[0]['parameters'].distributed_slack is False
    assert received[0]['provider'] == owner.provider('loadflow')
    real_security = pp.security.create_analysis
    real_sensitivity = pp.sensitivity.create_ac_analysis
    settings_seen = {}
    def security_factory():
        analysis = real_security()
        run = analysis.run_ac
        def invoke(n, **kwargs):
            settings_seen['security'] = kwargs
            return run(n, **kwargs)
        analysis.run_ac = invoke
        return analysis
    def sensitivity_factory():
        analysis = real_sensitivity()
        run = analysis.run
        def invoke(n, **kwargs):
            settings_seen['sensitivity'] = kwargs
            return run(n, **kwargs)
        analysis.run = invoke
        return analysis
    monkeypatch.setattr(pp.security, 'create_analysis', security_factory)
    monkeypatch.setattr(pp.sensitivity, 'create_ac_analysis', sensitivity_factory)
    assert run_line_contingency(model, 'L2-4-1')['post_status'] == 'CONVERGED'
    assert rank_generator_sensitivities(model, 'L2-4-1', 'L2-5-1', 5)['candidate_count'] == 5
    assert settings_seen['security']['parameters'].load_flow_parameters.distributed_slack is False
    assert settings_seen['sensitivity']['parameters'].load_flow_parameters.distributed_slack is True
    assert settings_seen['sensitivity']['provider'] == owner.provider('sensitivity')


def test_full_kpg_legacy_numeric_parity_and_hvdc_revision():
    identified = load_identified_network(FULL)
    legacy = load_network(FULL)
    owner = access_for(identified)
    initial = owner.capture_revision()
    assert owner._provenance['loader'] == 'kpg-full-matpower-v1'
    assert owner._provenance['source_sha256'] == hashlib.sha256(FULL.read_bytes()).hexdigest()
    for n in [identified, legacy]:
        assert len(n.get_buses()) == 193 and len(n.get_hvdc_lines()) == 2
    left = run_line_contingency(identified, 'LINE-16-28', ['LINE-16-22'])
    right = run_line_contingency(legacy, 'LINE-16-28', ['LINE-16-22'])
    assert left == right
    after = owner.capture_revision()
    assert not after.initial_state and initial.revision != after.revision
    assert rank_generator_sensitivities(identified, 'LINE-16-28', 'LINE-16-22', 5) == \
           rank_generator_sensitivities(legacy, 'LINE-16-28', 'LINE-16-22', 5)
    assert owner.capture_revision() == after
    for name, args in [('line_contingency', {'outage_line_id': 'LINE-16-28'}),
                       ('generator_sensitivity', {'outage_line_id': 'LINE-16-28', 'monitored_line_id': 'LINE-16-22'})]:
        a = next(t for t in agent_tools.create_agent_tools(identified) if t.name == name)
        b = next(t for t in agent_tools.create_agent_tools(legacy) if t.name == name)
        assert a.invoke(args) == b.invoke(args)
        assert a.response_format == b.response_format == 'content'
        assert a.tool_call_schema.model_json_schema() == b.tool_call_schema.model_json_schema()
    from ai_ems.network_access import EXPORT_PARAMETERS
    restored = pp.network.load_from_string('full-roundtrip.xiidm',
        identified.save_to_string('XIIDM', parameters=EXPORT_PARAMETERS))
    for get in ['get_substations', 'get_voltage_levels', 'get_lines', 'get_loads',
                'get_generators', 'get_2_windings_transformers', 'get_ratio_tap_changers',
                'get_ratio_tap_changer_steps', 'get_phase_tap_changers',
                'get_phase_tap_changer_steps', 'get_vsc_converter_stations',
                'get_hvdc_lines', 'get_loading_limits', 'get_reactive_capability_curve_points']:
        pd.testing.assert_frame_equal(getattr(identified, get)(all_attributes=True).sort_index(),
                                      getattr(restored, get)(all_attributes=True).sort_index(),
                                      check_exact=True)
    with network_access(identified):
        row = identified.get_hvdc_lines().iloc[0]
        identified.update_hvdc_lines(id=row.name, target_p=float(row.target_p) + 1)
    assert owner.capture_revision().revision != after.revision


def test_reentrancy_exception_and_independent_object(model):
    other = pp.network.create_ieee14()
    assert access_for(model) is access_for(model)
    assert access_for(model) is not access_for(other)
    with pytest.raises(ValueError):
        with network_access(model):
            with network_access(model):
                assert get_network_summary(model)['buses'] == 14
                raise ValueError('nested failure')
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(get_network_summary, model).result(timeout=5)['buses'] == 14
    with network_access(model):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(get_network_summary, other).result(timeout=5)['buses'] == 14
            with pytest.raises(NetworkBusy): pool.submit(get_network_summary, model).result(timeout=5)
    with pytest.raises(ValueError): run_line_contingency(model, 'NO_LINE')
    assert run_line_contingency(model, 'L2-4-1')['base_converged']


def test_rest_busy_includes_map_metadata_and_physics(model, monkeypatch):
    monkeypatch.setattr(web_app, 'network', model)
    ready, release = threading.Event(), threading.Event()
    def hold():
        with network_access(model):
            ready.set()
            assert release.wait(6)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(hold)
        try:
            assert ready.wait(3)
            client = TestClient(web_app.app)
            for path in ['/api/network-summary', '/api/network-map', '/api/grids/kr-kpg193/map',
                         '/api/security-analysis?outage_line_id=L2-4-1',
                         '/api/sensitivity-analysis?outage_line_id=L2-4-1']:
                response = client.get(path)
                assert response.status_code == 409, (path, response.text)
                assert response.json() == {'detail': 'ANALYSIS_BUSY'}
            with pytest.raises(NetworkBusy): web_app._sensitivity_result_for_ui({'candidates': []})
            import ai_ems.api.app as physics
            monkeypatch.setattr(physics, 'load_identified_network', lambda _: model)
            app = physics.create_app('unused')
            response = TestClient(app).post('/api/v1/security-analysis', json={'outage_line_id': 'L2-4-1'})
            assert response.status_code == 409
        finally: release.set()
        task.result(timeout=5)
    assert client.get('/api/network-summary').status_code == 200


def test_real_graph_and_rest_contend_without_holding_lock_while_model_waits(model, monkeypatch):
    monkeypatch.setattr(web_app, 'network', model)
    monkeypatch.setattr(web_app, 'agent_sessions', {})
    waiting, run_tool, engine, release = [threading.Event() for _ in range(4)]
    real = agent_tools.run_line_contingency
    def slow(*args, **kwargs):
        engine.set()
        assert release.wait(6)
        return real(*args, **kwargs)
    monkeypatch.setattr(agent_tools, 'run_line_contingency', slow)
    class ScriptedModel:
        step = 0
        def bind_tools(self, tools): return self
        def invoke(self, messages):
            self.step += 1
            if self.step == 1:
                waiting.set()
                assert run_tool.wait(6)
                return call('line_contingency', {'outage_line_id': 'L2-4-1'})['messages'][0]
            return AIMessage(content='검증 테스트')
    monkeypatch.setattr(graph_module, 'ChatOllama', lambda **kwargs: ScriptedModel())
    monkeypatch.setattr(web_app, 'agent_graph', graph_module.create_agent_graph(model))
    client = TestClient(web_app.app)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(client.post, '/api/chat', json={'session_id': 'b1-race', 'message': 'N-1'})
        try:
            assert waiting.wait(3)
            assert client.get('/api/network-summary').status_code == 200
            run_tool.set()
            assert engine.wait(3)
            assert client.get('/api/network-summary').status_code == 409
            direct = next(t for t in agent_tools.create_agent_tools(model) if t.name == 'line_list')
            with pytest.raises(NetworkBusy): direct.invoke({})
        finally:
            run_tool.set(); release.set()
        response = request.result(timeout=6)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {'session_id', 'answer', 'ui_updates'}
    assert response.json()['ui_updates'][0]['type'] == 'security'
    assert client.get('/api/network-summary').status_code == 200


def test_rest_owner_rejects_toolnode_and_private_tools_do_not_share_lock(model, monkeypatch):
    monkeypatch.setattr(web_app, 'network', model)
    real = web_app.run_line_contingency
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(6)
        return real(*args, **kwargs)
    monkeypatch.setattr(web_app, 'run_line_contingency', slow)
    monkeypatch.setattr(agent_tools, 'validate_balanced_redispatch', lambda **kwargs: {'private': True})
    tools = agent_tools.create_agent_tools(model)
    client = TestClient(web_app.app)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(client.get, '/api/security-analysis?outage_line_id=L2-4-1')
        try:
            assert entered.wait(3)
            output = tool_graph(tools).invoke(call('network_summary'))['messages'][-1]
            assert isinstance(output, ToolMessage) and output.status == 'error'
            assert 'ANALYSIS_BUSY' in output.content and output.artifact is None
            private = next(t for t in tools if t.name == 'balanced_redispatch_validation')
            assert private.invoke({'outage_line_id': 'a', 'monitored_line_id': 'b',
                'up_generator_id': 'u', 'down_generator_id': 'd', 'delta_mw': 1}) == {'private': True}
            # Mongolia actually loads/calculates its separate validated network.
            mn = client.get('/api/grids/mn-osm-35k/security-analysis?outage_line_id=751261406-1_0')
            assert mn.status_code == 200, mn.text
            assert mn.json()['grid_id'] == 'mn-osm-35k'
        finally: release.set()
        assert request.result(timeout=6).status_code == 200


def test_http_cancel_keeps_executing_worker_lock(model, monkeypatch):
    monkeypatch.setattr(web_app, 'network', model)
    real = web_app.run_line_contingency
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(6)
            return real(*args, **kwargs)
        finally: finished.set()
    monkeypatch.setattr(web_app, 'run_line_contingency', slow)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app.app), base_url='http://test') as client:
            task = asyncio.create_task(client.get('/api/security-analysis?outage_line_id=L2-4-1'))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                task.cancel()
                # Starlette may defer task cancellation while the sync worker runs.
                # Inspect the lock BEFORE waiting for cancellation acknowledgement.
                await asyncio.sleep(0)
                assert task.cancelling() > 0
                assert not finished.is_set()
                assert (await client.get('/api/network-summary')).status_code == 409
            finally:
                release.set()
                if not task.done(): task.cancel()
            assert await asyncio.to_thread(finished.wait, 5)
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 5)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if (await client.get('/api/network-summary')).status_code == 200: break
                await asyncio.sleep(0.01)
            else: pytest.fail('Executing worker failed to release lock')
    asyncio.run(asyncio.wait_for(scenario(), 15))


@pytest.mark.parametrize('route', ['rest', 'tool'])
def test_auto_sensitivity_keeps_lock_between_security_selection_and_mapping(monkeypatch, route):
    model = load_identified_network(FULL)
    monkeypatch.setattr(web_app, 'network', model)
    entered, release = threading.Event(), threading.Event()
    target = web_app if route == 'rest' else agent_tools
    original = target.select_most_severe_violated_line
    def select(*args):
        entered.set()
        assert release.wait(6)
        return original(*args)
    monkeypatch.setattr(target, 'select_most_severe_violated_line', select)
    client = TestClient(web_app.app)
    with ThreadPoolExecutor(max_workers=1) as pool:
        if route == 'rest':
            pending = pool.submit(client.get, '/api/sensitivity-analysis?outage_line_id=LINE-16-28')
        else:
            tool = next(t for t in agent_tools.create_agent_tools(model) if t.name == 'generator_sensitivity')
            pending = pool.submit(tool.invoke, {'outage_line_id': 'LINE-16-28'})
        try:
            assert entered.wait(3)
            assert client.get('/api/network-summary').status_code == 409
            with pytest.raises(NetworkBusy): get_network_summary(model)
        finally: release.set()
        result = pending.result(timeout=6)
    if route == 'rest':
        assert result.status_code == 200, result.text
        result = result.json()
        assert result['candidates'][0]['bus_id'] is not None
    assert result['candidate_count'] == 5
    assert result['monitored_line_id'] == 'LINE-16-22'


def test_engine_exception_releases_nested_rest_guard(model, monkeypatch):
    monkeypatch.setattr(web_app, 'network', model)
    original = pp.loadflow.run_ac
    def unavailable(*args, **kwargs):
        raise RuntimeError('injected engine failure')
    monkeypatch.setattr(pp.loadflow, 'run_ac', unavailable)
    client = TestClient(web_app.app, raise_server_exceptions=False)
    assert client.get('/api/security-analysis?outage_line_id=L2-4-1').status_code == 500
    assert client.get('/api/network-summary').status_code == 200
    monkeypatch.setattr(pp.loadflow, 'run_ac', original)
    assert client.get('/api/security-analysis?outage_line_id=L2-4-1').status_code == 200


def test_missing_engine_build_closes_revision_without_breaking_legacy(model_file, monkeypatch):
    monkeypatch.setattr(pp._pypowsybl, 'get_version_table', lambda: '')
    n = load_identified_network(model_file)
    with pytest.raises(ModelIdentityUnavailable): access_for(n).capture_revision()
    assert run_ac_load_flow(n)['converged']
