#!/usr/bin/env python3
"""
==============================================================================
YouTube Playlist Incremental Downloader - GUI Edition (yt_gui)
==============================================================================
Enhanced with:
- Live "Now Downloading" active song widget with progress bar, speed, & stage pills
- Playlist Preview Inspector with Synced (🟢) vs New (⚪) status indicators
- "Download Anyway" / Force Re-download toggles (per-song & whole playlist)
- Full Toast Notification & Visual Feedback System on every button
- Native Linux Folder Chooser + Quick Path chips
- Auto-shutdown on window close (Zero 24/7 background process)
==============================================================================
"""

import sys
import os
import re
import json
import time
import socket
import select
import signal
import threading
import subprocess
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, List, Optional, Any

# Configurations
CONFIG_DIR = Path.home() / ".config" / "yt_sync"
PLAYLISTS_FILE = CONFIG_DIR / "playlists.json"
ARCHIVE_FILE = CONFIG_DIR / "download_archive.txt"

def get_default_music_dir() -> Path:
    if sys.platform.startswith("win"):
        win_music = Path.home() / "Music"
        if win_music.exists():
            return win_music
        return Path.home() / "Downloads" / "Music"
    else:
        phone_dir = Path.home() / "phone"
        if phone_dir.exists():
            return phone_dir
        return Path.home() / "Music"

DEFAULT_MUSIC_DIR = get_default_music_dir()
DEFAULT_VIDEO_DIR = Path.home() / "Videos"
DEFAULT_DOWNLOADS_DIR = Path.home() / "Downloads"

def get_ytdlp_base_cmd() -> List[str]:
    import shutil
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    if sys.platform.startswith("win"):
        script_dir = Path(__file__).resolve().parent
        if (script_dir / "yt-dlp.exe").exists():
            return [str(script_dir / "yt-dlp.exe")]
    return [sys.executable, "-m", "yt_dlp"]

def get_ffmpeg_args() -> List[str]:
    import shutil
    if shutil.which("ffmpeg"):
        return []
    script_dir = Path(__file__).resolve().parent
    local_ffmpeg = script_dir / ("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg")
    if local_ffmpeg.exists():
        return ["--ffmpeg-location", str(local_ffmpeg)]
    return []

# Global App State
app_state = {
    "current_process": None,
    "is_downloading": False,
    "current_task_name": "",
    "active_download": {
        "title": "",
        "percent": 0.0,
        "speed": "",
        "eta": "",
        "size": "",
        "status": "Idle"
    },
    "log_history": [],
    "last_heartbeat": time.time(),
    "shutdown_requested": False,
    "lock": threading.Lock()
}

log_subscribers = []

def ensure_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not PLAYLISTS_FILE.exists():
        with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
    if not ARCHIVE_FILE.exists():
        ARCHIVE_FILE.touch()

def get_saved_playlists() -> List[Dict]:
    ensure_config()
    try:
        with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_playlists(data: List[Dict]):
    ensure_config()
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_archive_set() -> set:
    ensure_config()
    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            return {line.split()[-1] for line in f if line.strip()}
    except Exception:
        return set()

def get_archive_count() -> int:
    return len(get_archive_set())

