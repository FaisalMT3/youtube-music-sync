@echo off
title YouTube Music Sync - GUI
cd /d "%~dp0"

echo ==========================================================
echo   Starting YouTube Playlist Incremental Downloader GUI...
echo ==========================================================

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python is not found in your PATH!
    echo Please install Python from https://www.python.org or run install_windows.bat
    pause
    exit /b 1
)

:: Run GUI
python yt_gui.py
if %errorlevel% neq 0 (
    echo.
    echo [!] An error occurred. If packages are missing, run install_windows.bat
    pause
)
