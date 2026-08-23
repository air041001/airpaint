@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "AIRPAINT_PYTHON=python"
if exist "E:\python 3.10\python.exe" set "AIRPAINT_PYTHON=E:\python 3.10\python.exe"
"%AIRPAINT_PYTHON%" ".tools\register_lora.py" --agent
echo.
pause
endlocal
