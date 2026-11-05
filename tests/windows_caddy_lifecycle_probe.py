"""Opt-in real Windows/WSL probe; temporary launchers only, no installed files changed.

Run: .venv/bin/python tests/windows_caddy_lifecycle_probe.py
Tokens are generated in memory and passed through stdin/environment, never saved.
"""
import base64
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ENGINE = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
BINDING_CASES = {
    'empty': ([], 'absent'),
    'ipv4': (['127.0.0.1'], 'local'),
    'wildcard_ipv4': (['0.0.0.0'], 'occupied'),
    'wildcard_ipv6': (['::'], 'occupied'),
    'loopback_ipv6': (['::1'], 'occupied'),
    'other_loopback': (['127.0.0.2'], 'occupied'),
    'other_interface': (['192.0.2.123'], 'occupied'),
    'ipv4_then_wildcard': (['127.0.0.1', '0.0.0.0'], 'occupied'),
    'wildcard_then_ipv4': (['0.0.0.0', '127.0.0.1'], 'occupied'),
    'ipv4_and_ipv6': (['127.0.0.1', '::1'], 'occupied'),
    'ipv4_and_other_interface': (['127.0.0.1', '192.0.2.123'], 'occupied'),
    'duplicate_ipv4': (['127.0.0.1', '127.0.0.1'], 'occupied'),
}


def ps_command(source):
    source = "$ProgressPreference = 'SilentlyContinue'\n[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)\n[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)\n" + source
    encoded = base64.b64encode(source.encode('utf-16le')).decode()
    return [ENGINE, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded]


def windows(source, payload=None):
    result = subprocess.run(ps_command(source), input=json.dumps(payload)+"\n", text=True,
                            encoding='utf-8', errors='replace', capture_output=True, timeout=30)
    if result.returncode:
        raise AssertionError('Windows probe command failed (output withheld)')
    return json.loads(result.stdout.lstrip('\ufeff'))


def winpath(path):
    return subprocess.check_output(['wslpath', '-w', str(path)], text=True).strip()


def listeners(port=18080):
    return windows(r'''
$p = [Console]::In.ReadLine() | ConvertFrom-Json
$rows = @(Get-NetTCPConnection -State Listen -LocalPort ([int]$p) -ErrorAction SilentlyContinue | ForEach-Object {
    $proc = Get-Process -Id $_.OwningProcess
    [pscustomobject]@{pid=$proc.Id; start=$proc.StartTime.ToUniversalTime().ToString('o');
        address=[string]$_.LocalAddress; loopback=($_.LocalAddress -eq '127.0.0.1'); name=$proc.ProcessName}
})
ConvertTo-Json -InputObject $rows -Compress
''', port)


def fixture(directory, port=18080, upstream_port=11434):
    directory.mkdir()
    source = ROOT / 'deploy/ollama-proxy'
    # Only the temporary test module's listener changes. Bearer/allowlist stay identical.
    module = (source/'ProxyConfig.psm1').read_text().replace('127.0.0.1:18080', f'127.0.0.1:{port}')
    module = module.replace('127.0.0.1:11434', f'127.0.0.1:{upstream_port}')
    (directory/'ProxyConfig.psm1').write_text(module)
    script = directory/"custom launch '$ name.ps1"
    script.write_text((source/'Start-OllamaProxy.ps1').read_text())
    return script


