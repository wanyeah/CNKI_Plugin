@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\.serverctl.py status
echo.
pause
