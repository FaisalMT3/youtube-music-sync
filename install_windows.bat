@echo off
setlocal enabledelayedexpansion
title YouTube Music Sync - Windows Setup Wizard
cd /d "%~dp0"

echo ======================================================================
echo   🎵 YouTube Music Sync - Automated Windows Setup Wizard
echo ======================================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [X] Python was not detected!
    echo.
    echo Attempting to install Python via Windows Package Manager (winget)...
    winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    if %errorlevel% neq 0 (
        echo [!] Could not auto-install Python.
        echo Please download and install Python from: https://www.python.org/downloads/
        echo (IMPORTANT: Check the box "Add Python to PATH" during installation!)
        pause
        exit /b 1
    )
    echo [✔] Python installed! Please restart this script.
    pause
    exit /b 0
)

echo [✔] Python is installed:
python --version
echo.

:: 2. Upgrade pip and install Python packages
echo [1/3] Installing required Python libraries (yt-dlp, mutagen, PyQt6)...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [!] Warning: Some packages failed to install with -r requirements.txt, trying individual install...
    python -m pip install --upgrade yt-dlp mutagen PyQt6
)
echo [✔] Python libraries successfully installed!
echo.

:: 3. Check FFmpeg (Required for MP3 audio conversion & thumbnail embedding)
echo [2/3] Checking FFmpeg audio engine...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    if exist "%~dp0ffmpeg.exe" (
        echo [✔] Local ffmpeg.exe detected in current directory!
    ) else (
        echo [!] FFmpeg is not found in PATH.
        echo Attempting to install FFmpeg via winget (Windows Package Manager)...
        winget install Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
        ffmpeg -version >nul 2>&1
        if %errorlevel% neq 0 (
            echo.
            echo [NOTE] If winget is unavailable, you can download ffmpeg-release-essentials.zip from:
            echo https://www.gyan.dev/ffmpeg/builds/
            echo and place 'ffmpeg.exe' in this exact folder!
        ) else (
            echo [✔] FFmpeg successfully installed via winget!
        )
    )
) else (
    echo [✔] FFmpeg is already installed and available in PATH!
)
echo.

:: 4. Create Desktop Shortcut
echo [3/3] Creating Windows Desktop Shortcut...
set SCRIPT_DIR=%~dp0
set TARGET_BAT=%SCRIPT_DIR%run_gui.bat
set SHORTCUT_PATH=%USERPROFILE%\Desktop\YouTube Music Sync.lnk

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT_PATH%'); $s.TargetPath = '%TARGET_BAT%'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Save()" >nul 2>&1
if exist "%SHORTCUT_PATH%" (
    echo [✔] Desktop shortcut created: "YouTube Music Sync" on your Desktop!
)

echo.
echo ======================================================================
echo   🎉 Installation Complete!
echo ======================================================================
echo You can now double-click "run_gui.bat" or the desktop shortcut
echo "YouTube Music Sync" to launch the application anytime!
echo ======================================================================
echo.
pause