def choose_folder_dialog(initial_path: str = "") -> Optional[str]:
    init_dir = os.path.expanduser(initial_path) if initial_path else str(DEFAULT_MUSIC_DIR)
    if not os.path.exists(init_dir):
        init_dir = str(Path.home())

    # Try Qt File Dialog if available
    try:
        from PyQt6.QtWidgets import QApplication, QFileDialog
        app = QApplication.instance()
        if not app:
            app = QApplication(sys.argv)
        chosen = QFileDialog.getExistingDirectory(None, "Select Destination Folder", init_dir)
        if chosen and os.path.exists(chosen):
            return chosen
    except Exception:
        pass

    # Windows PowerShell Folder Picker fallback
    if sys.platform.startswith("win"):
        try:
            ps_script = (
                f'[System.Reflection.Assembly]::LoadWithPartialName("System.windows.forms") | Out-Null;'
                f'$dialog = New-Object System.Windows.Forms.FolderBrowserDialog;'
                f'$dialog.SelectedPath = "{init_dir}";'
                f'$dialog.Description = "Select Destination Folder";'
                f'if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ $dialog.SelectedPath }}'
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True, text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    # Linux Zenity / Kdialog fallback
    if Path("/usr/bin/zenity").exists():
        try:
            res = subprocess.run(
                [
                    "zenity",
                    "--file-selection",
                    "--directory",
                    "--title=Select Destination Folder",
                    f"--filename={init_dir}/"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    if Path("/usr/bin/kdialog").exists():
        try:
            res = subprocess.run(
                ["kdialog", "--getexistingdirectory", init_dir, "--title", "Select Destination Folder"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    return None

def broadcast_log(line: str, log_type: str = "info"):
    entry = {"time": time.strftime("%H:%M:%S"), "text": line, "type": log_type}
    with app_state["lock"]:
        app_state["log_history"].append(entry)
        if len(app_state["log_history"]) > 600:
            app_state["log_history"].pop(0)
    for q in list(log_subscribers):
        try:
            q(entry)
        except Exception:
            if q in log_subscribers:
                log_subscribers.remove(q)

def inspect_playlist_items(url: str) -> Dict[str, Any]:
    """Inspects playlist and returns video list with indices, durations, and sync status."""
    cmd = get_ytdlp_base_cmd() + [
        "--flat-playlist",
        "--ignore-errors",
        "--print", "%(playlist_index,autonumber)s\t%(id)s\t%(title)s\t%(duration_string)s",
        url
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
        if proc.returncode != 0 and not proc.stdout.strip():
            return {"error": proc.stderr.strip() or f"yt-dlp exited with code {proc.returncode}"}

        archive_set = get_archive_set()
        items = []
        synced_count = 0

        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                idx_str = parts[0]
                try:
                    idx = int(idx_str)
                except ValueError:
                    idx = len(items) + 1
                vid_id = parts[1]
                title = parts[2]
                duration = parts[3] if len(parts) > 3 and parts[3] != "NA" else ""
                is_downloaded = vid_id in archive_set
                if is_downloaded:
                    synced_count += 1

                items.append({
                    "index": idx,
                    "id": vid_id,
                    "title": title,
                    "duration": duration,
                    "is_downloaded": is_downloaded
                })

        return {
            "success": True,
            "items": items,
            "count": len(items),
            "synced_count": synced_count,
            "pending_count": len(items) - synced_count
        }
    except subprocess.TimeoutExpired:
        return {"error": "Timeout while inspecting playlist. Please check your internet connection."}
    except Exception as e:
        return {"error": f"Failed to inspect playlist: {str(e)}"}

def update_active_progress(line: str):
    with app_state["lock"]:
        ad = app_state["active_download"]
        # Match destination title
        if "[download] Destination:" in line:
            dest_file = line.split("[download] Destination:")[-1].strip()
            title = Path(dest_file).stem
            ad["title"] = title
            ad["percent"] = 0.0
            ad["status"] = "Downloading"
        # Match progress line: [download]  45.2% of 3.26MiB at 5.87MiB/s ETA 00:01
        elif "[download]" in line and "%" in line:
            m = re.search(r"(\d+\.?\d*)%\s+of\s+~?(\S+)\s+at\s+(\S+)\s+ETA\s+(\S+)", line)
            if m:
                try:
                    ad["percent"] = float(m.group(1))
                except ValueError:
                    pass
                ad["size"] = m.group(2)
                ad["speed"] = m.group(3)
                ad["eta"] = m.group(4)
                ad["status"] = "Downloading"
        elif "[ExtractAudio]" in line:
            ad["status"] = "Extracting MP3"
            ad["percent"] = 90.0
        elif "[Metadata]" in line:
            ad["status"] = "Adding Metadata"
            ad["percent"] = 95.0
        elif "[EmbedThumbnail]" in line or "[ThumbnailsConvertor]" in line:
            ad["status"] = "Embedding Cover Art"
            ad["percent"] = 98.0

def run_download_job(
    url: str,
    mode: str,
    audio_fmt: str,
    output_path: str,
    playlist_start: int = 1,
    playlist_end: Optional[int] = None,
    force_redownload: bool = False
):
    ensure_config()
    out_dir = Path(output_path).expanduser() if output_path else (DEFAULT_MUSIC_DIR if mode == "audio" else DEFAULT_VIDEO_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = get_ytdlp_base_cmd() + [
        "--yes-playlist",
        "--ignore-errors",
        "--newline",
        "--add-metadata",
        "--embed-metadata",
    ] + get_ffmpeg_args()

    if force_redownload:
        cmd.extend(["--no-download-archive", "--force-overwrites"])
        force_text = " (Force Re-download ON - downloading anyway)"
    else:
        cmd.extend(["--download-archive", str(ARCHIVE_FILE), "--no-post-overwrites"])
        force_text = ""

    # Handle playlist start / end range
    if playlist_start > 1 or playlist_end:
        start_val = playlist_start if playlist_start >= 1 else 1
        if playlist_end and playlist_end >= start_val:
            cmd.extend(["--playlist-items", f"{start_val}:{playlist_end}"])
            range_desc = f"Items #{start_val} to #{playlist_end}"
        else:
            cmd.extend(["--playlist-start", str(start_val)])
            range_desc = f"Starting from Item #{start_val} (Skipping #{1} to #{start_val-1})"
    else:
        range_desc = "All items"

    if mode == "audio":
        cmd.extend([
            "-x",
            "--audio-format", audio_fmt or "mp3",
            "--audio-quality", "0",
            "--embed-thumbnail",
            "-o", str(out_dir / "%(title)s.%(ext)s")
        ])
    else:
        cmd.extend([
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--embed-thumbnail",
            "-o", str(out_dir / "%(title)s [%(id)s].%(ext)s")
        ])

    cmd.append(url)

    broadcast_log(f"🔗 Starting download: {url}", "header")
    broadcast_log(f"📁 Destination: {out_dir} | Mode: {mode.upper()} | Range: {range_desc}{force_text}", "header")
    broadcast_log("⏳ Contacting YouTube...", "info")

    with app_state["lock"]:
        app_state["active_download"] = {
            "title": "Contacting YouTube...",
            "percent": 0.0,
            "speed": "--",
            "eta": "--",
            "size": "--",
            "status": "Connecting"
        }

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        with app_state["lock"]:
            app_state["current_process"] = proc
            app_state["is_downloading"] = True
            app_state["current_task_name"] = url

        for line in iter(proc.stdout.readline, ''):
            clean_line = line.strip()
            if clean_line:
                update_active_progress(clean_line)
                if "has already been recorded in the archive" in clean_line:
                    broadcast_log(f"⏩ [Already Downloaded] {clean_line}", "skip")
                elif "[download]" in clean_line and "%" in clean_line:
                    broadcast_log(clean_line, "progress")
                elif "[download] Destination:" in clean_line:
                    broadcast_log(f"📥 {clean_line}", "download")
                elif "[ExtractAudio]" in clean_line or "[Metadata]" in clean_line or "[Thumbnails]" in clean_line or "[EmbedThumbnail]" in clean_line:
                    broadcast_log(f"🎵 {clean_line}", "audio")
                elif "ERROR" in clean_line:
                    broadcast_log(f"❌ {clean_line}", "error")
                elif "WARNING" in clean_line:
                    broadcast_log(f"⚠️ {clean_line}", "warning")
                else:
                    broadcast_log(clean_line, "info")

        proc.stdout.close()
        proc.wait()

        if proc.returncode == 0:
            broadcast_log("✅ Sync completed successfully! All new tracks are ready in your synced folder.", "success")
            with app_state["lock"]:
                app_state["active_download"]["percent"] = 100.0
                app_state["active_download"]["status"] = "Completed"
        elif proc.returncode in (-15, -9):
            broadcast_log("⚠️ Download was stopped by user.", "warning")
            with app_state["lock"]:
                app_state["active_download"]["status"] = "Stopped"
        else:
            broadcast_log(f"⚠️ Process finished with exit code {proc.returncode}", "warning")
            with app_state["lock"]:
                app_state["active_download"]["status"] = f"Finished ({proc.returncode})"

    except Exception as e:
        broadcast_log(f"❌ Error during execution: {str(e)}", "error")
        with app_state["lock"]:
            app_state["active_download"]["status"] = "Error"
    finally:
        with app_state["lock"]:
            app_state["current_process"] = None
            app_state["is_downloading"] = False
            app_state["current_task_name"] = ""

def run_sync_all_job():
    playlists = get_saved_playlists()
    if not playlists:
        broadcast_log("No saved playlists to sync. Add a playlist first!", "warning")
        return

    broadcast_log(f"🚀 Starting Auto-Sync for {len(playlists)} saved playlist(s)...", "header")
    for idx, p in enumerate(playlists, 1):
        broadcast_log(f"\n--- [ Playlist {idx}/{len(playlists)}: {p['name']} ] ---", "header")
        mode = p.get("mode", "audio")
        subfolder = p.get("subfolder", "").strip()
        base_dir = DEFAULT_MUSIC_DIR if mode == "audio" else DEFAULT_VIDEO_DIR
        out_dir = base_dir / subfolder if subfolder else base_dir
        run_download_job(p["url"], mode, "mp3", str(out_dir), force_redownload=False)
    broadcast_log("\n✨ All playlists synced!", "success")

# Single Page App Template with Now-Downloading Widget, Sync Badges & Force Re-download
HTML_PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YouTube Sync & Downloader</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --bg-dark: #090d16;
  --bg-card: rgba(18, 26, 44, 0.75);
  --bg-card-hover: rgba(26, 37, 63, 0.9);
  --border: rgba(255, 255, 255, 0.08);
  --border-focus: rgba(99, 102, 241, 0.5);
  --primary: #6366f1;
  --primary-glow: rgba(99, 102, 241, 0.35);
  --accent: #06b6d4;
  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --radius: 16px;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Plus Jakarta Sans', sans-serif;
  background-color: var(--bg-dark);
  background-image: 
    radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.14) 0px, transparent 50%),
    radial-gradient(at 100% 100%, rgba(6, 182, 212, 0.12) 0px, transparent 50%);
  color: var(--text-main);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
}}

/* Toast Container */
#toastContainer {{
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 420px;
  pointer-events: none;
}}
.toast {{
  pointer-events: auto;
  background: rgba(15, 23, 42, 0.95);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  animation: toastSlideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  transition: all 0.2s ease;
}}
.toast.toast-success {{ border-left: 4px solid var(--success); }}
.toast.toast-error {{ border-left: 4px solid var(--danger); }}
.toast.toast-warning {{ border-left: 4px solid var(--warning); }}
.toast.toast-info {{ border-left: 4px solid var(--accent); }}
.toast-icon {{ font-size: 1.2rem; display: flex; align-items: center; justify-content: center; }}
.toast-content {{ flex: 1; }}
.toast-title {{ font-size: 0.85rem; font-weight: 700; color: var(--text-main); }}
.toast-desc {{ font-size: 0.78rem; color: var(--text-muted); margin-top: 2px; }}
.toast-close {{
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px;
  font-size: 1rem;
}}
.toast-close:hover {{ color: var(--text-main); }}

@keyframes toastSlideIn {{
  from {{ transform: translateX(100%); opacity: 0; }}
  to {{ transform: translateX(0); opacity: 1; }}
}}
@keyframes toastFadeOut {{
  from {{ transform: translateX(0); opacity: 1; }}
  to {{ transform: translateX(100%); opacity: 0; }}
}}

/* Header */
header {{
  padding: 1.15rem 2rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border);
  background: rgba(9, 13, 22, 0.85);
  backdrop-filter: blur(12px);
  position: sticky;
  top: 0;
  z-index: 100;
}}
.logo-container {{
  display: flex;
  align-items: center;
  gap: 12px;
}}
.logo-icon {{
  width: 40px;
  height: 40px;
  background: linear-gradient(135deg, #ef4444, #6366f1);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 16px rgba(239, 68, 68, 0.35);
}}
.logo-icon svg {{ width: 22px; height: 22px; fill: white; }}
.logo-text h1 {{ font-size: 1.2rem; font-weight: 800; letter-spacing: -0.5px; }}
.logo-text span {{ font-size: 0.72rem; color: var(--accent); font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }}

.header-actions {{
  display: flex;
  align-items: center;
  gap: 12px;
}}
.badge-archive {{
  padding: 6px 14px;
  border-radius: 30px;
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.3);
  color: var(--success);
  font-size: 0.8rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}}
