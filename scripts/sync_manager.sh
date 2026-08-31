#!/usr/bin/env bash
# ==============================================================================
# Script: sync_manager.sh
# Purpose: CLI manager for checking and managing local sync on omarchy
# ==============================================================================
set -euo pipefail

API_KEY=""
CONFIG_FILE="${HOME}/.local/state/syncthing/config.xml"
if [ ! -f "$CONFIG_FILE" ]; then
    CONFIG_FILE="${HOME}/.config/syncthing/config.xml"
fi

if [ -f "$CONFIG_FILE" ]; then
    API_KEY=$(grep -oPm1 "(?<=<apikey>)[^<]+" "$CONFIG_FILE" || true)
fi

show_status() {
    echo "--- [ Service Status ] ---"
    if systemctl --user is-active --quiet syncthing.service; then
        echo "Syncthing Service: RUNNING (Active)"
    else
        echo "Syncthing Service: STOPPED"
        echo "Run './setup_syncthing.sh' or 'systemctl --user start syncthing' to start it."
        return
    fi

    echo ""
    echo "--- [ Device Information ] ---"
    syncthing --device-id 2>/dev/null || echo "Unable to query device ID directly"
    
    if [ -n "$API_KEY" ]; then
        echo ""
        echo "--- [ Connected Devices (LAN) ] ---"
        CONNECTIONS=$(curl -s -k -H "X-API-Key: $API_KEY" http://127.0.0.1:8384/rest/system/connections 2>/dev/null || echo "{}")
        echo "$CONNECTIONS" | grep -o '"connected": *[a-z]*' || echo "Checking device connections via Web GUI (http://127.0.0.1:8384)..."
    fi
}

add_test_song() {
    local MUSIC_DIR="${HOME}/Music"
    mkdir -p "$MUSIC_DIR"
    local COUNT
    COUNT=$(ls -1 "$MUSIC_DIR"/test_track_*.mp3 2>/dev/null | wc -l || echo 0)
    local NEXT=$((COUNT + 1))
    local FILE_PATH="$MUSIC_DIR/test_track_${NEXT}.mp3"
    
    echo "Creating dummy test audio file: $FILE_PATH (Track #$NEXT)..."
    # Create a small dummy file simulating an audio file
    head -c 1048576 </dev/urandom > "$FILE_PATH"
    echo "Done! $FILE_PATH created (1 MB). Watch your Huawei Note sync this track!"
}

list_music() {
    local MUSIC_DIR="${HOME}/Music"
    echo "--- [ Files in $MUSIC_DIR ] ---"
    if [ -d "$MUSIC_DIR" ]; then
        ls -lh "$MUSIC_DIR"
        echo "Total files: $(ls -1 "$MUSIC_DIR" | wc -l)"
    else
        echo "Music folder does not exist yet."
    fi
}

rescan_music() {
    if [ -n "$API_KEY" ]; then
        echo "Triggering rescan of Music folder..."
        curl -s -k -X POST -H "X-API-Key: $API_KEY" "http://127.0.0.1:8384/rest/db/scan?folder=music" || true
        echo "Rescan request sent."
    else
        echo "Web GUI API key not found. Rescan happens automatically upon file changes."
    fi
}

case "${1:-status}" in
    status)
        show_status
        ;;
    add-track)
        add_test_song
        ;;
    list)
        list_music
        ;;
    rescan)
        rescan_music
        ;;
    gui)
        echo "Opening Syncthing Web GUI in browser..."
        xdg-open "http://127.0.0.1:8384" 2>/dev/null || echo "Please open http://127.0.0.1:8384 in your browser."
        ;;
    *)
        echo "Usage: $0 {status|add-track|list|rescan|gui}"
        echo ""
        echo "Commands:"
        echo "  status     - Show syncthing daemon status and device info"
        echo "  add-track  - Generate a test song (e.g. 11th song) in ~/Music to test delta sync"
        echo "  list       - List all songs currently in ~/Music"
        echo "  rescan     - Force an immediate folder rescan"
        echo "  gui        - Open the Syncthing Web UI in your default browser"
        ;;
esac
