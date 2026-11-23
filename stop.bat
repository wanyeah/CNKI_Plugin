@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   CNKI Paper API - STOP
echo ========================================
python scripts\.serverctl.py stop
echo.
pause