.btn-shutdown {{
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  padding: 8px 14px;
  border-radius: 10px;
  cursor: pointer;
  font-weight: 600;
  font-size: 0.85rem;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 6px;
}}
.btn-shutdown:hover {{
  background: var(--danger);
  color: white;
}}

/* Layout */
main {{
  flex: 1;
  padding: 1.75rem 2rem;
  max-width: 1300px;
  width: 100%;
  margin: 0 auto;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.75rem;
}}

@media (max-width: 950px) {{
  main {{ grid-template-columns: 1fr; }}
}}

/* Cards */
.card {{
  background: var(--bg-card);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 1.5rem;
  box-shadow: 0 8px 32px rgba(0,0,0,0.35);
  display: flex;
  flex-direction: column;
}}
.card-header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.25rem;
}}
.card-title {{
  font-size: 1.1rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
}}

/* Forms */
.form-group {{
  margin-bottom: 1.15rem;
}}
label {{
  display: block;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
input[type="text"], input[type="number"], select {{
  width: 100%;
  padding: 11px 14px;
  background: rgba(10, 15, 29, 0.85);
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--text-main);
  font-family: inherit;
  font-size: 0.92rem;
  transition: all 0.2s;
  outline: none;
}}
input[type="text"]:focus, input[type="number"]:focus, select:focus {{
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-glow);
}}
.row {{ display: flex; gap: 10px; }}
.row .form-group {{ flex: 1; }}

.btn {{
  padding: 11px 18px;
  border-radius: 12px;
  font-weight: 700;
  font-size: 0.92rem;
  font-family: inherit;
  cursor: pointer;
  border: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s;
}}
.btn-primary {{
  background: linear-gradient(135deg, var(--primary), #4f46e5);
  color: white;
  box-shadow: 0 4px 20px var(--primary-glow);
}}
.btn-primary:hover {{
  transform: translateY(-1px);
  box-shadow: 0 6px 24px var(--primary-glow);
}}
.btn-primary:disabled {{
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}}
.btn-secondary {{
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-main);
  border: 1px solid var(--border);
}}
.btn-secondary:hover {{
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.2);
}}
.btn-danger {{
  background: rgba(239, 68, 68, 0.15);
  color: #fca5a5;
  border: 1px solid rgba(239, 68, 68, 0.3);
}}
.btn-danger:hover {{
  background: var(--danger);
  color: white;
}}

/* Quick chips */
.quick-chip {{
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}}
.quick-chip:hover {{
  background: rgba(99, 102, 241, 0.2);
  color: #c7d2fe;
  border-color: var(--primary);
}}

/* Playlist Inspector Panel */
.inspector-panel {{
  background: rgba(10, 15, 29, 0.85);
  border: 1px solid rgba(99, 102, 241, 0.35);
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 1.15rem;
  display: none;
  flex-direction: column;
  gap: 10px;
  animation: fadeIn 0.25s ease;
}}
@keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(-6px); }} to {{ opacity: 1; transform: translateY(0); }} }}

.inspector-header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
}}
.inspect-stats {{
  display: flex;
  gap: 8px;
  font-size: 0.78rem;
  font-weight: 700;
}}
.badge-stat-synced {{
  color: var(--success);
  background: rgba(16, 185, 129, 0.15);
  padding: 3px 8px;
  border-radius: 12px;
  border: 1px solid rgba(16, 185, 129, 0.3);
}}
.badge-stat-pending {{
  color: var(--accent);
  background: rgba(6, 182, 212, 0.15);
  padding: 3px 8px;
  border-radius: 12px;
  border: 1px solid rgba(6, 182, 212, 0.3);
}}

.inspector-items-list {{
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-right: 4px;
}}
.inspect-row {{
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.8rem;
  gap: 8px;
  transition: all 0.15s;
}}
.inspect-row:hover {{ background: rgba(99, 102, 241, 0.12); border-color: rgba(99, 102, 241, 0.4); }}
.inspect-row.synced-item {{ border-left: 3px solid var(--success); }}
.inspect-row.pending-item {{ border-left: 3px solid var(--accent); }}

.badge-synced-pill {{
  background: rgba(16, 185, 129, 0.15);
  color: var(--success);
  font-size: 0.7rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}}
.badge-pending-pill {{
  background: rgba(6, 182, 212, 0.15);
  color: var(--accent);
  font-size: 0.7rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}}

