@echo off
title YouTube Music Sync - CLI Downloader
cd /d "%~dp0"

echo ==========================================================
echo   YouTube Playlist Incremental Downloader - CLI Sync
echo ==========================================================

python yt_sync.py sync-all
if %errorlevel% neq 0 (
    echo.
    echo [!] Download encountered an error or no playlists are configured.
    echo You can also run: python yt_sync.py --help
)
pause
