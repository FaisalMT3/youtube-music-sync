#!/usr/bin/env bash
# ==============================================================================
# Script: enable_auto_sync.sh
# Purpose: Configure background systemd user timer to automatically check and
#          download new songs added to your YouTube playlists every X minutes.
# ==============================================================================
set -euo pipefail

INTERVAL_MINUTES="${1:-15}"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

SCRIPT_PATH="/home/heaven/Projects/New/yt_sync.py"

echo "=========================================================="
echo "  Enabling Automated Background Playlist Sync"
echo "=========================================================="
echo "Check Interval: Every $INTERVAL_MINUTES minutes"
echo "Script:         $SCRIPT_PATH"
echo "=========================================================="

# 1. Create systemd service unit
cat <<EOF > "$SYSTEMD_USER_DIR/yt-playlist-sync.service"
[Unit]
Description=YouTube Playlist Background Auto-Sync
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $SCRIPT_PATH sync-all
StandardOutput=journal
StandardError=journal
EOF

# 2. Create systemd timer unit
cat <<EOF > "$SYSTEMD_USER_DIR/yt-playlist-sync.timer"
[Unit]
Description=Timer for YouTube Playlist Background Auto-Sync

[Timer]
OnBootSec=2min
OnUnitActiveSec=${INTERVAL_MINUTES}min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# 3. Reload systemd and enable timer
systemctl --user daemon-reload
systemctl --user enable --now yt-playlist-sync.timer

echo ""
echo "[+] Background Auto-Sync is now ACTIVE!"
echo "Status: Running every $INTERVAL_MINUTES minutes in the background."
echo "Whenever you add a new video/song to your tracked playlists on YouTube,"
echo "it will be automatically downloaded to ~/Music and synced to your Huawei Note!"
echo ""
echo "To check timer status:  systemctl --user list-timers yt-playlist-sync.timer"
echo "To disable auto-sync:   ./disable_auto_sync.sh"
echo "=========================================================="
