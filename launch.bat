@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run setup.bat first to install Delta's dependencies.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0app.py"
if errorlevel 1 pause
