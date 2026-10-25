@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
title Yus Browser Pro 2.3 - Build
where py >nul 2>&1 || (echo Python launcher py not found.& pause& exit /b 1)
echo [1/4] Installing browser dependencies...
py -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto fail
echo [2/4] Checking application syntax...
py -m py_compile main.py browser_utils.py
if errorlevel 1 goto fail
echo [3/4] Installing PyInstaller...
py -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail
echo [4/4] Building standalone windowed app...
py -m PyInstaller --noconfirm --clean --windowed --onedir --name "Yus Browser Pro 2.3" --icon "assets\yus_browser_icon.ico" --add-data "assets;assets" main.py
if errorlevel 1 goto fail
if not exist "dist\Yus Browser Pro 2.3\Yus Browser Pro 2.3.exe" (echo EXE was not created.& goto fail)
cscript //nologo SETUP_NO_CONSOLE_SHORTCUT.vbs
echo.
echo BUILD COMPLETE. The app starts without a console window.
start "" "%CD%\dist\Yus Browser Pro 2.3\Yus Browser Pro 2.3.exe"
exit /b 0
:fail
echo BUILD FAILED. Copy the error text above.
pause
exit /b 1
