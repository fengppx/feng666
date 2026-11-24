@echo off
cd /d "%~dp0"
title QLab Web Dashboard

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. See README.md
    pause
    exit /b 1
)

echo Starting dashboard at http://127.0.0.1:8000
echo Close this window to stop.
echo.
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m qlab web --port 8000
pause