.badge-start-btn {{
  background: rgba(99, 102, 241, 0.2);
  border: 1px solid var(--primary);
  color: #c7d2fe;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 0.72rem;
  cursor: pointer;
  font-weight: 600;
  transition: all 0.15s;
  white-space: nowrap;
}}
.badge-start-btn:hover {{ background: var(--primary); color: white; }}

.badge-redownload-btn {{
  background: rgba(245, 158, 11, 0.15);
  border: 1px solid rgba(245, 158, 11, 0.4);
  color: #fde68a;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 0.72rem;
  cursor: pointer;
  font-weight: 600;
  transition: all 0.15s;
  white-space: nowrap;
}}
.badge-redownload-btn:hover {{ background: var(--warning); color: black; }}

/* Now Downloading Widget */
.now-downloading-card {{
  background: rgba(15, 23, 42, 0.9);
  border: 1px solid rgba(99, 102, 241, 0.35);
  border-radius: 12px;
  padding: 12px 16px;
  margin-bottom: 1rem;
  display: none;
  flex-direction: column;
  gap: 8px;
}}
.nd-header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
}}
.nd-title {{
  font-weight: 700;
  font-size: 0.88rem;
  color: #c7d2fe;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 75%;
}}
.nd-stage-pill {{
  padding: 3px 8px;
  border-radius: 12px;
  font-size: 0.72rem;
  font-weight: 700;
  background: rgba(99, 102, 241, 0.25);
  color: #a5b4fc;
  border: 1px solid rgba(99, 102, 241, 0.4);
}}
.progress-bar-bg {{
  height: 8px;
  width: 100%;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  overflow: hidden;
}}
.progress-bar-fill {{
  height: 100%;
  width: 0%;
  background: linear-gradient(90deg, #6366f1, #06b6d4, #10b981);
  border-radius: 6px;
  transition: width 0.3s ease;
}}
.nd-meta {{
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
}}

/* Checkbox toggle */
.checkbox-label {{
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 0.82rem;
  color: #cbd5e1;
  text-transform: none;
  letter-spacing: normal;
  margin-bottom: 0;
}}
.checkbox-label input[type="checkbox"] {{
  width: 16px;
  height: 16px;
  accent-color: var(--warning);
  cursor: pointer;
}}

/* Playlists Grid */
.playlist-list {{
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 250px;
  overflow-y: auto;
  padding-right: 4px;
}}
.playlist-item {{
  background: rgba(10, 15, 29, 0.65);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: all 0.2s;
}}
.playlist-item:hover {{
  background: var(--bg-card-hover);
  border-color: rgba(99, 102, 241, 0.3);
}}
.pl-info h4 {{ font-size: 0.92rem; font-weight: 700; }}
.pl-info p {{ font-size: 0.76rem; color: var(--text-muted); margin-top: 2px; }}
.pl-actions {{ display: flex; gap: 8px; }}

/* Console / Log Viewer */
.console-card {{
  grid-column: 1 / -1;
  min-height: 300px;
}}
.console-box {{
  background: #050811;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 12px;
  padding: 1rem;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.84rem;
  height: 240px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}}
.log-line {{
  line-height: 1.4;
  word-break: break-all;
}}
.log-time {{ color: #64748b; margin-right: 8px; font-size: 0.76rem; }}
.log-header {{ color: #818cf8; font-weight: 600; }}
.log-skip {{ color: #38bdf8; }}
.log-progress {{ color: #fbbf24; }}
.log-download {{ color: #34d399; font-weight: 600; }}
.log-audio {{ color: #a78bfa; }}
.log-success {{ color: #4ade80; font-weight: 700; }}
.log-error {{ color: #f87171; font-weight: 700; }}
.log-warning {{ color: #fb923c; }}
.log-info {{ color: #cbd5e1; }}

/* Spinner */
.spinner {{
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255,255,255,0.3);
  border-radius: 50%;
  border-top-color: white;
  animation: spin 0.8s linear infinite;
}}
@keyframes spin {{
  to {{ transform: rotate(360deg); }}
}}

.info-banner {{
  background: rgba(99, 102, 241, 0.08);
  border: 1px solid rgba(99, 102, 241, 0.2);
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 0.82rem;
  color: #c7d2fe;
  margin-top: 0.75rem;
  display: flex;
  align-items: center;
  gap: 10px;
}}
</style>
</head>
<body>

<div id="toastContainer"></div>

<header>
  <div class="logo-container">
    <div class="logo-icon">
      <svg viewBox="0 0 24 24"><path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816-.029-6.185-.484-8.549-4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z"/></svg>
    </div>
    <div class="logo-text">
      <h1>YouTube Sync</h1>
      <span>Incremental Downloader</span>
    </div>
  </div>

  <div class="header-actions">
    <div class="badge-archive" id="archiveBadge">
      <span>●</span> <span id="archiveCount">0</span> downloaded items recorded
    </div>
    <button class="btn-shutdown" onclick="shutdownApp()" id="btnShutdown" title="Close and shutdown application server">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10"/></svg>
      Exit App
    </button>
  </div>
</header>

<main>
  <!-- Card 1: Direct Download -->
  <div class="card">
    <div class="card-header">
      <div class="card-title">
        <span>⚡</span> Direct Playlist / Video Download
      </div>
    </div>

    <form id="directForm" onsubmit="handleDirectDownload(event)">
      <div class="form-group">
        <label>YouTube Playlist or Video URL</label>
        <div style="display: flex; gap: 8px;">
          <input type="text" id="directUrl" placeholder="https://www.youtube.com/playlist?list=..." required />
          <button type="button" class="btn btn-secondary" onclick="inspectPlaylist()" id="btnInspect" style="padding: 10px 14px; font-size: 0.85rem; white-space: nowrap;">
            🔍 Check Items
          </button>
        </div>
      </div>

      <!-- Playlist Video Range & Item Inspector Panel -->
      <div class="inspector-panel" id="inspectorPanel">
        <div class="inspector-header">
          <div class="inspect-stats">
            <span class="badge-stat-synced" id="statSynced">🟢 0 Synced</span>
            <span class="badge-stat-pending" id="statPending">⚪ 0 Pending</span>
          </div>
          <button type="button" class="quick-chip" onclick="closeInspector()">✕ Hide</button>
        </div>
        
        <div class="inspector-items-list" id="inspectItemsList">
          <!-- Populated dynamically -->
        </div>

        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-top: 4px;">
          <div style="display: flex; align-items: center; gap: 6px; font-size: 0.8rem; color: var(--text-muted);">
            <span>Range:</span>
            <button type="button" class="quick-chip" onclick="setPlaylistRange(1, null)">From #1 (All)</button>
            <button type="button" class="quick-chip" onclick="setPlaylistRange(4, null)">Skip First 3 (#4+)</button>
          </div>
        </div>
      </div>

      <!-- Start Video Index / Range -->
      <div class="row">
        <div class="form-group">
          <label>Start from Video # (Skip earlier)</label>
          <input type="number" id="directStart" min="1" value="1" placeholder="1" />
        </div>
        <div class="form-group">
          <label>End Video # (Optional)</label>
          <input type="number" id="directEnd" min="1" placeholder="End" />
        </div>
      </div>

      <div class="row">
        <div class="form-group">
          <label>Mode</label>
          <select id="directMode" onchange="toggleFormatSelect()">
            <option value="audio">Audio (Music for Phone)</option>
            <option value="video">Video (MP4 High Res)</option>
          </select>
        </div>
        <div class="form-group" id="formatGroup">
          <label>Audio Format</label>
          <select id="directFormat">
            <option value="mp3">MP3 (Best Compatibility)</option>
            <option value="m4a">M4A (AAC)</option>
            <option value="opus">Opus</option>
            <option value="flac">FLAC</option>
          </select>
        </div>
      </div>

      <div class="form-group">
        <label>Destination Folder</label>
        <div style="display: flex; gap: 8px;">
          <input type="text" id="directOutput" value="{DEFAULT_MUSIC_DIR}" placeholder="{DEFAULT_MUSIC_DIR}" style="flex: 1;" />
          <button type="button" class="btn btn-secondary" onclick="browseFolder('directOutput')" id="btnBrowse" style="padding: 10px 14px; font-size: 0.85rem; white-space: nowrap;">
            📁 Browse...
          </button>
        </div>
        <div style="display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;">
          <button type="button" class="quick-chip" onclick="setQuickFolder('directOutput', '{DEFAULT_MUSIC_DIR}')">🎵 ~/phone (Syncthing)</button>
          <button type="button" class="quick-chip" onclick="setQuickFolder('directOutput', '{DEFAULT_VIDEO_DIR}')">🎬 ~/Videos</button>
          <button type="button" class="quick-chip" onclick="setQuickFolder('directOutput', '{DEFAULT_DOWNLOADS_DIR}')">📥 ~/Downloads</button>
        </div>
      </div>

      <!-- Force Re-download Option -->
      <div class="form-group" style="margin-bottom: 0.85rem;">
        <label class="checkbox-label">
          <input type="checkbox" id="directForce" />
          <span>⚡ <strong>Download anyway</strong> (Force re-download even if already synced)</span>
        </label>
      </div>

      <div style="display: flex; gap: 10px; margin-top: 0.5rem;">
        <button type="submit" class="btn btn-primary" id="btnDownload" style="flex: 1;">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
          <span id="btnDownloadText">Download / Sync Incremental</span>
        </button>
        <button type="button" class="btn btn-danger" id="btnStop" onclick="stopDownload()" style="display: none;">
          Stop
        </button>
      </div>

      <div class="info-banner">
        <span>💡</span>
        <span>By default, only <strong>new</strong> tracks are downloaded. Check <em>Download anyway</em> if you ever want to re-fetch existing tracks!</span>
      </div>
    </form>
  </div>

  <!-- Card 2: Saved Playlists Manager -->
  <div class="card">
    <div class="card-header">
      <div class="card-title">
        <span>📑</span> Saved Auto-Sync Playlists
      </div>
      <button class="btn btn-primary" style="padding: 6px 12px; font-size: 0.82rem;" onclick="syncAllPlaylists()" id="btnSyncAll">
        🔄 Sync All
      </button>
    </div>

    <div class="playlist-list" id="playlistContainer">
      <p style="color: var(--text-muted); font-size: 0.88rem; text-align: center; padding: 2rem;">Loading saved playlists...</p>
    </div>

    <!-- Quick Add Playlist Form -->
    <div style="margin-top: 1.25rem; padding-top: 1rem; border-top: 1px solid var(--border);">
      <label>Add New Playlist to Track</label>
      <div class="row" style="margin-bottom: 8px;">
        <input type="text" id="newPlName" placeholder="Playlist Name (e.g. Chill Beats)" style="flex: 1;" />
        <select id="newPlMode" style="width: 120px;">
          <option value="audio">Audio</option>
          <option value="video">Video</option>
        </select>
      </div>
      <div class="row" style="margin-bottom: 8px;">
        <input type="text" id="newPlUrl" placeholder="Playlist URL (https://...)" style="flex: 1;" />
      </div>
      <div class="row">
        <input type="text" id="newPlFolder" placeholder="Subfolder name (optional)" style="flex: 1;" />
        <button class="btn btn-secondary" onclick="addNewPlaylist()" id="btnAddPlaylist">+ Add</button>
      </div>
    </div>
  </div>

  <!-- Card 3: Live Output Terminal & Now-Downloading Widget -->
  <div class="card console-card">
    <!-- Active Track Widget -->
    <div class="now-downloading-card" id="nowDownloadingCard">
      <div class="nd-header">
        <div class="nd-title" id="ndTitle">🎵 Preparing...</div>
        <div class="nd-stage-pill" id="ndStage">Starting</div>
      </div>
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="ndProgressBar"></div>
      </div>
      <div class="nd-meta">
        <span id="ndPercent">0%</span>
        <span id="ndSize">--</span>
        <span id="ndSpeed">--</span>
        <span id="ndEta">ETA --</span>
      </div>
    </div>

    <div class="card-header" style="margin-bottom: 0.75rem;">
      <div class="card-title">
        <span>🖥️</span> Live Download Stream & Status
      </div>
      <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 0.78rem;" onclick="clearLogs()">
        Clear Log
      </button>
    </div>
    <div class="console-box" id="consoleBox">
      <div class="log-line log-info"><span class="log-time">[System]</span> Ready. Ready to sync and download.</div>
    </div>
  </div>
</main>

<script>
let isDownloading = false;
let inspectedVideos = [];

// Toast Notification System
function showToast(message, type = 'info', title = '') {{
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast toast-${{type}}`;

  const icons = {{
    success: '✅',
    error: '❌',
    warning: '⚠️',
    info: 'ℹ️'
  }};

  const titles = {{
    success: title || 'Success',
    error: title || 'Error',
    warning: title || 'Warning',
    info: title || 'Notice'
  }};

  toast.innerHTML = `
    <div class="toast-icon">${{icons[type] || 'ℹ️'}}</div>
    <div class="toast-content">
      <div class="toast-title">${{escapeHtml(titles[type])}}</div>
      <div class="toast-desc">${{escapeHtml(message)}}</div>
    </div>
    <button class="toast-close" onclick="this.parentElement.remove()">✕</button>
  `;

  container.appendChild(toast);
  setTimeout(() => {{
    toast.style.animation = 'toastFadeOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }}, 4000);
}}

function toggleFormatSelect() {{
  const mode = document.getElementById('directMode').value;
  document.getElementById('formatGroup').style.display = mode === 'audio' ? 'block' : 'none';
  const outInput = document.getElementById('directOutput');
  if (mode === 'video' && outInput.value === '{DEFAULT_MUSIC_DIR}') {{
    outInput.value = '{DEFAULT_VIDEO_DIR}';
  }} else if (mode === 'audio' && outInput.value === '{DEFAULT_VIDEO_DIR}') {{
    outInput.value = '{DEFAULT_MUSIC_DIR}';
  }}
}}

function setQuickFolder(inputId, path) {{
  document.getElementById(inputId).value = path;
  showToast(`Destination set to: ${{path}}`, 'info', 'Folder Selected');
}}

async function browseFolder(inputId) {{
  const btn = document.getElementById('btnBrowse');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Browsing...';
  const currentVal = document.getElementById(inputId).value.trim();

  try {{
    const res = await fetch('/api/browse-folder', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ current: currentVal }})
    }});
    const data = await res.json();
    if (data.folder) {{
      document.getElementById(inputId).value = data.folder;
      showToast(`Selected folder: ${{data.folder}}`, 'success', 'Folder Chosen');
    }} else {{
      showToast('Folder selection was cancelled', 'info');
    }}
  }} catch (err) {{
    showToast(`Failed to open folder picker: ${{err}}`, 'error');
  }} finally {{
    btn.disabled = false;
    btn.innerHTML = '📁 Browse...';
  }}
}}

async function inspectPlaylist() {{
  const url = document.getElementById('directUrl').value.trim();
  if (!url) {{
    showToast('Please enter a YouTube playlist URL first', 'warning');
    document.getElementById('directUrl').focus();
    return;
  }}

  const btn = document.getElementById('btnInspect');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Checking...';

  try {{
    const res = await fetch('/api/playlist/inspect', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url }})
    }});
    const data = await res.json();

    if (data.error) {{
      showToast(data.error, 'error', 'Inspection Failed');
      return;
    }}

    inspectedVideos = data.items || [];
    renderInspector(inspectedVideos, data.synced_count, data.pending_count);
    showToast(`Found ${{data.count}} videos (${{data.synced_count}} synced, ${{data.pending_count}} pending)!`, 'success', 'Playlist Checked');
  }} catch (err) {{
    showToast(`Error checking playlist: ${{err}}`, 'error');
  }} finally {{
    btn.disabled = false;
    btn.innerHTML = '🔍 Check Items';
  }}
}}

function renderInspector(items, syncedCount, pendingCount) {{
  const panel = document.getElementById('inspectorPanel');
  const statSynced = document.getElementById('statSynced');
  const statPending = document.getElementById('statPending');
  const list = document.getElementById('inspectItemsList');

  const sCount = syncedCount !== undefined ? syncedCount : items.filter(i => i.is_downloaded).length;
  const pCount = pendingCount !== undefined ? pendingCount : (items.length - sCount);

  statSynced.textContent = `🟢 ${{sCount}} Synced`;
  statPending.textContent = `⚪ ${{pCount}} Pending`;

  const currentStart = parseInt(document.getElementById('directStart').value) || 1;

  list.innerHTML = items.map(item => `
    <div class="inspect-row ${{item.is_downloaded ? 'synced-item' : 'pending-item'}}" id="inspectRow_${{item.index}}">
      <div style="display:flex;align-items:center;gap:8px;flex:1;overflow:hidden;">
        <span style="font-weight:700;color:var(--accent);min-width:26px;">#${{item.index}}</span>
        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${{escapeHtml(item.title)}}">${{escapeHtml(item.title)}}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        ${{item.duration ? `<span style="color:var(--text-muted);font-size:0.75rem;">${{escapeHtml(item.duration)}}</span>` : ''}}
        ${{item.is_downloaded 
          ? `<span class="badge-synced-pill">✓ Synced</span>
             <button type="button" class="badge-redownload-btn" onclick="downloadSingleVideoDirect('${{item.id}}', '${{escapeHtml(item.title)}}', true)" title="Re-download this song anyway">🔄 Re-download</button>` 
          : `<span class="badge-pending-pill">Pending</span>
             <button type="button" class="badge-start-btn" onclick="downloadSingleVideoDirect('${{item.id}}', '${{escapeHtml(item.title)}}', false)">⬇️ Download</button>`
        }}
        <button type="button" class="badge-start-btn" onclick="setPlaylistStartItem(${{item.index}})" title="Start downloading playlist from here">
          ${{item.index === currentStart ? '★ Start Here' : '# ' + item.index + ' ➔'}}
        </button>
      </div>
    </div>
  `).join('');

  panel.style.display = 'flex';
}}

function closeInspector() {{
  document.getElementById('inspectorPanel').style.display = 'none';
}}

function setPlaylistStartItem(idx) {{
  document.getElementById('directStart').value = idx;
  showToast(`Will start downloading from video #${{idx}}`, 'info', 'Start Video Set');
  if (inspectedVideos.length > 0) {{
    renderInspector(inspectedVideos);
  }}
}}

function setPlaylistRange(startIdx, endIdx) {{
  document.getElementById('directStart').value = startIdx || 1;
  document.getElementById('directEnd').value = endIdx || '';
  const desc = endIdx ? `Videos #${{startIdx}} to #${{endIdx}}` : `From video #${{startIdx}} to End`;
  showToast(`Range set: ${{desc}}`, 'info', 'Range Updated');
  if (inspectedVideos.length > 0) {{
    renderInspector(inspectedVideos);
  }}
}}

async function downloadSingleVideoDirect(vidId, title, force) {{
  const videoUrl = `https://www.youtube.com/watch?v=${{vidId}}`;
  const mode = document.getElementById('directMode').value;
  const format = document.getElementById('directFormat').value;
  const output = document.getElementById('directOutput').value.trim();

  showToast(`Downloading '${{title.substring(0, 30)}}...'`, 'info', force ? 'Re-downloading Anyway' : 'Downloading Track');

  await fetch('/api/download', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ url: videoUrl, mode, format, output, force }})
  }});
  fetchStatus();
}}

async function fetchStatus() {{
  try {{
    const res = await fetch('/api/status');
    const data = await res.json();
    document.getElementById('archiveCount').textContent = data.archive_count;
    isDownloading = data.is_downloading;
    
    const btn = document.getElementById('btnDownload');
    const btnText = document.getElementById('btnDownloadText');
    const btnStop = document.getElementById('btnStop');
    const ndCard = document.getElementById('nowDownloadingCard');

    if (isDownloading) {{
      btn.disabled = true;
      btnText.innerHTML = '<span class="spinner"></span> Downloading...';
      btnStop.style.display = 'inline-flex';
      ndCard.style.display = 'flex';

      // Update active download widget
      const ad = data.active_download || {{}};
      if (ad.title) {{
        document.getElementById('ndTitle').textContent = `🎵 ${{ad.title}}`;
      }}
      document.getElementById('ndStage').textContent = ad.status || 'Downloading';
      const pct = (ad.percent || 0).toFixed(1);
      document.getElementById('ndProgressBar').style.width = `${{pct}}%`;
      document.getElementById('ndPercent').textContent = `${{pct}}%`;
      document.getElementById('ndSize').textContent = ad.size || '--';
      document.getElementById('ndSpeed').textContent = ad.speed || '--';
      document.getElementById('ndEta').textContent = ad.eta ? `ETA ${{ad.eta}}` : '--';
    }} else {{
      btn.disabled = false;
      btnText.textContent = 'Download / Sync Incremental';
      btnStop.style.display = 'none';
      if (!data.active_download || data.active_download.status === 'Completed' || data.active_download.status === 'Idle') {{
        ndCard.style.display = 'none';
      }}
    }}
  }} catch (err) {{
    console.error(err);
  }}
}}

async function loadPlaylists() {{
  try {{
    const res = await fetch('/api/playlists');
    const list = await res.json();
    const container = document.getElementById('playlistContainer');
    if (list.length === 0) {{
      container.innerHTML = '<p style="color: var(--text-muted); font-size: 0.88rem; text-align: center; padding: 2rem;">No saved playlists yet. Add one below!</p>';
      return;
    }}
    container.innerHTML = list.map((p, idx) => `
      <div class="playlist-item">
        <div class="pl-info">
          <h4>${{escapeHtml(p.name)}} <span style="font-size: 0.75rem; color: var(--accent); font-weight: normal;">[${{p.mode.toUpperCase()}}]</span></h4>
          <p>${{escapeHtml(p.url.substring(0, 45))}}...</p>
        </div>
        <div class="pl-actions">
          <button class="btn btn-secondary" id="btnSyncPl_${{idx}}" style="padding: 6px 10px; font-size: 0.8rem;" onclick="syncSinglePlaylist('${{encodeURIComponent(p.url)}}', '${{p.mode}}', ${{idx}})">
            Sync
          </button>
          <button class="btn btn-danger" id="btnDelPl_${{idx}}" style="padding: 6px 10px; font-size: 0.8rem;" onclick="removePlaylist(${{idx}}, '${{escapeHtml(p.name)}}')">
            ✕
          </button>
        </div>
      </div>
    `).join('');
  }} catch (err) {{
    console.error(err);
  }}
}}

async function addNewPlaylist() {{
  const name = document.getElementById('newPlName').value.trim();
  const url = document.getElementById('newPlUrl').value.trim();
  const mode = document.getElementById('newPlMode').value;
  const subfolder = document.getElementById('newPlFolder').value.trim();
  const btn = document.getElementById('btnAddPlaylist');

  if (!name || !url) {{
    showToast('Please enter both playlist name and YouTube URL', 'warning', 'Missing Fields');
    return;
  }}

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Adding...';

  try {{
    const res = await fetch('/api/playlists/add', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ name, url, mode, subfolder }})
    }});
    const data = await res.json();
    if (data.success) {{
      showToast(`Added playlist: ${{name}}`, 'success', 'Playlist Saved');
      document.getElementById('newPlName').value = '';
      document.getElementById('newPlUrl').value = '';
      document.getElementById('newPlFolder').value = '';
      loadPlaylists();
    }} else {{
      showToast(data.error || 'Failed to add playlist', 'error');
    }}
  }} catch (err) {{
    showToast(`Error: ${{err}}`, 'error');
  }} finally {{
    btn.disabled = false;
    btn.innerHTML = '+ Add';
  }}
}}

async function removePlaylist(idx, name) {{
  if (!confirm(`Remove playlist '${{name}}' from auto-sync list?`)) return;

  const btn = document.getElementById(`btnDelPl_${{idx}}`);
  if (btn) {{
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
  }}

  try {{
    const res = await fetch('/api/playlists/remove', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ index: idx }})
    }});
    const data = await res.json();
    if (data.success) {{
      showToast(`Removed playlist: ${{name}}`, 'info', 'Removed');
      loadPlaylists();
    }} else {{
      showToast(data.error || 'Failed to remove playlist', 'error');
    }}
  }} catch (err) {{
    showToast(`Error: ${{err}}`, 'error');
  }}
}}

async function handleDirectDownload(e) {{
  e.preventDefault();
  const url = document.getElementById('directUrl').value.trim();
  const mode = document.getElementById('directMode').value;
  const format = document.getElementById('directFormat').value;
  const output = document.getElementById('directOutput').value.trim();
  const start = parseInt(document.getElementById('directStart').value) || 1;
  const end = parseInt(document.getElementById('directEnd').value) || null;
  const force = document.getElementById('directForce').checked;

  document.getElementById('btnDownload').disabled = true;
  document.getElementById('btnDownloadText').innerHTML = '<span class="spinner"></span> Starting...';

  try {{
    const res = await fetch('/api/download', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url, mode, format, output, start, end, force }})
    }});
    const data = await res.json();
    if (data.error) {{
      showToast(data.error, 'error', 'Download Error');
    }} else {{
      showToast(force ? 'Download started (Force Re-download ON)!' : 'Incremental sync started!', 'success', 'Started');
    }}
  }} catch (err) {{
    showToast(`Failed to start download: ${{err}}`, 'error');
  }}
  fetchStatus();
}}

async function syncSinglePlaylist(urlEnc, mode, idx) {{
  const url = decodeURIComponent(urlEnc);
  const btn = document.getElementById(`btnSyncPl_${{idx}}`);
  if (btn) {{
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
  }}

  try {{
    const res = await fetch('/api/download', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url, mode, format: 'mp3', output: '', start: 1, force: false }})
    }});
    const data = await res.json();
    if (data.error) {{
      showToast(data.error, 'error');
    }} else {{
      showToast('Sync job started for playlist!', 'info', 'Syncing');
    }}
  }} catch (err) {{
    showToast(`Error: ${{err}}`, 'error');
  }} finally {{
    fetchStatus();
    if (btn) {{
      btn.disabled = false;
      btn.innerHTML = 'Sync';
    }}
  }}
}}

