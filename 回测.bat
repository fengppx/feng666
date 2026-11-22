@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QLab 回测

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 找不到虚拟环境 .venv
    echo 请先在项目目录执行：
    echo     py -3.12 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo 正在运行回测（约 5-15 秒）...
echo.
".venv\Scripts\python.exe" scripts\run_backtest.py %*
if errorlevel 1 (
    echo.
    echo [失败] 回测出错，请把上面的红色报错整段复制给组员或 AI 助手。
    pause
    exit /b 1
)

echo.
echo [完成] 结果已生成：
echo    图表：output\figures\
echo    指标：output\tables\
echo    报告：output\report\项目技术报告.docx
echo.
pause
