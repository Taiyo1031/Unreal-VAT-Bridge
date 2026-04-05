@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
set "ADDON_DIR=%ROOT_DIR%\unreal_vat_bridge"
set "DIST_DIR=%SCRIPT_DIR%"
set "ZIP_PATH=%DIST_DIR%unreal_vat_bridge.zip"
set "TEMP_ZIP=%DIST_DIR%unreal_vat_bridge.rebuilt.zip"

if not exist "%ADDON_DIR%\__init__.py" (
  echo [ERROR] Add-on entry point not found: "%ADDON_DIR%\__init__.py"
  exit /b 1
)

where powershell >nul 2>&1
if errorlevel 1 (
  echo [ERROR] PowerShell was not found on this machine.
  exit /b 1
)

echo [INFO] Rebuilding "%ZIP_PATH%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "Add-Type -AssemblyName System.IO.Compression;" ^
  "Add-Type -AssemblyName System.IO.Compression.FileSystem;" ^
  "$addonDir = $env:ADDON_DIR.TrimEnd('\');" ^
  "$distDir = $env:DIST_DIR.TrimEnd('\');" ^
  "$zipPath = $env:ZIP_PATH;" ^
  "$tempZip = $env:TEMP_ZIP;" ^
  "if (Test-Path -LiteralPath $tempZip) { Remove-Item -LiteralPath $tempZip -Force };" ^
  "$files = Get-ChildItem -LiteralPath $addonDir -Recurse -File | Where-Object {" ^
  "  $_.FullName -notmatch '\\\\__pycache__\\\\' -and" ^
  "  $_.FullName -notmatch '\\\\__MACOSX\\\\' -and" ^
  "  $_.Name -ne '.DS_Store' -and" ^
  "  $_.Name -ne 'workflow.py.bak' -and" ^
  "  $_.Extension -ne '.pyc' -and" ^
  "  $_.Extension -ne '.blend1'" ^
  "};" ^
  "$zip = [System.IO.Compression.ZipFile]::Open($tempZip, [System.IO.Compression.ZipArchiveMode]::Create);" ^
  "try {" ^
  "  foreach ($f in $files) {" ^
  "    $rel = $f.FullName.Substring($addonDir.Length).TrimStart('\');" ^
  "    $entryName = ('unreal_vat_bridge/' + ($rel -replace '\\\\','/'));" ^
  "    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(" ^
  "      $zip, $f.FullName, $entryName, [System.IO.Compression.CompressionLevel]::Optimal" ^
  "    ) | Out-Null;" ^
  "  }" ^
  "} finally {" ^
  "  $zip.Dispose();" ^
  "};" ^
  "Move-Item -LiteralPath $tempZip -Destination $zipPath -Force;" ^
  "$check = [System.IO.Compression.ZipFile]::OpenRead($zipPath);" ^
  "try {" ^
  "  $names = @($check.Entries | ForEach-Object { $_.FullName });" ^
  "  if (-not ($names -contains 'unreal_vat_bridge/__init__.py')) {" ^
  "    throw 'ZIP validation failed: unreal_vat_bridge/__init__.py is missing.'" ^
  "  }" ^
  "  $top = @($names | ForEach-Object { ($_ -split '/')[0] } | Select-Object -Unique);" ^
  "  if ($top.Count -ne 1 -or $top[0] -ne 'unreal_vat_bridge') {" ^
  "    throw ('ZIP validation failed: unexpected top-level entries: ' + ($top -join ', '))" ^
  "  }" ^
  "  Write-Host ('[OK] ZIP rebuilt: ' + $zipPath);" ^
  "  Write-Host ('[OK] File count: ' + $names.Count);" ^
  "} finally {" ^
  "  $check.Dispose();" ^
  "}"

if errorlevel 1 (
  echo [ERROR] Failed to rebuild ZIP.
  exit /b 1
)

echo [DONE] Completed.
exit /b 0