def start_bridge(script, port, token, local_app_data=None, *, clear_windows_token=False):
    # The environment override exists only inside this test PowerShell process.
    bootstrap = r'''
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$setup = [Console]::In.ReadLine() | ConvertFrom-Json
if ($setup.root) { $env:LOCALAPPDATA = [string]$setup.root }
if ($setup.clear_token) { Remove-Item Env:OLLAMA_PROXY_TOKEN -ErrorAction SilentlyContinue }
& ([string]$setup.bridge) -ListenPort ([int]$setup.port)
'''
    process = subprocess.Popen(ps_command(bootstrap), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', start_new_session=True)
    process.stdin.write(json.dumps({'root':local_app_data, 'port':port, 'clear_token':clear_windows_token,
                                   'bridge':winpath(ROOT/'deploy/ollama-proxy/Manage-AgentProxy.ps1')})+'\n')
    process.stdin.write(json.dumps({'script':script, 'token':token})+'\n')
    process.stdin.flush()
    # The enclosing probe is run with a timeout by the caller.
    state = json.loads(process.stdout.readline())
    assert not token or token not in json.dumps(state), 'credential leaked in readiness output'
    return process, state


def close_bridge(process, token):
    output, error = process.communicate('stop\n', timeout=30)
    assert not token or token not in output + error, 'credential leaked in controller output'
    assert process.returncode == 0, 'controller shutdown failed'


def check_auth(port, token):
    result = windows(r'''
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
$p = [Console]::In.ReadLine() | ConvertFrom-Json
if (-not $p.token) { $p.token = $env:OLLAMA_PROXY_TOKEN }
$cases = @(@('GET','/api/tags',''), @('GET','/api/tags','invalid'),
           @('POST','/api/pull',$p.token), @('POST','/api/generate',$p.token),
           @('GET','/api/chat',$p.token), @('GET','/',$p.token), @('GET','/api/tags',$p.token))
$codes = @(foreach ($c in $cases) {
    $r = [Net.HttpWebRequest]::Create("http://127.0.0.1:$($p.port)$($c[1])")
    $r.Method = $c[0]; $r.Timeout = 10000
    if ($c[2]) { $r.Headers['Authorization'] = "Bearer $($c[2])" }
    if ($r.Method -eq 'POST') { $r.ContentLength = 0 }
    try { $resp = $r.GetResponse() } catch [Net.WebException] { $resp = $_.Exception.Response }
    if ($null -eq $resp) { throw 'No HTTP response' }
    [int]$resp.StatusCode
    $resp.Close()
})
ConvertTo-Json -InputObject $codes -Compress
''', {'port':port,'token':token})
    assert result[:6] == [401,401,403,403,403,403], result[:6]
    assert result[6] in (200,502), 'allowed tags route rejected'
    print('PASS Windows Bearer rejection + API allowlist; authenticated tags HTTP', result[6], flush=True)
    return result[6]


def free_windows_port():
    return windows('$l = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0); $l.Start(); $l.LocalEndpoint.Port; $l.Stop()')


def binding_policy_results():
    """Execute the production Listener with mocked rows; never open unsafe sockets."""
    return windows(r'''
$p = [Console]::In.ReadLine() | ConvertFrom-Json
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile([string]$p.bridge, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Manager syntax error' }
$definition = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Listener'}, $true)
. ([scriptblock]::Create($definition.Extent.Text))
function Get-NetTCPConnection {
    [CmdletBinding()] param($State, $LocalPort)
    if ($State -ne 'Listen' -or $LocalPort -ne 18080) { throw 'Wrong port query' }
    $global:TestConnections
}
$ListenPort = 18080
$results = @{}
foreach ($case in $p.cases.PSObject.Properties) {
    $global:TestConnections = @(foreach ($address in $case.Value) {
        [pscustomobject]@{LocalAddress=[string]$address; OwningProcess=42}
    })
    $results[$case.Name] = Listener
}
ConvertTo-Json -InputObject $results -Depth 5 -Compress
''', {'bridge': winpath(ROOT/'deploy/ollama-proxy/Manage-AgentProxy.ps1'),
      'cases': {name: addresses for name, (addresses, _) in BINDING_CASES.items()}})


def reject_mocked_bindings(port, caddy_pid):
    """Whole-manager reuse rejection with a real Caddy PID and fake address rows."""
    rejected = windows(r'''
$p = [Console]::In.ReadLine() | ConvertFrom-Json
function Get-NetTCPConnection {
    [CmdletBinding()] param($State, $LocalPort)
    $global:TestConnections
}
$rows = @()
foreach ($case in $p.cases.PSObject.Properties) {
    $global:TestConnections = @(foreach ($address in $case.Value) {
        [pscustomobject]@{LocalAddress=[string]$address; OwningProcess=[int]$p.pid}
    })
    $request = @{script='Z:\not-installed\unused.ps1'; token=$null} | ConvertTo-Json -Compress
    $savedInput = [Console]::In; $savedOutput = [Console]::Out
    $capture = New-Object IO.StringWriter
    try {
        [Console]::SetIn((New-Object IO.StringReader($request)))
        [Console]::SetOut($capture)
        & ([string]$p.bridge) -ListenPort ([int]$p.port)
    } finally { [Console]::SetIn($savedInput); [Console]::SetOut($savedOutput) }
    $result = $capture.ToString() | ConvertFrom-Json
    $rows += [pscustomobject]@{case=$case.Name; state=$result.state}
}
ConvertTo-Json -InputObject $rows -Compress
''', {'bridge': winpath(ROOT/'deploy/ollama-proxy/Manage-AgentProxy.ps1'), 'port': port,
      'pid': caddy_pid, 'cases': {name: addresses for name, (addresses, state) in BINDING_CASES.items()
                                 if state == 'occupied'}})
    assert len(rejected) == sum(state == 'occupied' for _, state in BINDING_CASES.values())
    assert all(row['state'] == 'occupied' for row in rejected), rejected
    print('PASS whole-manager rejects wildcard/IPv6/other-address/mixed/duplicate mocked bindings; no unsafe socket opened', flush=True)


def reject_startup_bindings(script, port):
    """Falsify observed addresses after starting a real, safe loopback-only Caddy."""
    for addresses in (['0.0.0.0'], ['::1'], ['127.0.0.1', '::']):
        result = windows(r'''
$p = [Console]::In.ReadLine() | ConvertFrom-Json
$global:ListenerCalls = 0
# Load native commands before installing the mock; module autoload must not replace it.
Import-Module NetTCPIP -ErrorAction Stop
function Get-NetTCPConnection {
    [CmdletBinding()] param($State, $LocalPort)
    $global:ListenerCalls += 1
    if ($global:ListenerCalls -eq 1) { return }
    $actual = @(NetTCPIP\Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)
    if ($actual.Count) {
        foreach ($address in $p.addresses) {
            [pscustomobject]@{LocalAddress=[string]$address; OwningProcess=$actual[0].OwningProcess}
        }
    }
}
$request = (@{script=$p.script; token=$null} | ConvertTo-Json -Compress) + "`nstop`n"
$savedInput = [Console]::In; $savedOutput = [Console]::Out
$capture = New-Object IO.StringWriter
try {
    [Console]::SetIn((New-Object IO.StringReader($request)))
    [Console]::SetOut($capture)
    & ([string]$p.bridge) -ListenPort ([int]$p.port)
} finally { [Console]::SetIn($savedInput); [Console]::SetOut($savedOutput) }
$events = @($capture.ToString().Split("`n") | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
@{states=@($events | ForEach-Object { $_.state })} | ConvertTo-Json -Compress
''', {'bridge': winpath(ROOT/'deploy/ollama-proxy/Manage-AgentProxy.ps1'),
      'script': winpath(script), 'port': port, 'addresses': addresses})
        assert result['states'] == ['occupied', 'stopped'], result
        assert not listeners(port), 'rejected owned Caddy survived cleanup'
    print('PASS post-start wildcard/IPv6/mixed snapshots rejected and owned Caddy cleaned (safe loopback sockets only)', flush=True)


def configured_llm_check():
    # The real WSL app route, dotenv settings and auth; no URL, headers or body printed.
    import runpy
    module = runpy.run_path(str(ROOT/'scripts/run_agent.py'))
    state = module['probe_llm'](module['dotenv_settings'](ROOT))
    print('OBSERVED actual WSL configured LLM /api/tags:', state, flush=True)
    return state


def isolated_app_check(directory, port, token, *, ignore_signals=False):
    """Real controller/app processes on a test proxy port; preserve all existing listeners."""
    script = fixture(directory, port)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        web_port = sock.getsockname()[1]
    env = {**os.environ, 'AI_EMS_WEB_PORT': str(web_port), 'AI_EMS_CADDY_AUTOSTART': 'true',
           'AI_EMS_CADDY_SCRIPT_WIN': winpath(script)}
    env.pop('OLLAMA_PROXY_TOKEN', None)  # Prove Windows inheritance, not a synthetic WSL token.
    command = None
    if ignore_signals:
        command = [sys.executable, '-c', "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('SIGNALS_IGNORED_READY',flush=True); time.sleep(60)"]
    source = f"""
import runpy,sys
m=runpy.run_path('scripts/run_agent.py'); g=m['run_app'].__globals__
original=g['CaddySession']
g['CaddySession']=lambda *a: original(*a,listen_port={port})
g['APP_GRACE_SECONDS']=0.3
g['APP_TERMINATE_SECONDS']=0.3
sys.exit(m['run_app'](command={command!r}))
"""
    process = subprocess.Popen([sys.executable, '-c', source], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding='utf-8', start_new_session=True)
    output = []
    reader = threading.Thread(target=lambda: output.extend(process.stdout), daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 60
        while True:
            assert process.poll() is None, 'isolated launcher exited before readiness'
            if ignore_signals:
                ready = any('SIGNALS_IGNORED_READY' in line for line in output)
            else:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{web_port}/api/health', timeout=1) as r:
                        ready = r.status == 200
                except OSError:
                    ready = False
            if ready:
                break
            assert time.monotonic() < deadline, 'isolated app readiness timeout'
            time.sleep(.1)
        active = listeners(port)
        assert len(active) == 1 and active[0]['name'] == 'caddy'
        assert check_auth(port, None) == 200, 'Ollama upstream is not ready'
        started = time.monotonic()
        process.send_signal(signal.SIGTERM if ignore_signals else signal.SIGINT)
        process.wait(timeout=30)
        assert time.monotonic() - started < 30
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=35)
        reader.join(timeout=3)
    assert token not in ''.join(output)
    assert not listeners(port), 'owned Caddy survived app shutdown'
    assert any('Windows 프로세스' in line for line in output), 'did not verify inherited credential source'
    if ignore_signals:
        assert process.returncode == 130
        print('PASS real signal-ignoring app: bounded escalation + owned Windows Caddy cleanup', flush=True)
    else:
        print('PASS no Caddy -> Windows-inherited token -> Ollama 200 -> real Uvicorn -> Ctrl+C cleanup', flush=True)


def main():
    baseline = listeners()
    results = binding_policy_results()
    for name, (_, expected) in BINDING_CASES.items():
        assert results[name]['state'] == expected, name
    print('PASS production Listener address matrix (12 mocked cases)', flush=True)
    configured_llm_check()
    print('OBSERVED token availability:', windows("@{windows_process=[bool]$env:OLLAMA_PROXY_TOKEN; valid_format=($env:OLLAMA_PROXY_TOKEN -cmatch '^[0-9a-f]{64}$')} | ConvertTo-Json -Compress"),
          'WSL process:', bool(os.environ.get('OLLAMA_PROXY_TOKEN')), flush=True)
    token = secrets.token_hex(32)
    with tempfile.TemporaryDirectory(prefix='ai-ems-caddy-') as temp:
        directory = Path(temp)
        port = free_windows_port()
        script = fixture(directory/'경로 with spaces', port)
        owner = None
        try:
            owner, state = start_bridge('%LOCALAPPDATA%\\'+script.name, port, token, winpath(script.parent))
            assert state['state'] == 'started', state
            identity = listeners(port)
            assert any(x['pid'] == state['pid'] for x in identity)
            assert len(identity) == 1 and identity[0]['address'] == '127.0.0.1'
            print('PASS Windows LOCALAPPDATA expansion + renamed Unicode/quoted script path + owned startup', flush=True)
            reject_mocked_bindings(port, state['pid'])
            assert listeners(port) == identity, 'binding rejection changed an existing Caddy'
            check_auth(port, token)
            reused, state = start_bridge('Z:\\not-installed\\anything.ps1', port, token)
            try:
                assert state['state'] == 'reused', state
            finally:
                close_bridge(reused, token)
            assert listeners(port) == identity, 'reused Caddy changed after client exit'
            print('PASS reuse leaves existing Caddy alive', flush=True)
        finally:
            if owner is not None:
                close_bridge(owner, token)
        assert not listeners(port), 'owned Caddy listener survived shutdown'
        assert listeners() == baseline, 'pre-existing default listener changed'
        print('PASS owned-instance cleanup; default-port processes unchanged', flush=True)
        reject_startup_bindings(script, port)
        assert listeners() == baseline, 'startup binding rejection changed baseline'

        failed, state = start_bridge('%LOCALAPPDATA%\\missing.ps1', port, token, winpath(script.parent))
        try:
            assert state['state'] == 'invalid_path', state
        finally:
            close_bridge(failed, token)
        assert not listeners(port)
        print('PASS missing path rejected without starting Caddy', flush=True)

        invalid_token = token + 'x'
        failed, state = start_bridge(winpath(script), port, invalid_token)
        try:
            assert state['state'] == 'token_invalid', state
        finally:
            close_bridge(failed, invalid_token)
        assert not listeners(port)
        print('PASS invalid token fails without credential/config output or orphan proxy', flush=True)

        missing, state = start_bridge(winpath(script), port, None, clear_windows_token=True)
        try:
            assert state['state'] == 'token_missing', state
        finally:
            close_bridge(missing, token)
        assert not listeners(port)
        print('PASS missing Windows and WSL credentials rejected before launch (no storage fallback)', flush=True)

        server = subprocess.Popen(ps_command(r"""
$p = [Console]::In.ReadLine() | ConvertFrom-Json
$l = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, [int]$p)
try { $l.Start(); [Console]::Out.WriteLine('ready'); [Console]::Out.Flush(); $null = [Console]::In.ReadLine() }
finally { $l.Stop() }
"""), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            server.stdin.write(str(port)+'\n'); server.stdin.flush()
            assert server.stdout.readline().strip() == 'ready'
            other = listeners(port)
            occupied, state = start_bridge(winpath(script), port, token)
            try:
                assert state['state'] == 'occupied', state
            finally:
                close_bridge(occupied, token)
            assert listeners(port) == other and server.poll() is None
            print('PASS non-Caddy port owner is never terminated', flush=True)
        finally:
            server.communicate('stop\n', timeout=15)

        # Real Caddy with a deliberately unavailable upstream; never stop real Ollama.
        dead_upstream = free_windows_port()
        bad_script = fixture(directory/'unavailable upstream', port, dead_upstream)
        owner, state = start_bridge(winpath(bad_script), port, token)
        try:
            assert state['state'] == 'started'
            active = listeners(port)
            assert check_auth(port, token) == 502
            reused, state = start_bridge(winpath(bad_script), port, token)
            try:
                assert state['state'] == 'reused'
            finally:
                close_bridge(reused, token)
            assert listeners(port) == active, 'upstream failure restarted existing Caddy'
            print('PASS real Ollama upstream 502 + auth 401 distinguished; Caddy PID/start unchanged', flush=True)
        finally:
            close_bridge(owner, token)
        assert not listeners(port)

        isolated_app_check(directory/'owned app', port, token)
        isolated_app_check(directory/'signal ignoring app', port, token, ignore_signals=True)
        assert listeners() == baseline

        # Use a free app port: never stop an app already listening on the default port.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            app_port = sock.getsockname()[1]
        script = fixture(directory/'app launcher')
        env = {**os.environ, 'AI_EMS_WEB_PORT':str(app_port), 'AI_EMS_CADDY_AUTOSTART':'true',
               'AI_EMS_CADDY_SCRIPT_WIN':winpath(script)}
        env.pop('OLLAMA_PROXY_TOKEN', None)
        app = subprocess.Popen([str(ROOT/'run_agent.sh')], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', start_new_session=True)
        output = []
        reader = threading.Thread(target=lambda: output.extend(app.stdout), daemon=True)
        reader.start()
        try:
            deadline = time.monotonic()+55
            while True:
                assert app.poll() is None, 'launcher exited before health check'
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{app_port}/api/health', timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise AssertionError('web app did not become ready')
                    time.sleep(.2)
            active = listeners()
            assert any(x['loopback'] and x['name'] == 'caddy' for x in active), 'no proxy after startup'
            configured_llm_check()
            # Another app exits with error while reusing this Caddy: it must not stop it.
            source = "import runpy,sys; m=runpy.run_path('scripts/run_agent.py'); sys.exit(m['run_app'](command=[sys.executable,'-c','raise SystemExit(7)']))"
            failed_app = subprocess.run([sys.executable,'-c',source], cwd=ROOT, env=env,
                                        capture_output=True, text=True, timeout=35)
            assert failed_app.returncode == 7
            assert token not in failed_app.stdout + failed_app.stderr
            assert listeners() == active, 'reused proxy stopped on app failure'
            print('PASS actual run_agent.sh app ready + reusing app failure keeps proxy', flush=True)
        finally:
            if app.poll() is None:
                app.send_signal(signal.SIGINT)
                app.wait(timeout=40)
            reader.join(timeout=5)
        assert token not in ''.join(output), 'credential leaked in app logs'
        assert listeners() == baseline, 'Ctrl+C changed an existing listener or left an owned one'
        print('PASS actual run_agent.sh Ctrl+C cleanup; previous listeners preserved', flush=True)
        own_failure = subprocess.run([sys.executable,'-c',source], cwd=ROOT, env=env,
                                     capture_output=True, text=True, timeout=35)
        assert own_failure.returncode == 7
        assert token not in own_failure.stdout + own_failure.stderr
        assert listeners() == baseline, 'owned proxy survived app failure'
        print('PASS app failure preserves baseline (or cleans owned Caddy when default port was free)', flush=True)


if __name__ == '__main__':
    main()
