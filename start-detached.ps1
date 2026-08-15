# Start Switchboard outside the calling job/console (WMI).
# Safe to run from an agent chat: the server keeps running after the chat ends.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  $py = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
}
if (-not $py -or -not (Test-Path $py)) { throw "python not found" }

$listen = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
if ($listen) {
  $pids = $listen.OwningProcess | Select-Object -Unique
  Write-Host "already listening on 8787 (PID $($pids -join ', '))"
  exit 0
}

$cmd = '"{0}" -u "{1}"' -f $py, (Join-Path $here "server.py")
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine      = $cmd
  CurrentDirectory = $here
}
if ($created.ReturnValue -ne 0 -or -not $created.ProcessId) {
  throw "WMI Create failed: $($created.ReturnValue)"
}
Write-Host "started detached PID $($created.ProcessId) -> http://127.0.0.1:8787"
Write-Host "log: $(Join-Path $here 'data\server.log')"
