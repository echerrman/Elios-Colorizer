@echo off
setlocal
cd /d "%~dp0"
if exist "dist\EliosColorizer\EliosColorizer.exe" (
  start "" "dist\EliosColorizer\EliosColorizer.exe"
  exit /b
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo First run scripts\setup.ps1 with Python 3.12 or newer installed.
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src"
start "" ".venv\Scripts\pythonw.exe" -m elios_colorizer

