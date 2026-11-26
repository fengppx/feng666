@echo off
cd /d "%~dp0"
title QLab Backtest

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found.
    echo Run: py -3.12 -m venv .venv
    echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Running backtest, please wait (about 10 seconds)...
echo.
".venv\Scripts\python.exe" scripts\run_backtest.py %*
if errorlevel 1 (
    echo.
    echo [FAILED] See the error above.
    pause
    exit /b 1
)

echo.
echo [DONE] Outputs:
echo     figures : output\figures\
echo     tables  : output\tables\
echo     report  : output\report\
echo.
pause