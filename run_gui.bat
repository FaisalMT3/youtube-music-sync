@echo off
cd /d "%~dp0"

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python was not detected on your system.
    echo Please double-click 'install_windows.bat' first to set up the app automatically!
    pause
    exit /b 1
)

:: Launch GUI window silently without keeping a black terminal window open
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw yt_gui.py
) else (
    start "" python yt_gui.py
)
exit /b 0
