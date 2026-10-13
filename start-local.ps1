param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$runtime = Join-Path $PSScriptRoot '.runtime'
$stateFile = Join-Path $runtime 'server.json'
$url = 'http://127.0.0.1:18100/'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.started) {
        try {
            $health = Invoke-RestMethod ($url + 'api/v1/health') -TimeoutSec 3
            if ($health.status -eq 'ok') {
                if (-not $NoBrowser) { Start-Process $url }
                Write-Output "Already running: $url"
                exit 0
            }
        } catch { throw 'The saved server is running but not responding. Run stop-local.cmd and try again.' }
    }
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Python environment is missing.' }
& $python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',18100)); s.close()"
if ($LASTEXITCODE -ne 0) { throw 'Port 18100 is occupied. Stop the conflicting application first.' }
$server = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18100' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'server.out.log') -RedirectStandardError (Join-Path $runtime 'server.err.log') -PassThru
@{pid=$server.Id; started=$server.StartTime.ToUniversalTime().Ticks.ToString(); url=$url} | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
for ($i=0; $i -lt 40; $i++) {
    if ($server.HasExited) { throw ('Server exited. See ' + (Join-Path $runtime 'server.err.log')) }
    try {
        $health = Invoke-RestMethod ($url + 'api/v1/health') -TimeoutSec 2
        if ($health.status -eq 'ok') {
            if (-not $NoBrowser) { Start-Process $url }
            Write-Output "Running: $url"
            exit 0
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}
throw ('Startup timed out. See ' + (Join-Path $runtime 'server.err.log'))