async function syncAllPlaylists() {{
  const btn = document.getElementById('btnSyncAll');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Syncing...';

  try {{
    const res = await fetch('/api/sync-all', {{ method: 'POST' }});
    const data = await res.json();
    if (data.error) {{
      showToast(data.error, 'error');
    }} else {{
      showToast('Auto-Sync started for all saved playlists!', 'success', 'Syncing All');
    }}
  }} catch (err) {{
    showToast(`Error: ${{err}}`, 'error');
  }} finally {{
    fetchStatus();
    btn.disabled = false;
    btn.innerHTML = '🔄 Sync All';
  }}
}}

async function stopDownload() {{
  const btn = document.getElementById('btnStop');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Stopping...';

  try {{
    await fetch('/api/stop', {{ method: 'POST' }});
    showToast('Download process stopped', 'warning', 'Stopped');
  }} catch (err) {{
    showToast(`Error stopping download: ${{err}}`, 'error');
  }} finally {{
    fetchStatus();
    btn.disabled = false;
    btn.innerHTML = 'Stop';
  }}
}}

async function shutdownApp() {{
  if (confirm("Shutdown YouTube Sync application?")) {{
    const btn = document.getElementById('btnShutdown');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Exiting...';
    showToast('Stopping server and closing app...', 'info', 'Shutting Down');
    await fetch('/api/shutdown', {{ method: 'POST' }});
    setTimeout(() => {{
      window.close();
      document.body.innerHTML = '<div style="display:flex;height:100vh;align-items:center;justify-content:center;color:white;font-family:sans-serif;"><h1>App Closed. You may close this tab.</h1></div>';
    }}, 400);
  }}
}}

