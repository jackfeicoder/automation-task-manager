@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\setup.ps1"
    if errorlevel 1 exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1"
pause
