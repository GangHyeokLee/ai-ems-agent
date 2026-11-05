"""Launcher configuration is data, and app shutdown always closes its own controller."""
import importlib.util
from pathlib import Path
import sys
import os
import signal
import subprocess
import time

import httpx
import pytest

spec = importlib.util.spec_from_file_location('agent_launcher', Path(__file__).parents[1] / 'scripts/run_agent.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
probe_spec = importlib.util.spec_from_file_location('windows_caddy_probe', Path(__file__).with_name('windows_caddy_lifecycle_probe.py'))
windows_probe = importlib.util.module_from_spec(probe_spec)
probe_spec.loader.exec_module(windows_probe)


@pytest.fixture(scope='module')
def windows_binding_results():
    if not Path(windows_probe.ENGINE).is_file():
        pytest.skip('Windows PowerShell required for production Listener mock tests')
    return windows_probe.binding_policy_results()


@pytest.mark.parametrize('name', windows_probe.BINDING_CASES)
def test_windows_listener_rejects_every_unexpected_binding(windows_binding_results, name):
    expected = windows_probe.BINDING_CASES[name][1]
    result = windows_binding_results[name]
    assert result['state'] == expected
    assert result['pid'] == (42 if expected == 'local' else 0)


@pytest.fixture(autouse=True)
def no_process_settings(monkeypatch):
    for name in ('AI_EMS_CADDY_AUTOSTART', 'AI_EMS_CADDY_SCRIPT_WIN', 'AI_EMS_LLM_BASE_URL',
                 'AI_EMS_LLM_API_KEY', 'AI_EMS_CF_ACCESS_CLIENT_ID', 'AI_EMS_CF_ACCESS_CLIENT_SECRET'):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize('contents', [None, '', 'AI_EMS_CADDY_AUTOSTART=maybe\n',
                                     'AI_EMS_CADDY_AUTOSTART=true\n',
                                     'AI_EMS_CADDY_AUTOSTART=false\n'])
def test_incomplete_settings_leave_manual_operation_available(tmp_path, capsys, contents):
    if contents is not None:
        (tmp_path / '.env').write_text(contents)
    assert launcher.caddy_settings(tmp_path) is None
    assert '수동' in capsys.readouterr().err


def test_windows_path_is_literal_and_dotenv_is_never_executed(tmp_path):
    marker = tmp_path / 'executed'
    path = r"%LOCALAPPDATA%\Your App\renamed '$.ps1"
    (tmp_path / '.env').write_text(
        f'AI_EMS_CADDY_AUTOSTART=true\nAI_EMS_CADDY_SCRIPT_WIN={path}\n'
        f'IGNORED=$(touch {marker})\n', encoding='utf-8')
    assert launcher.caddy_settings(tmp_path) == path
    assert not marker.exists()


def test_exported_settings_override_dotenv_without_interpolation(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('AI_EMS_CADDY_AUTOSTART=true\nAI_EMS_CADDY_SCRIPT_WIN=${SECRET}\n')
    monkeypatch.setenv('AI_EMS_CADDY_AUTOSTART', 'false')
    assert launcher.caddy_settings(tmp_path) is None
    monkeypatch.setenv('AI_EMS_CADDY_AUTOSTART', 'true')
    assert launcher.caddy_settings(tmp_path) == '${SECRET}'


def test_app_remains_runnable_without_dotenv(tmp_path):
    assert launcher.run_app(tmp_path, [sys.executable, '-c', 'pass']) == 0


def test_app_failure_closes_only_its_session(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('AI_EMS_CADDY_AUTOSTART=true\nAI_EMS_CADDY_SCRIPT_WIN=C:\\Example\\any-name.ps1\n')
    calls = []
    class Session:
        def __init__(self, *args):
            pass
        def start(self):
            calls.append('start')
            return True
        def close(self):
            calls.append('close')
    monkeypatch.setattr(launcher, 'CaddySession', Session)
    monkeypatch.setattr(launcher, 'probe_llm', lambda _: 'ready')
    assert launcher.run_app(tmp_path, [sys.executable, '-c', 'raise SystemExit(7)']) == 7
    assert calls == ['start', 'close']


@pytest.mark.parametrize('port', ['not-a-port', '70000'])
def test_invalid_app_port_does_not_start_caddy(tmp_path, monkeypatch, port):
    monkeypatch.setenv('AI_EMS_WEB_PORT', port)
    monkeypatch.setattr(launcher, 'CaddySession', lambda *args: pytest.fail('unexpected startup'))
    assert launcher.run_app(tmp_path) == 2


def test_autostart_failure_does_not_launch_app_and_closes_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('AI_EMS_CADDY_AUTOSTART', 'true')
    monkeypatch.setenv('AI_EMS_CADDY_SCRIPT_WIN', r'C:\Example\launch.ps1')
    calls = []
    class FailedSession:
        def __init__(self, *args): pass
        def start(self): return False
        def close(self): calls.append('close')
    monkeypatch.setattr(launcher, 'CaddySession', FailedSession)
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *a, **k: pytest.fail('app launched'))
    assert launcher.run_app(tmp_path, ['unused']) == 3
    assert calls == ['close']
    assert 'exit 3' in capsys.readouterr().err


def test_enabled_autostart_without_path_stops_app(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_EMS_CADDY_AUTOSTART', 'true')
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *a, **k: pytest.fail('app launched'))
    assert launcher.run_app(tmp_path, ['unused']) == 2


@pytest.mark.parametrize('status,body,expected', [
    (200, {'models': []}, 'ready'), (200, {'other': []}, 'invalid_response'),
    (401, {}, 'auth_error'), (403, {}, 'auth_error'), (502, {}, 'upstream_502'),
    (500, {}, 'http_error'), (302, {}, 'http_error'),
])
def test_probe_uses_dotenv_auth_and_classifies_http_without_secret_logs(monkeypatch, capsys, status, body, expected):
    def request(url, **kwargs):
        assert url == 'https://ollama.example.com/api/tags'
        assert kwargs['headers'] == {'Authorization': 'Bearer test-secret-never-print'}
        assert kwargs['follow_redirects'] is False
        assert kwargs['timeout'] == 8
        return httpx.Response(status, json=body)
    monkeypatch.setattr(httpx, 'get', request)
    state = launcher.probe_llm({'AI_EMS_LLM_BASE_URL': 'https://ollama.example.com/',
                               'AI_EMS_LLM_API_KEY': 'test-secret-never-print'})
    assert state == expected
    launcher.report_llm(state)
    output = capsys.readouterr().err
    assert 'test-secret-never-print' not in output
    if expected != 'ready':
        assert 'AI 사용이 불가능할 수 있습니다' in output
    if status != 200:
        assert f'HTTP {status}' in output


@pytest.mark.parametrize('error,expected', [
    (httpx.ConnectError('sensitive URL/header withheld'), 'unreachable'),
    (httpx.ReadTimeout('sensitive URL/header withheld'), 'timeout'),
])
def test_probe_transport_errors_are_separate_from_http_auth_and_502(monkeypatch, error, expected, capsys):
    def request(*a, **kw): raise error
    monkeypatch.setattr(httpx, 'get', request)
    assert launcher.probe_llm({}) == expected
    assert 'sensitive' not in capsys.readouterr().err


def test_probe_environment_precedence_and_cloudflare_auth(monkeypatch):
    monkeypatch.setenv('AI_EMS_LLM_BASE_URL', 'https://configured.example.com')
    monkeypatch.setenv('AI_EMS_CF_ACCESS_CLIENT_ID', 'test-id')
    monkeypatch.setenv('AI_EMS_CF_ACCESS_CLIENT_SECRET', 'test-secret')
    def request(url, **kwargs):
        assert url == 'https://configured.example.com/api/tags'
        assert kwargs['headers'] == {'CF-Access-Client-Id': 'test-id', 'CF-Access-Client-Secret': 'test-secret'}
        return httpx.Response(200, json={'models': []})
    monkeypatch.setattr(httpx, 'get', request)
    assert launcher.probe_llm({'AI_EMS_LLM_BASE_URL': 'https://dotenv.example.com'}) == 'ready'


def test_probe_does_not_send_credentials_to_invalid_http_endpoint(monkeypatch):
    monkeypatch.setattr(httpx, 'get', lambda *a, **kw: pytest.fail('credential sent'))
    assert launcher.probe_llm({'AI_EMS_LLM_BASE_URL': 'http://remote.example.com',
                               'AI_EMS_LLM_API_KEY': 'test-secret'}) == 'invalid_auth_config'


@pytest.mark.parametrize('state', ['auth_error', 'upstream_502', 'unreachable'])
def test_unhealthy_api_keeps_app_available_without_restarting_proxy(tmp_path, monkeypatch, state, capsys):
    monkeypatch.setenv('AI_EMS_CADDY_AUTOSTART', 'true')
    monkeypatch.setenv('AI_EMS_CADDY_SCRIPT_WIN', r'C:\Example\launch.ps1')
    calls = []
    class ReusedSession:
        def __init__(self, *a): pass
        def start(self): calls.append('reuse'); return True
        def close(self): calls.append('detach')
    monkeypatch.setattr(launcher, 'CaddySession', ReusedSession)
    monkeypatch.setattr(launcher, 'probe_llm', lambda _: state)
    assert launcher.run_app(tmp_path, [sys.executable, '-c', 'pass']) == 0
    assert calls == ['reuse', 'detach']
    assert 'AI 사용이 불가능할 수 있습니다' in capsys.readouterr().err


@pytest.mark.parametrize('signum', [signal.SIGINT, signal.SIGTERM])
def test_signal_ignoring_app_reaches_cleanup_within_deadline(tmp_path, signum):
    ready = tmp_path / 'child-ready'
    cleaned = tmp_path / 'controller-closed'
    child_source = ("import signal,time,pathlib; signal.signal(signal.SIGINT,signal.SIG_IGN); "
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                    f"pathlib.Path({str(ready)!r}).touch(); time.sleep(60)")
    source = f"""
import runpy,sys,pathlib
m=runpy.run_path({str(Path(launcher.__file__))!r})
g=m['run_app'].__globals__
g['APP_GRACE_SECONDS']=0.2
g['APP_TERMINATE_SECONDS']=0.2
class Session:
    def __init__(self,*a): pass
    def start(self): return True
    def close(self): pathlib.Path({str(cleaned)!r}).touch()
g['CaddySession']=Session
g['probe_llm']=lambda _: 'ready'
sys.exit(m['run_app'](pathlib.Path({str(tmp_path)!r}), [sys.executable,'-c',{child_source!r}]))
"""
    env = {**os.environ, 'AI_EMS_CADDY_AUTOSTART': 'true', 'AI_EMS_CADDY_SCRIPT_WIN': r'C:\Example\launch.ps1'}
    process = subprocess.Popen([sys.executable, '-c', source], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert process.poll() is None, 'launcher exited before child readiness'
            assert time.monotonic() < deadline
            time.sleep(.02)
        started = time.monotonic()
        process.send_signal(signum)
        process.communicate(timeout=4)
        assert time.monotonic() - started < 3
        assert process.returncode == 130
        assert cleaned.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
