# Start signalrgb.py on the interactive console session (not Session 0 / SSH).
# RTSS shared memory is session-local — Session 0 cannot see hooked game FPS.
$ErrorActionPreference = "Stop"
$root = "C:\Users\louis\Projects\squid"
$py = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "signalrgb.py"
$task = "SquidLCDBridge"

Get-Process python, pythonw -EA SilentlyContinue | Where-Object {
  $_.Path -like "*squid*" -or $_.Path -like "*Projects\squid*"
} | Stop-Process -Force -EA SilentlyContinue

# Also stop any python hosting signalrgb from this venv
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -EA SilentlyContinue |
  Where-Object { $_.CommandLine -match 'signalrgb' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }

Start-Sleep -Milliseconds 500

$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory $root
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task
Start-Sleep 2

Get-Process python, pythonw -EA SilentlyContinue |
  Select-Object Name, Id, SessionId, Path |
  Format-Table -Auto
Write-Host "Bridge started via scheduled task '$task' (should be SessionId=1)."