function clearLogs() {{
  document.getElementById('consoleBox').innerHTML = '<div class="log-line log-info"><span class="log-time">[System]</span> Log cleared.</div>';
  showToast('Console logs cleared', 'info');
}}

function escapeHtml(text) {{
  if (!text) return '';
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}}

function setupSSE() {{
  const evtSource = new EventSource('/api/logs/stream');
  evtSource.onmessage = function(e) {{
    const entry = JSON.parse(e.data);
    const box = document.getElementById('consoleBox');
    const div = document.createElement('div');
    div.className = `log-line log-${{entry.type || 'info'}}`;
    div.innerHTML = `<span class="log-time">[${{entry.time}}]</span> ${{escapeHtml(entry.text)}}`;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    fetchStatus();
  }};
}}

// Heartbeat to automatically shut down backend if window is closed
setInterval(() => {{
  fetch('/api/heartbeat', {{ method: 'POST' }}).catch(() => {{}});
}}, 3000);

// Status polling every 1 second when active
setInterval(() => {{
  if (isDownloading) {{
    fetchStatus();
  }}
}}, 1000);

// Init
fetchStatus();
loadPlaylists();
setupSSE();
</script>
</body>
</html>
"""

class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default terminal request logs
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            with app_state["lock"]:
                self._send_json({
                    "is_downloading": app_state["is_downloading"],
                    "archive_count": get_archive_count(),
                    "task_name": app_state["current_task_name"],
                    "active_download": dict(app_state["active_download"])
                })
        elif path == "/api/playlists":
            self._send_json(get_saved_playlists())
        elif path == "/api/logs/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            queue = []
            lock = threading.Lock()

            def listener(entry):
                with lock:
                    queue.append(entry)

            log_subscribers.append(listener)

            # Send historical logs first
            with app_state["lock"]:
                for h in app_state["log_history"][-30:]:
                    data = f"data: {json.dumps(h)}\\n\\n"
                    try:
                        self.wfile.write(data.encode("utf-8"))
                    except Exception:
                        break
                try:
                    self.wfile.flush()
                except Exception:
                    pass

            try:
                while not app_state["shutdown_requested"]:
                    to_send = []
                    with lock:
                        if queue:
                            to_send = list(queue)
                            queue.clear()
                    for item in to_send:
                        data = f"data: {json.dumps(item)}\\n\\n"
                        self.wfile.write(data.encode("utf-8"))
                    if to_send:
                        self.wfile.flush()
                    time.sleep(0.1)
            except Exception:
                pass
            finally:
                if listener in log_subscribers:
                    log_subscribers.remove(listener)
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(length) if length > 0 else b'{}'
        body = {}
        if post_data:
            try:
                body = json.loads(post_data.decode('utf-8'))
            except Exception:
                body = {}

        if path == "/api/heartbeat":
            with app_state["lock"]:
                app_state["last_heartbeat"] = time.time()
            self._send_json({"status": "ok"})

        elif path == "/api/browse-folder":
            current_val = body.get("current", "")
            selected = choose_folder_dialog(current_val)
            if selected:
                self._send_json({"folder": selected})
            else:
                self._send_json({"cancelled": True})

        elif path == "/api/playlist/inspect":
            url = body.get("url", "").strip()
            if not url:
                self._send_json({"error": "Please provide a playlist URL"}, 400)
                return
            res = inspect_playlist_items(url)
            self._send_json(res)

        elif path == "/api/playlists/add":
            name = body.get("name", "").strip()
            url = body.get("url", "").strip()
            mode = body.get("mode", "audio")
            subfolder = body.get("subfolder", "").strip()
            if name and url:
                playlists = get_saved_playlists()
                playlists.append({"name": name, "url": url, "mode": mode, "subfolder": subfolder})
                save_playlists(playlists)
                broadcast_log(f"Saved new playlist: '{name}'", "success")
                self._send_json({"success": True})
            else:
                self._send_json({"error": "Missing name or url"}, 400)

        elif path == "/api/playlists/remove":
            idx = body.get("index")
            if idx is not None and isinstance(idx, int):
                playlists = get_saved_playlists()
                if 0 <= idx < len(playlists):
                    removed = playlists.pop(idx)
                    save_playlists(playlists)
                    broadcast_log(f"Removed playlist: '{removed['name']}'", "info")
                    self._send_json({"success": True})
                    return
            self._send_json({"error": "Invalid index"}, 400)

        elif path == "/api/download":
            url = body.get("url", "").strip()
            mode = body.get("mode", "audio")
            fmt = body.get("format", "mp3")
            out = body.get("output", "")
            start = int(body.get("start", 1) or 1)
            end_val = body.get("end")
            end = int(end_val) if end_val else None
            force = bool(body.get("force", False))

            if not url:
                self._send_json({"error": "No URL provided"}, 400)
                return
            if app_state["is_downloading"]:
                self._send_json({"error": "Download already in progress"}, 409)
                return
            threading.Thread(target=run_download_job, args=(url, mode, fmt, out, start, end, force), daemon=True).start()
            self._send_json({"success": True})

        elif path == "/api/sync-all":
            if app_state["is_downloading"]:
                self._send_json({"error": "Download already in progress"}, 409)
                return
            threading.Thread(target=run_sync_all_job, daemon=True).start()
            self._send_json({"success": True})

        elif path == "/api/stop":
            with app_state["lock"]:
                if app_state["current_process"]:
                    app_state["current_process"].terminate()
            self._send_json({"success": True})

        elif path == "/api/shutdown":
            broadcast_log("Shutting down GUI server...", "warning")
            app_state["shutdown_requested"] = True
            self._send_json({"success": True})
            threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()
        else:
            self.send_error(404, "Not Found")

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def auto_shutdown_watcher():
    """Shuts down server if no heartbeat for 12 seconds after initial grace period."""
    time.sleep(12) # Initial grace period for browser to open
    while not app_state["shutdown_requested"]:
        time.sleep(2)
        with app_state["lock"]:
            elapsed = time.time() - app_state["last_heartbeat"]
            if elapsed > 12:
                print("\n[!] Browser window closed. Shutting down GUI server cleanly (Zero background 24/7).")
                app_state["shutdown_requested"] = True
                os._exit(0)

def main():
    port = find_free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), RequestHandler)
    url = f"http://127.0.0.1:{port}"

    print("==========================================================")
    print("  YouTube Playlist Incremental Downloader - GUI")
    print("==========================================================")
    print(f"Server URL:        {url}")
    print("Behavior:          Runs on-demand. Auto-stops on window close.")
    print("==========================================================")

    def open_window():
        time.sleep(0.5)
        if sys.platform.startswith("win"):
            edge_paths = [
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            ]
            for ep in edge_paths:
                if os.path.exists(ep):
                    try:
                        subprocess.Popen([ep, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    except Exception:
                        pass
            import webbrowser
            webbrowser.open(url)
        else:
            if Path("/usr/bin/chromium").exists():
                subprocess.Popen(["/usr/bin/chromium", f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif Path("/usr/bin/google-chrome").exists():
                subprocess.Popen(["/usr/bin/google-chrome", f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import webbrowser
                webbrowser.open(url)

    threading.Thread(target=open_window, daemon=True).start()
    threading.Thread(target=auto_shutdown_watcher, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExiting cleanly...")
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
