@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_airpaint.ps1"
if errorlevel 1 pause
endlocal
