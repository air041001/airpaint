@echo off
REM 一键启动 airpaint.xyz 服务 (后端 + 命名隧道)
REM 用法: 双击或命令行运行 start_airpaint.bat
REM 依赖: ComfyUI 已在 127.0.0.1:8188 跑着 (你用 run_nvidia_gpu_fast_fp16_accumulation.bat 启动)
chcp 65001 >nul
title airpaint.xyz 服务

echo ============================================
echo   airpaint.xyz 启动
echo   后端:   http://127.0.0.1:8000
echo   公网:   https://airpaint.xyz        (网页)
echo           https://api.airpaint.xyz     (API)
echo   Ctrl+C 退出会同时关闭后端和隧道
echo ============================================
echo.

REM 检查 ComfyUI 是否在跑
curl -s -o nul http://127.0.0.1:8188/system_stats >nul 2>&1
if errorlevel 1 (
    echo [警告] 没检测到 ComfyUI (127.0.0.1:8188). 请先启动 ComfyUI, 否则生图会失败.
    echo.
)

REM 启动后端 (新窗口)
start "airpaint 后端" cmd /k "cd /d %~dp0\..\server && python main.py"
timeout /t 3 /nobreak >nul

REM 启动 cloudflared 命名隧道 (当前窗口, 前台运行便于看日志)
echo [隧道] 启动 cloudflared ...
"E:\cloudflared\cloudflared.exe" tunnel run airpaint

echo.
echo 服务已停止. (后端窗口需手动关闭)
pause
