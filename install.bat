@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   CNKI Paper API - INSTALL
echo ========================================

echo [0/3] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo   [ERROR] 'python' command not found.
  echo   Install Python 3.10+ from https://www.python.org/downloads/
  echo   IMPORTANT: check "Add Python to PATH" during installation.
  pause
  exit /b 1
)
python scripts\preflight.py pre
if errorlevel 1 (
  echo   [ERROR] Python version too old. Need 3.10+.
  pause
  exit /b 1
)

echo.
echo [1/3] Installing Python dependencies...
python -m pip install -r cnki_paper_api\requirements.txt
if errorlevel 1 (
  echo   [WARN] pip install failed. If download is slow, try a mirror:
  echo   python -m pip install -r cnki_paper_api\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  pause
  exit /b 1
)

echo.
echo [2/3] Installing Playwright Chromium browser (for manual captcha login)...
python -m playwright install chromium
if errorlevel 1 (
  echo   [WARN] Browser download failed. In China, set mirror then retry:
  echo   set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
  echo   python -m playwright install chromium
)

echo.
echo [3/3] Verifying installation...
python scripts\preflight.py post

echo.
echo ========================================
echo   NEXT STEPS
echo   1. double-click start.bat
echo   2. open http://127.0.0.1:8765/docs
echo   3. call POST /session/refresh and log in CNKI in the
echo      browser window, then click the green "done" button
echo   4. now GET /search is ready
echo ========================================
pause
