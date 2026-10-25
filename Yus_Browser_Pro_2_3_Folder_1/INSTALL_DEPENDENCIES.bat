@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
title Yus Browser Pro 2.3 - Setup
where py >nul 2>&1 || (echo Install Python 3.10 or newer from python.org, then retry.& pause& exit /b 1)
py -m pip install --upgrade pip
if errorlevel 1 goto fail
py -m pip install -r requirements.txt
if errorlevel 1 goto fail
py -c "from PySide6.QtWebEngineWidgets import QWebEngineView; print('Qt WebEngine OK')"
if errorlevel 1 goto fail
echo.
echo Dependencies installed successfully.
pause
exit /b 0
:fail
echo.
echo Setup failed. Copy the error above.
pause
exit /b 1
