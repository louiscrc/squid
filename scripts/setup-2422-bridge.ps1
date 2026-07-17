# Setup SignalRGB 2.4.22 + KrakenLCDBridge (no native NZXT LCD fight)
$ErrorActionPreference = "Continue"
$vortx2422 = Join-Path $env:LOCALAPPDATA "VortxEngine\app-2.4.22\Signal-x64"
$docsPlugins = Join-Path $env:USERPROFILE "Documents\WhirlwindFX\Plugins"
$repo = "C:\Users\louis\Projects\squid"

Write-Host "=== hosts pin ==="
$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$hostsText = Get-Content $hostsPath -Raw -EA SilentlyContinue
if ($hostsText -notmatch "release\.signalrgb\.com") {
  try {
    Add-Content -Path $hostsPath -Value "`r`n# squid: pin SignalRGB 2.4.22`r`n127.0.0.1 release.signalrgb.com`r`n" -Encoding ascii -EA Stop
    Write-Host "hosts blocked"
  } catch { Write-Host "hosts write failed: $_" }
} else { Write-Host "hosts already pinned" }

Write-Host "=== stop everything ==="
Get-Process python*, SignalRgb*, SignalRgbLauncher, SignalRgbSplash, SignalRGBLCDBridge*, Update -EA SilentlyContinue |
  Stop-Process -Force -EA SilentlyContinue
Start-Sleep 2

Write-Host "=== disable native NZXT on 2.4.22 ==="
if (Test-Path "$vortx2422\Plugins\Nzxt") {
  if (Test-Path "$vortx2422\Plugins\Nzxt.disabled") {
    Remove-Item "$vortx2422\Plugins\Nzxt.disabled" -Recurse -Force
  }
  Rename-Item "$vortx2422\Plugins\Nzxt" "Nzxt.disabled"
  Write-Host "Plugins\Nzxt -> Nzxt.disabled"
} elseif (Test-Path "$vortx2422\Plugins\Nzxt.disabled") {
  Write-Host "Nzxt already disabled"
} else {
  Write-Host "WARNING: no Nzxt plugin folder on 2.4.22"
}

if (Test-Path "$vortx2422\Components\NZXT") {
  if (Test-Path "$vortx2422\Components\NZXT.disabled") {
    Remove-Item "$vortx2422\Components\NZXT.disabled" -Recurse -Force
  }
  Rename-Item "$vortx2422\Components\NZXT" "NZXT.disabled"
  Write-Host "Components\NZXT -> NZXT.disabled"
}

Write-Host "=== restore KrakenLCDBridge ==="
$disabled = Join-Path $docsPlugins "KrakenLCDBridge.disabled"
$active = Join-Path $docsPlugins "KrakenLCDBridge"
$src = Join-Path $repo "SignalRGBPlugin"
if (Test-Path $disabled) {
  if (Test-Path $active) { Remove-Item $active -Recurse -Force }
  Rename-Item $disabled "KrakenLCDBridge"
  Write-Host "renamed .disabled -> KrakenLCDBridge"
}
New-Item -ItemType Directory -Force -Path $active | Out-Null
Copy-Item -Force (Join-Path $src "*") $active
Write-Host "plugin copied from repo"
Get-ChildItem $active | Select-Object Name

Write-Host "=== start bridge ==="
$out = "$env:TEMP\squid-bridge-out.log"
$err = "$env:TEMP\squid-bridge-err.log"
Remove-Item $out, $err -EA SilentlyContinue
Start-Process -FilePath "$repo\.venv\Scripts\python.exe" -ArgumentList "-u","signalrgb.py","--debug" `
  -WorkingDirectory $repo -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden
Start-Sleep 7
if (-not (Get-Process python* -EA SilentlyContinue)) {
  Write-Host "BRIDGE FAILED"
  Get-Content $err -Tail 40
  exit 1
}
try {
  $info = Invoke-RestMethod http://127.0.0.1:30003/ -TimeoutSec 5
  Write-Host ("bridge: " + ($info | ConvertTo-Json -Compress))
} catch {
  Write-Host "bridge HTTP failed: $_"
  Get-Content $err -Tail 20
  exit 1
}

Write-Host "=== start SignalRGB 2.4.22 ==="
$exe = Join-Path $vortx2422 "SignalRgb.exe"
if (-not (Test-Path $exe)) { throw "Missing $exe" }
# Kill updater again just in case
Get-Process Update -EA SilentlyContinue | Where-Object { $_.Path -like "*VortxEngine*" } | Stop-Process -Force -EA SilentlyContinue
Start-Process $exe
Start-Sleep 14
Get-Process SignalRgb -EA SilentlyContinue | Select-Object Name, Id, Path | Format-List

Write-Host "=== sample logs ==="
Start-Sleep 8
Get-Content $out -Tail 15
$ok = (Select-String -Path $out -Pattern "FPS:" -EA SilentlyContinue | Measure-Object).Count
# stock may not print rejects; use short writes heuristic later
Write-Host "fps_lines=$ok"
Write-Host "DONE - check LCD + SignalRGB for Kraken LCD Bridge device"
