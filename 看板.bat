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
rem 服务启动时要先加载本地行情面板，直接开浏览器会撞上“无法访问”；
rem 这里用隐藏的 PowerShell 等 4 秒再打开页面。
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:8000'"
".venv\Scripts\python.exe" -m qlab web --port 8000
pause