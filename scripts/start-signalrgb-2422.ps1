# Pin/reinstall SignalRGB 2.4.22 without letting Squirrel jump to 2.5.x
$ErrorActionPreference = "Continue"
$setup = "C:\Users\louis\Projects\squid\signalrgb-old\SignalRGB_v2.4.22.exe"
$vortx = Join-Path $env:LOCALAPPDATA "VortxEngine"
$app2422 = Join-Path $vortx "app-2.4.22"

Write-Host "=== stop SignalRGB / Update ==="
Get-Process SignalRgb*, SignalRgbLauncher, SignalRgbSplash, Update, SignalRGB_v2* -EA SilentlyContinue |
  Stop-Process -Force -EA SilentlyContinue
Start-Sleep 2

Write-Host "=== block release.signalrgb.com in hosts (needs admin) ==="
$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$marker = "squid: pin SignalRGB 2.4.22"
$hostsText = Get-Content $hostsPath -Raw -EA SilentlyContinue
if ($hostsText -notmatch [regex]::Escape($marker)) {
  $block = "`r`n# $marker`r`n127.0.0.1 release.signalrgb.com`r`n"
  try {
    Add-Content -Path $hostsPath -Value $block -Encoding ascii -EA Stop
    Write-Host "hosts OK"
  } catch {
    Write-Host "hosts FAILED (run elevated): $($_.Exception.Message)"
  }
} else {
  Write-Host "hosts already pinned"
}

Write-Host "=== clear empty/broken app-2.4.22 ==="
if (Test-Path $app2422) {
  $files = @(Get-ChildItem $app2422 -Recurse -File -EA SilentlyContinue)
  Write-Host "existing file count: $($files.Count)"
  if ($files.Count -lt 5) {
    Remove-Item -Recurse -Force $app2422 -EA SilentlyContinue
  }
}

Write-Host "=== start installer on interactive desktop ==="
if (-not (Test-Path $setup)) { throw "Missing $setup" }
# explorer ensures UI shows on the user session
Start-Process -FilePath "explorer.exe" -ArgumentList $setup
Write-Host "Installer launched. Waiting for SignalRgb.exe under app-2.4.22 ..."

$found = $null
for ($i = 0; $i -lt 60; $i++) {
  Get-Process Update -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
  $found = Get-ChildItem $app2422 -Recurse -Filter "SignalRgb.exe" -EA SilentlyContinue | Select-Object -First 1
  if ($found) { break }
  if ($i % 5 -eq 0) {
    $n = @(Get-ChildItem $app2422 -Recurse -File -EA SilentlyContinue).Count
    $procs = @(Get-Process SignalRGB_v2*, SignalRgb*, Update -EA SilentlyContinue | ForEach-Object Name) -join ","
    Write-Host "[$i] files=$n procs=$procs"
  }
  Start-Sleep 2
}

if (-not $found) {
  Write-Host "FAILED: 2.4.22 did not extract. Log:"
  Get-Content (Join-Path $vortx "SquirrelSetup.log") -Tail 30 -EA SilentlyContinue
  exit 1
}

Write-Host "FOUND $($found.FullName)"
Get-Process Update -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Process $found.FullName
Start-Sleep 5
Get-Process SignalRgb -EA SilentlyContinue | Select-Object Name, Id, Path | Format-List
Write-Host "Done. Disable updates in Settings if the UI offers it."
