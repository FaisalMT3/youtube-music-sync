#!/usr/bin/env bash
# ==============================================================================
# Script: setup_syncthing.sh
# Purpose: Automated setup of Syncthing daemon on Arch Linux (omarchy)
# ==============================================================================
set -euo pipefail

echo "======================================================"
echo "  Setting up Syncthing for Local Sync on omarchy"
echo "======================================================"

# 1. Check if syncthing is installed, if not install via pacman
if ! command -v syncthing &>/dev/null; then
    echo "[1/4] Syncthing not found. Installing via pacman..."
    sudo pacman -S --noconfirm syncthing
else
    echo "[1/4] Syncthing is already installed."
fi

# 2. Ensure Music folder exists
MUSIC_DIR="${HOME}/Music"
if [ ! -d "$MUSIC_DIR" ]; then
    echo "[2/4] Creating Music folder at $MUSIC_DIR..."
    mkdir -p "$MUSIC_DIR"
else
    echo "[2/4] Music directory exists at $MUSIC_DIR."
fi

# 3. Enable and start systemd user service
echo "[3/4] Enabling and starting syncthing.service for user $(whoami)..."
systemctl --user enable syncthing.service
systemctl --user restart syncthing.service

# Wait a brief moment for Syncthing to initialize keys and config
sleep 3

# 4. Fetch and display Device ID & Web UI info
echo "[4/4] Fetching Device Information..."
DEVICE_ID=$(syncthing --device-id 2>/dev/null || true)
LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -n1 || echo "127.0.0.1")

echo ""
echo "======================================================"
echo "  Syncthing Successfully Configured on omarchy!"
echo "======================================================"
echo "Host Machine:       omarchy ($LOCAL_IP)"
echo "Web GUI URL:        http://127.0.0.1:8384"
echo "Synced Music Dir:   $MUSIC_DIR"
if [ -n "$DEVICE_ID" ]; then
    echo "Your Device ID:     $DEVICE_ID"
fi
echo "======================================================"
echo ""
echo "Next Steps:"
echo "1. Open http://127.0.0.1:8384 in your browser to access the Web GUI."
echo "2. Install 'Syncthing-Fork' on your Huawei Note 15 (from F-Droid or GitHub)."
echo "3. In the phone app, tap '+' to add device, scan the QR code from the Web GUI (Actions -> Show ID)."
echo "4. Share the 'Music' folder with your Huawei Note!"
echo ""
