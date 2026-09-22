$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& '.\.venv\Scripts\python.exe' scripts/make_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Application icon generation failed.' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm EliosColorizer.spec
if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
& '.\.venv\Scripts\python.exe' scripts/package_metadata.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging documentation failed.' }
Write-Host 'Portable application: dist/EliosColorizer/EliosColorizer.exe'
