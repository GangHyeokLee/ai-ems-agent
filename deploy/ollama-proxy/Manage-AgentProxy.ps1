# Controls only the Caddy child launched for this app session. No admin/config API.
param([ValidateRange(1, 65535)][int]$ListenPort = 18080)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Console]::InputEncoding.CodePage -ne 65001) { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false) }
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$runner = $null
$owned = @()
$mutex = $null
$locked = $false
$announced = $false
$cleanupFailed = $false
$tokenSource = 'not_needed'

function Send-State([string]$State, [int]$ProcessId = 0) {
    [Console]::Out.WriteLine((@{state = $State; pid = $ProcessId; token_source = $tokenSource} | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}
function Listener {
    # Validate the entire port snapshot, including unexpected interface addresses.
    $connections = @(Get-NetTCPConnection -State Listen -LocalPort $ListenPort -ErrorAction SilentlyContinue)
    if ($connections.Count -eq 0) { return @{state = 'absent'; pid = 0} }
    if ($connections.Count -ne 1 -or $connections[0].LocalAddress -cne '127.0.0.1') {
        return @{state = 'occupied'; pid = 0}
    }
    return @{state = 'local'; pid = [int]$connections[0].OwningProcess}
}
function Owned-Caddy {
    if ($null -eq $runner) { return }
    foreach ($candidate in @(Get-CimInstance Win32_Process -Filter "Name='caddy.exe'")) {
        $node = $candidate
        for ($depth = 0; $depth -lt 16 -and $null -ne $node; $depth++) {
            if ($node.CreationDate.ToUniversalTime() -lt $runner.StartTime.ToUniversalTime()) { break }
            if ($node.ParentProcessId -eq $runner.Id) {
                if ($runner.HasExited -and $node.CreationDate.ToUniversalTime() -gt $runner.ExitTime.ToUniversalTime()) { break }
                try {
                    $process = [Diagnostics.Process]::GetProcessById([int]$candidate.ProcessId)
                    # Bind a process handle now: cleanup cannot target a recycled PID.
                    $null = $process.Handle
                    # Also reject recycling between the CIM snapshot and handle binding.
                    $observedStart = $candidate.CreationDate.ToUniversalTime().ToString('yyyyMMddHHmmssffffff')
                    $liveStart = $process.StartTime.ToUniversalTime().ToString('yyyyMMddHHmmssffffff')
                    if ($process.ProcessName -ieq 'caddy' -and $liveStart -eq $observedStart -and
                        $process.StartTime.ToUniversalTime() -ge $runner.StartTime.ToUniversalTime()) { $process }
                } catch { }
                break
            }
            $node = Get-CimInstance Win32_Process -Filter "ProcessId=$($node.ParentProcessId)" -ErrorAction SilentlyContinue
        }
    }
}
try {
    $request = [Console]::In.ReadLine() | ConvertFrom-Json
    $mutex = New-Object Threading.Mutex($false, "Local\AIEMS_Caddy_$ListenPort")
    try { $locked = $mutex.WaitOne(20000) }
    catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { Send-State 'busy'; $announced = $true; return }
    $listener = Listener
    if ($listener.state -ne 'absent') {
        if ($listener.state -eq 'local' -and
            ([Diagnostics.Process]::GetProcessById($listener.pid)).ProcessName -ieq 'caddy') {
            Send-State 'reused' $listener.pid
        }
        else { Send-State 'occupied' }
        $announced = $true
        return
    }
    # Expansion is data-only on Windows; neither shell interpolation nor Invoke-Expression.
    $script = [Environment]::ExpandEnvironmentVariables([string]$request.script)
    if ($script -match '[\r\n\x00]' -or $script -match '%[^%]+%' -or
        ($script -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\)') -or [IO.Path]::GetExtension($script) -ine '.ps1' -or
        -not (Test-Path -LiteralPath $script -PathType Leaf)) {
        Send-State 'invalid_path'; $announced = $true; return
    }
    # Reuse needs no server credential. A new server inherits the Windows process
    # credential unless WSL explicitly supplies one over stdin. No registry/file storage.
    $token = [string]$request.token
    $tokenSource = 'wsl_process'
    if ([string]::IsNullOrEmpty($token)) {
        $token = $env:OLLAMA_PROXY_TOKEN
        $tokenSource = 'windows_process'
    }
    if ([string]::IsNullOrEmpty($token)) { Send-State 'token_missing'; $announced = $true; return }
    if ($token.Length -ne 64 -or $token -cnotmatch '^[0-9a-f]{64}$') {
        Send-State 'token_invalid'; $announced = $true; return
    }
    $bootstrap = @'
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $inputData = [Console]::In.ReadLine() | ConvertFrom-Json
    if ($null -ne $inputData.token) { $env:OLLAMA_PROXY_TOKEN = [string]$inputData.token }
    & ([string]$inputData.script)
    exit $LASTEXITCODE
} catch { exit 1 }
'@
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = (Get-Process -Id $PID).Path
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
    $info.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded"
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $runner = New-Object Diagnostics.Process
    $runner.StartInfo = $info
    $null = $runner.Start()
    $runner.BeginOutputReadLine()
    $runner.BeginErrorReadLine()
    $wire = (@{script = $script; token = $token} | ConvertTo-Json -Compress) + "`n"
    $bytes = [Text.Encoding]::UTF8.GetBytes($wire)
    $runner.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $runner.StandardInput.BaseStream.Flush()
    $runner.StandardInput.Close()
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        $owned = @(Owned-Caddy)
        $listener = Listener
        if ($listener.state -eq 'occupied') { Send-State 'occupied'; $announced = $true; break }
        if ($listener.state -eq 'local' -and (@($owned | ForEach-Object { $_.Id }) -contains $listener.pid)) {
            Send-State 'started' $listener.pid
            $announced = $true
            $mutex.ReleaseMutex(); $locked = $false
            $null = [Console]::In.ReadLine() # 'stop' or EOF; both end only this session.
            break
        }
        if ($runner.HasExited -and $owned.Count -eq 0) { Send-State 'launch_failed'; $announced = $true; break }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $announced) {
        if ($listener.state -eq 'local') { Send-State 'occupied' } else { Send-State 'timeout' }
        $announced = $true
    }
} catch {
    # Do not emit exception messages, user script output, tokens, or runtime JSON.
    if (-not $announced) {
        [Console]::Out.WriteLine((@{state='failed'; pid=0; error_type=$_.Exception.GetType().Name;
            line=$_.InvocationInfo.ScriptLineNumber} | ConvertTo-Json -Compress))
        [Console]::Out.Flush()
    }
} finally {
    if ($null -ne $runner) {
        try { $owned = @($owned) + @(Owned-Caddy) } catch { $cleanupFailed = $true }
        foreach ($process in $owned) {
            try { if (-not $process.HasExited) { $process.Kill(); if (-not $process.WaitForExit(5000)) { $cleanupFailed = $true } } }
            catch { $cleanupFailed = $true }
        }
        try {
            if (-not $runner.WaitForExit(5000)) {
                $runner.Kill()
                if (-not $runner.WaitForExit(5000)) { $cleanupFailed = $true }
            }
        } catch { $cleanupFailed = $true }
        if ($cleanupFailed) { Send-State 'cleanup_failed' } else { Send-State 'stopped' }
    }
    if ($locked) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
if ($cleanupFailed) { exit 1 }
