@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   CNKI Paper API - START
echo ========================================
python --version >nul 2>&1
if errorlevel 1 (
  echo   [ERROR] 'python' not found. Did you run install.bat first?
  echo   Python must be 3.10+ and added to PATH.
  pause
  exit /b 1
)
python scripts\.serverctl.py start
echo.
echo Opening API docs in browser...
start "" "http://127.0.0.1:8765/docs"
echo.
echo If this is your FIRST run, call POST /session/refresh in the docs
echo and complete CNKI login in the browser window before using /search.
pause
