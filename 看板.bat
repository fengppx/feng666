@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QLab Web 看板

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 找不到虚拟环境 .venv，请先按 新手使用说明.md 准备环境。
    pause
    exit /b 1
)

echo 正在启动 Web 看板，稍后浏览器会自动打开 http://127.0.0.1:8000
echo 关闭本窗口即停止服务。
echo.
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m qlab web --port 8000
pause
