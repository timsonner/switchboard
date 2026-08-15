# PRODUCTION ONLY — do not enable on a lab/dev box.
# Registers a current-user logon task so Switchboard starts after reboot.
# Bind stays 127.0.0.1 today; still do not auto-start until auth / bind / TLS
# are decided. To remove: Unregister-ScheduledTask -TaskName Switchboard -Confirm:$false
#
# Usage (later):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-startup.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$starter = Join-Path $here "start-detached.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$starter`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Switchboard" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "logon task 'Switchboard' registered for $env:USERNAME"
Write-Host "it runs: $starter"
