@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
title Yus Browser Pro 2.3 - Diagnostics
where py >nul 2>&1 || (echo Python launcher not found. Run INSTALL_DEPENDENCIES.bat first.& pause & exit /b 1)
py -c "from PySide6.QtWebEngineWidgets import QWebEngineView; from PySide6.QtMultimedia import QSoundEffect" >nul 2>&1
if errorlevel 1 (
  echo Browser libraries are missing. Installing them now...
  py -m pip install --upgrade -r requirements.txt
  if errorlevel 1 (echo Dependency installation failed.& pause& exit /b 1)
)
py main.py
if errorlevel 1 (echo Browser closed with an error. Copy this message for troubleshooting.& pause)
endlocal
