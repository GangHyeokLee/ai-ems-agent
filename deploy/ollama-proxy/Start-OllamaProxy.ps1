param([string]$CaddyPath = 'caddy')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'ProxyConfig.psm1') -Force

# Validate before launching Caddy. Never print or save the generated JSON.
$configJson = New-OllamaProxyConfig
$configJson | & $CaddyPath run --config -
if ($LASTEXITCODE -ne 0) {
    throw 'Caddy exited unsuccessfully.'
}
