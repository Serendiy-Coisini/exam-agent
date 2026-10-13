$ErrorActionPreference = 'Stop'
$stateFile = Join-Path $PSScriptRoot '.runtime\server.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Output 'Server is not running.'; exit 0 }
$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$server = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
if ($server -and $server.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.started) {
    Stop-Process -Id $server.Id
    $server.WaitForExit(10000) | Out-Null
    Write-Output 'Server stopped.'
} else { Write-Output 'The saved server has already stopped.' }
Remove-Item -LiteralPath $stateFile
