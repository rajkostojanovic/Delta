@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    py -3.14 -m venv "%~dp0.venv"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    pause
    exit /b 1
)
echo Delta is ready. Run launch.bat.
pause
