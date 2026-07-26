@echo off
REM 单独启动 cloudflared 命名隧道 - 不碰后端
REM 用途: 后端还在跑但隧道窗口没了 / 网站报 1033 时, 双击此文件补起隧道
REM 前提: 后端已起 127.0.0.1:8000. 若没起, 先跑 start_airpaint.bat
title airpaint 隧道

echo ============================================
echo   airpaint 隧道 - 单独
echo   本地后端: http://127.0.0.1:8000
echo   公网:     https://airpaint.xyz
echo   Ctrl+C 停隧道, 不影响后端
echo ============================================
echo.

REM 检查后端是否在跑
curl -s -o nul http://127.0.0.1:8000/api/health >nul 2>&1
if errorlevel 1 echo [警告] 没检测到后端 8000, 隧道能起但网站会 502. 若后端没开请改跑 start_airpaint.bat.

echo.
echo [隧道] 启动 cloudflared ...
"E:\cloudflared\cloudflared.exe" tunnel run airpaint

echo.
echo 隧道已停.
pause
