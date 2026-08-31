#!/usr/bin/env python3
"""
Custom Sync Watcher Daemon (Option B Alternative)
-------------------------------------------------
Watches for Huawei Note presence on the local Wi-Fi network.
When the device is detected online:
1. Compares local ~/Music directory state.
2. If files changed or device newly connected, triggers differential rsync/adb transfer.
3. When offline, sleeps and waits for device to reconnect.
"""

import os
import sys
import time
import subprocess
from pathlib import Path

DEFAULT_PHONE_IP = os.getenv("PHONE_IP", "192.168.8.100") # Replace with your phone's local IP or hostname
LOCAL_MUSIC_DIR = os.path.expanduser("~/Music")
REMOTE_MUSIC_PATH = os.getenv("REMOTE_MUSIC_PATH", "/sdcard/Music/")
SSH_PORT = int(os.getenv("SSH_PORT", "8022")) # Termux default SSH port

def is_device_online(ip: str) -> bool:
    """Check if device responds to ping on local network."""
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return res.returncode == 0
    except Exception:
        return False

def get_dir_mtime(path: str) -> float:
    """Get the latest modification timestamp in directory."""
    max_mtime = 0.0
    p = Path(path)
    if not p.exists():
        return 0.0
    for root, _, files in os.walk(p):
        for f in files:
            fp = os.path.join(root, f)
            try:
                m = os.path.getmtime(fp)
                if m > max_mtime:
                    max_mtime = m
            except OSError:
                pass
    return max_mtime

def trigger_rsync_sync(phone_ip: str):
    """Executes rsync to sync only new/modified files to the phone."""
    print(f"[*] Starting differential rsync to {phone_ip}:{REMOTE_MUSIC_PATH}...")
    cmd = [
        "rsync",
        "-avzP",
        "--update",
        "--delete",
        "-e", f"ssh -p {SSH_PORT}",
        f"{LOCAL_MUSIC_DIR}/",
        f"{phone_ip}:{REMOTE_MUSIC_PATH}"
    ]
    try:
        res = subprocess.run(cmd)
        if res.returncode == 0:
            print("[+] Sync completed successfully!")
        else:
            print(f"[-] Rsync exited with code {res.returncode}")
    except Exception as e:
        print(f"[-] Error executing rsync: {e}")

def main():
    print("========================================================")
    print("  Custom Network Presence Sync Watcher (omarchy)")
    print("========================================================")
    print(f"Target Device IP:  {DEFAULT_PHONE_IP}")
    print(f"Local Music Dir:   {LOCAL_MUSIC_DIR}")
    print(f"Poll Interval:     10 seconds")
    print("========================================================")

    last_state = False
    last_synced_mtime = 0.0

    while True:
        online = is_device_online(DEFAULT_PHONE_IP)
        current_mtime = get_dir_mtime(LOCAL_MUSIC_DIR)

        if online and not last_state:
            print(f"[!] Phone connected to local network ({DEFAULT_PHONE_IP})!")
            trigger_rsync_sync(DEFAULT_PHONE_IP)
            last_synced_mtime = current_mtime
        elif online and current_mtime > last_synced_mtime:
            print("[!] Changes detected in Music directory while phone is online. Syncing...")
            trigger_rsync_sync(DEFAULT_PHONE_IP)
            last_synced_mtime = current_mtime
        elif not online and last_state:
            print(f"[-] Phone disconnected / offline. Waiting for return...")

        last_state = online
        time.sleep(10)

if __name__ == "__main__":
    main()
