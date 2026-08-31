#!/usr/bin/env bash
# ==============================================================================
# Script: disable_auto_sync.sh
# Purpose: Stop and disable the background YouTube playlist auto-sync timer
# ==============================================================================
set -euo pipefail

echo "Disabling background YouTube Playlist Auto-Sync timer..."
systemctl --user stop yt-playlist-sync.timer 2>/dev/null || true
systemctl --user disable yt-playlist-sync.timer 2>/dev/null || true
systemctl --user daemon-reload

echo "[+] Auto-Sync timer disabled. Downloads will only happen on-demand when you run the CLI or GUI."
