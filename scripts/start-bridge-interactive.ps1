# Start SignalRGBLCDBridge on the interactive desktop session (not Session 0 / SSH).
# RTSS / MAHM framerate shared memory is session-local.
$ErrorActionPreference = "Stop"
$installDir = Join-Path $env:LOCALAPPDATA "SignalRGBLCDBridge"
$exe = Join-Path $installDir "SignalRGBLCDBridge.exe"
$task = "SquidLCDBridge"

Get-Process SignalRGBLCDBridge, signalrgb -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Sleep -Milliseconds 500

if (-not (Test-Path $exe)) {
  throw "Missing $exe - copy SignalRGBLCDBridge.exe into $installDir first."
}

$action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $installDir
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Principal $principal `
  -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task
Start-Sleep 2

Get-CimInstance Win32_Process | Where-Object { $_.Name -match "SignalRGBLCDBridge" } |
  Select-Object Name, ProcessId, SessionId, ExecutablePath | Format-Table -Auto
Write-Host "Bridge started via '$task' (SessionId should be 1)."
