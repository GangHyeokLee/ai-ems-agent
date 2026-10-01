function New-OllamaProxyConfig {
    # Only token data varies; listener, upstream and allowlist are fixed.
    $token = [Environment]::GetEnvironmentVariable('OLLAMA_PROXY_TOKEN', 'Process')
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw 'OLLAMA_PROXY_TOKEN is required; proxy was not started.'
    }
    if ($token -cnotmatch '^[0-9a-f]{64}$') {
        throw 'OLLAMA_PROXY_TOKEN must be 64 lowercase hex characters.'
    }
    $authFailure = @{
        match = @(@{not = @(@{header = @{Authorization = @("Bearer $token")}})})
        handle = @(@{handler = 'static_response'; status_code = 401; body = 'Unauthorized'})
    }
    $proxy = @{
        handler = 'reverse_proxy'
        upstreams = @(@{dial = '127.0.0.1:11434'})
        headers = @{request = @{delete = @('Authorization')}}
        # A small positive interval preserves cancellation on client disconnect.
        flush_interval = 10000000
    }
    $routes = @(
        $authFailure,
        @{match = @(@{method = @('GET'); path = @('/api/tags')}); handle = @($proxy)},
        @{match = @(@{method = @('POST'); path = @('/api/chat')}); handle = @($proxy)},
        @{handle = @(@{handler = 'static_response'; status_code = 403; body = 'Forbidden'})}
    )
    @{
        admin = @{disabled = $true; config = @{persist = $false}}
        logging = @{logs = @{default = @{writer = @{output = 'discard'}}}}
        apps = @{http = @{servers = @{ollama = @{
            listen = @('127.0.0.1:18080')
            automatic_https = @{disable = $true}
            routes = @(@{handle = @(@{handler = 'subroute'; routes = $routes})})
        }}}}
    } | ConvertTo-Json -Depth 20 -Compress
}
Export-ModuleMember -Function New-OllamaProxyConfig
