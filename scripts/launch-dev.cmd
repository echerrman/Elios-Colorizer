@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run scripts\setup.ps1 with 64-bit Python 3.12 before using the development launcher.
  pause
  exit /b 1
)
set "PYTHONPATH=%CD%\src"
start "" ".venv\Scripts\pythonw.exe" -m elios_colorizer
