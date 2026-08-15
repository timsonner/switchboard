# Start Switchboard on http://127.0.0.1:8787
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")
$py = $null
foreach ($c in @(
  (Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"),
  (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
)) {
  if ($c -and (Test-Path $c)) { $py = $c; break }
}
if (-not $py) { throw "python not found" }
Write-Host "Switchboard → http://127.0.0.1:8787  ($py)"
& $py (Join-Path $here "server.py")
