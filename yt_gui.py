#!/usr/bin/env python3
"""
==============================================================================
YouTube Playlist Incremental Downloader - GUI Edition (yt_gui)
==============================================================================
Features:
- Live "Now Downloading" widget with progress bar, speed, & stage pills
- Playlist Inspector with Synced (🟢) vs Pending (⚪) status
- Per-song "Download" / "Re-download" buttons from the inspector
- Force Re-download toggle (per-song & whole playlist)
- Toast notification system on every action
- Cross-platform folder picker (Qt6, PowerShell, zenity, kdialog)
- Saved Playlist Manager with per-playlist Sync button
- Auto-shutdown on window close (zero background process)
- Cross-platform: Linux, macOS, Windows
==============================================================================
"""

import sys
import os
import re
import json
import time
import socket
import shutil
import threading
import subprocess
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, List, Optional, Any

# ─── Config Paths ──────────────────────────────────────────────────────────────

CONFIG_DIR    = Path.home() / ".config" / "yt_sync"
PLAYLISTS_FILE = CONFIG_DIR / "playlists.json"
ARCHIVE_FILE  = CONFIG_DIR / "download_archive.txt"


def _default_music_dir() -> Path:
    if sys.platform.startswith("win"):
        p = Path.home() / "Music"
        return p if p.exists() else Path.home() / "Downloads" / "Music"
    p = Path.home() / "phone"
    return p if p.exists() else Path.home() / "Music"

DEFAULT_MUSIC_DIR     = _default_music_dir()
DEFAULT_VIDEO_DIR     = Path.home() / "Videos"
DEFAULT_DOWNLOADS_DIR = Path.home() / "Downloads"


# ─── Tool Detection ────────────────────────────────────────────────────────────

def get_ytdlp_cmd() -> List[str]:
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    if sys.platform.startswith("win"):
        local = Path(__file__).resolve().parent / "yt-dlp.exe"
        if local.exists():
            return [str(local)]
    return [sys.executable, "-m", "yt_dlp"]


def get_ffmpeg_args() -> List[str]:
    if shutil.which("ffmpeg"):
        return []
    local = Path(__file__).resolve().parent / ("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg")
    if local.exists():
        return ["--ffmpeg-location", str(local)]
    return []


# ─── App State ─────────────────────────────────────────────────────────────────

app_state: Dict[str, Any] = {
    "current_process":  None,
    "is_downloading":   False,
    "current_task_url": "",
    "active_download": {
        "title":   "",
        "percent": 0.0,
        "speed":   "",
        "eta":     "",
        "size":    "",
        "status":  "Idle",
    },
    "log_history":        [],
    "last_heartbeat":     time.time(),
    "shutdown_requested": False,
    "lock":               threading.Lock(),
}

_log_subscribers: List = []


# ─── Config Helpers ────────────────────────────────────────────────────────────

def ensure_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not PLAYLISTS_FILE.exists():
        PLAYLISTS_FILE.write_text("[]", encoding="utf-8")
    if not ARCHIVE_FILE.exists():
        ARCHIVE_FILE.touch()


def get_saved_playlists() -> List[Dict]:
    ensure_config()
    try:
        return json.loads(PLAYLISTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_playlists(data: List[Dict]):
    ensure_config()
    PLAYLISTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_archive_set() -> set:
    ensure_config()
    try:
        lines = ARCHIVE_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        return {ln.split()[-1] for ln in lines if ln.strip()}
    except Exception:
        return set()


def get_archive_count() -> int:
    return len(get_archive_set())


# ─── Logging / SSE ─────────────────────────────────────────────────────────────

def broadcast_log(text: str, log_type: str = "info"):
    entry = {"time": time.strftime("%H:%M:%S"), "text": text, "type": log_type}
    with app_state["lock"]:
        app_state["log_history"].append(entry)
        if len(app_state["log_history"]) > 800:
            app_state["log_history"].pop(0)
    for cb in list(_log_subscribers):
        try:
            cb(entry)
        except Exception:
            if cb in _log_subscribers:
                _log_subscribers.remove(cb)


# ─── Folder Picker (non-blocking wrapper) ─────────────────────────────────────

def choose_folder_dialog(initial_path: str = "") -> Optional[str]:
    """Returns selected folder or None. Runs in calling thread (POST handler thread)."""
    init_dir = os.path.expanduser(initial_path) if initial_path else str(DEFAULT_MUSIC_DIR)
    if not os.path.exists(init_dir):
        init_dir = str(Path.home())

    # 1. Qt6
    try:
        from PyQt6.QtWidgets import QApplication, QFileDialog
        app = QApplication.instance() or QApplication(sys.argv)
        chosen = QFileDialog.getExistingDirectory(None, "Select Destination Folder", init_dir)
        if chosen and os.path.exists(chosen):
            return chosen
    except Exception:
        pass

    # 2. Windows PowerShell
    if sys.platform.startswith("win"):
        try:
            ps = (
                '[System.Reflection.Assembly]::LoadWithPartialName("System.windows.forms") | Out-Null;'
                '$d = New-Object System.Windows.Forms.FolderBrowserDialog;'
                f'$d.SelectedPath = "{init_dir}";'
                '$d.Description = "Select Destination Folder";'
                'if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $d.SelectedPath }'
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass

    # 3. zenity
    if shutil.which("zenity"):
        try:
            r = subprocess.run(
                ["zenity", "--file-selection", "--directory",
                 "--title=Select Destination Folder", f"--filename={init_dir}/"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=120,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass

    # 4. kdialog
    if shutil.which("kdialog"):
        try:
            r = subprocess.run(
                ["kdialog", "--getexistingdirectory", init_dir, "--title", "Select Destination Folder"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=120,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass

    return None


# ─── Playlist Inspector ────────────────────────────────────────────────────────

def inspect_playlist(url: str) -> Dict[str, Any]:
    """Returns items list with sync status. Timeout 60s for large playlists."""
    cmd = get_ytdlp_cmd() + [
        "--flat-playlist",
        "--ignore-errors",
        "--socket-timeout", "15",
        "--print", "%(playlist_index,autonumber)s\t%(id)s\t%(title)s\t%(duration_string)s",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 and not proc.stdout.strip():
            return {"error": proc.stderr.strip() or f"yt-dlp exited {proc.returncode}"}

        archive = get_archive_set()
        items, synced = [], 0
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            try:
                idx = int(parts[0])
            except ValueError:
                idx = len(items) + 1
            vid_id   = parts[1]
            title    = parts[2]
            duration = parts[3] if len(parts) > 3 and parts[3] != "NA" else ""
            downloaded = vid_id in archive
            if downloaded:
                synced += 1
            items.append({"index": idx, "id": vid_id, "title": title,
                           "duration": duration, "is_downloaded": downloaded})

        return {
            "success":       True,
            "items":         items,
            "count":         len(items),
            "synced_count":  synced,
            "pending_count": len(items) - synced,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Timeout inspecting playlist. Try a smaller playlist or check connection."}
    except Exception as exc:
        return {"error": f"Inspection failed: {exc}"}


# ─── Progress Parsing ──────────────────────────────────────────────────────────

def _parse_progress(line: str):
    """Update app_state active_download from a yt-dlp output line."""
    ad = app_state["active_download"]
    if "[download] Destination:" in line:
        stem = Path(line.split("[download] Destination:")[-1].strip()).stem
        ad["title"]   = stem
        ad["percent"] = 0.0
        ad["status"]  = "Downloading"
    elif "[download]" in line and "%" in line:
        m = re.search(r"(\d+\.?\d*)%\s+of\s+~?(\S+)\s+at\s+(\S+)\s+ETA\s+(\S+)", line)
        if m:
            try:
                ad["percent"] = float(m.group(1))
            except ValueError:
                pass
            ad["size"]   = m.group(2)
            ad["speed"]  = m.group(3)
            ad["eta"]    = m.group(4)
            ad["status"] = "Downloading"
    elif "[ExtractAudio]" in line:
        ad["status"] = "Extracting Audio"
        ad["percent"] = 90.0
    elif "[Metadata]" in line or "[FFmpegMetadata]" in line:
        ad["status"] = "Adding Metadata"
        ad["percent"] = 95.0
    elif "[EmbedThumbnail]" in line or "[ThumbnailsConvertor]" in line:
        ad["status"] = "Embedding Cover Art"
        ad["percent"] = 98.0


# ─── Download Job ──────────────────────────────────────────────────────────────

def run_download_job(
    url: str,
    mode: str,
    audio_fmt: str,
    output_path: str,
    playlist_start: int = 1,
    playlist_end: Optional[int] = None,
    force_redownload: bool = False,
    is_single_video: bool = False,
):
    ensure_config()
    out_dir = (
        Path(output_path).expanduser()
        if output_path
        else (DEFAULT_MUSIC_DIR if mode == "audio" else DEFAULT_VIDEO_DIR)
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = get_ytdlp_cmd() + [
        "--ignore-errors",
        "--newline",
        "--add-metadata",
        "--embed-metadata",
        "--socket-timeout", "30",
    ] + get_ffmpeg_args()

    if is_single_video:
        # Single video: no playlist flags
        cmd.append("--no-playlist")
    else:
        cmd.append("--yes-playlist")
        # Range
        if playlist_start > 1 or playlist_end:
            sv = max(1, playlist_start)
            if playlist_end and playlist_end >= sv:
                cmd.extend(["--playlist-items", f"{sv}:{playlist_end}"])
            else:
                cmd.extend(["--playlist-start", str(sv)])

    if force_redownload:
        cmd.extend(["--no-download-archive", "--force-overwrites"])
        force_label = " (Force Re-download ON)"
    else:
        cmd.extend(["--download-archive", str(ARCHIVE_FILE), "--no-post-overwrites"])
        force_label = ""

    if mode == "audio":
        cmd.extend([
            "-x",
            "--audio-format", audio_fmt or "mp3",
            "--audio-quality", "0",
            "--embed-thumbnail",
            "-o", str(out_dir / "%(title)s.%(ext)s"),
        ])
    else:
        cmd.extend([
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--embed-thumbnail",
            "-o", str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        ])

    cmd.append(url)

    range_desc = "Single video" if is_single_video else (
        f"Items #{max(1, playlist_start)} to #{playlist_end}" if playlist_end
        else (f"From #{max(1, playlist_start)}" if playlist_start > 1 else "All items")
    )

    broadcast_log(f"🔗 URL: {url}", "header")
    broadcast_log(f"📁 Dest: {out_dir} | Mode: {mode.upper()} | {range_desc}{force_label}", "header")
    broadcast_log("⏳ Contacting YouTube...", "info")

    with app_state["lock"]:
        app_state["active_download"] = {
            "title": "Contacting YouTube...", "percent": 0.0,
            "speed": "--", "eta": "--", "size": "--", "status": "Connecting",
        }

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with app_state["lock"]:
            app_state["current_process"]  = proc
            app_state["is_downloading"]   = True
            app_state["current_task_url"] = url

        for raw_line in iter(proc.stdout.readline, ""):
            line = raw_line.strip()
            if not line:
                continue
            with app_state["lock"]:
                _parse_progress(line)

            if "has already been recorded in the archive" in line:
                broadcast_log(f"⏩ {line}", "skip")
            elif "[download]" in line and "%" in line:
                broadcast_log(line, "progress")
            elif "[download] Destination:" in line:
                broadcast_log(f"📥 {line}", "download")
            elif any(t in line for t in ("[ExtractAudio]", "[Metadata]", "[FFmpegMetadata]",
                                          "[EmbedThumbnail]", "[ThumbnailsConvertor]")):
                broadcast_log(f"🎵 {line}", "audio")
            elif "ERROR" in line:
                broadcast_log(f"❌ {line}", "error")
            elif "WARNING" in line:
                broadcast_log(f"⚠️ {line}", "warning")
            else:
                broadcast_log(line, "info")

        proc.stdout.close()
        proc.wait()

        if proc.returncode == 0:
            broadcast_log("✅ Download complete! All new tracks are ready.", "success")
            with app_state["lock"]:
                app_state["active_download"]["percent"] = 100.0
                app_state["active_download"]["status"]  = "Completed"
        elif proc.returncode in (-15, -9, 1):
            broadcast_log("⚠️ Download stopped by user.", "warning")
            with app_state["lock"]:
                app_state["active_download"]["status"] = "Stopped"
        else:
            broadcast_log(f"⚠️ Process exited with code {proc.returncode}", "warning")
            with app_state["lock"]:
                app_state["active_download"]["status"] = f"Exited ({proc.returncode})"

    except Exception as exc:
        broadcast_log(f"❌ Execution error: {exc}", "error")
        with app_state["lock"]:
            app_state["active_download"]["status"] = "Error"
    finally:
        with app_state["lock"]:
            app_state["current_process"]  = None
            app_state["is_downloading"]   = False
            app_state["current_task_url"] = ""


def run_sync_all_job():
    playlists = get_saved_playlists()
    if not playlists:
        broadcast_log("No saved playlists to sync. Add a playlist first!", "warning")
        return
    broadcast_log(f"🚀 Syncing {len(playlists)} playlist(s)...", "header")
    for i, p in enumerate(playlists, 1):
        broadcast_log(f"\n─── Playlist {i}/{len(playlists)}: {p['name']} ───", "header")
        mode      = p.get("mode", "audio")
        subfolder = p.get("subfolder", "").strip()
        base      = DEFAULT_MUSIC_DIR if mode == "audio" else DEFAULT_VIDEO_DIR
        out_dir   = base / subfolder if subfolder else base
        run_download_job(p["url"], mode, "mp3", str(out_dir), force_redownload=False)
    broadcast_log("\n✨ All playlists synced!", "success")


# ─── HTML / CSS / JS ───────────────────────────────────────────────────────────

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YouTube Sync &amp; Downloader</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg:          #090d16;
  --card:        rgba(18,26,44,.75);
  --card-h:      rgba(26,37,63,.9);
  --border:      rgba(255,255,255,.08);
  --focus:       rgba(99,102,241,.5);
  --primary:     #6366f1;
  --glow:        rgba(99,102,241,.35);
  --accent:      #06b6d4;
  --success:     #10b981;
  --warning:     #f59e0b;
  --danger:      #ef4444;
  --text:        #f8fafc;
  --muted:       #94a3b8;
  --radius:      16px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);background-image:radial-gradient(at 0% 0%,rgba(99,102,241,.14) 0,transparent 50%),radial-gradient(at 100% 100%,rgba(6,182,212,.12) 0,transparent 50%);color:var(--text);min-height:100vh;display:flex;flex-direction:column;overflow-x:hidden}

/* Toast */
#toastBox{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:10px;max-width:420px;pointer-events:none}
.toast{pointer-events:auto;background:rgba(15,23,42,.96);backdrop-filter:blur(16px);border:1px solid var(--border);border-radius:12px;padding:12px 16px;display:flex;align-items:center;gap:12px;box-shadow:0 10px 30px rgba(0,0,0,.5);animation:tsIn .3s cubic-bezier(.16,1,.3,1) forwards}
.toast.ts-success{border-left:4px solid var(--success)}.toast.ts-error{border-left:4px solid var(--danger)}.toast.ts-warning{border-left:4px solid var(--warning)}.toast.ts-info{border-left:4px solid var(--accent)}
.t-icon{font-size:1.2rem}.t-body{flex:1}.t-title{font-size:.85rem;font-weight:700}.t-desc{font-size:.78rem;color:var(--muted);margin-top:2px}
.t-close{background:none;border:none;color:var(--muted);cursor:pointer;padding:4px;font-size:1rem}
.t-close:hover{color:var(--text)}
@keyframes tsIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}
@keyframes tsOut{from{transform:translateX(0);opacity:1}to{transform:translateX(100%);opacity:0}}

/* Header */
header{padding:1.15rem 2rem;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);background:rgba(9,13,22,.85);backdrop-filter:blur(12px);position:sticky;top:0;z-index:100}
.logo{display:flex;align-items:center;gap:12px}
.logo-icon{width:40px;height:40px;background:linear-gradient(135deg,#ef4444,#6366f1);border-radius:12px;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(239,68,68,.35)}
.logo-icon svg{width:22px;height:22px;fill:#fff}
.logo-text h1{font-size:1.2rem;font-weight:800;letter-spacing:-.5px}
.logo-text span{font-size:.72rem;color:var(--accent);font-weight:700;text-transform:uppercase;letter-spacing:1px}
.hdr-right{display:flex;align-items:center;gap:12px}
.badge-arc{padding:6px 14px;border-radius:30px;background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.3);color:var(--success);font-size:.8rem;font-weight:600;display:flex;align-items:center;gap:6px}
.btn-exit{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.3);color:#fca5a5;padding:8px 14px;border-radius:10px;cursor:pointer;font-weight:600;font-size:.85rem;transition:all .2s;display:flex;align-items:center;gap:6px}
.btn-exit:hover{background:var(--danger);color:#fff}

/* Layout */
main{flex:1;padding:1.75rem 2rem;max-width:1320px;width:100%;margin:0 auto;display:grid;grid-template-columns:1fr 1fr;gap:1.75rem}
@media(max-width:960px){main{grid-template-columns:1fr}}

/* Card */
.card{background:var(--card);backdrop-filter:blur(16px);border:1px solid var(--border);border-radius:var(--radius);padding:1.5rem;box-shadow:0 8px 32px rgba(0,0,0,.35);display:flex;flex-direction:column}
.card-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:1.25rem}
.card-title{font-size:1.1rem;font-weight:700;display:flex;align-items:center;gap:10px}

/* Forms */
.fg{margin-bottom:1.15rem}
label{display:block;font-size:.8rem;font-weight:600;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
input[type=text],input[type=number],select{width:100%;padding:11px 14px;background:rgba(10,15,29,.85);border:1px solid var(--border);border-radius:12px;color:var(--text);font-family:inherit;font-size:.92rem;transition:all .2s;outline:none}
input[type=text]:focus,input[type=number]:focus,select:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--glow)}
select option{background:#0f172a}
.row{display:flex;gap:10px}.row .fg{flex:1}

/* Buttons */
.btn{padding:11px 18px;border-radius:12px;font-weight:700;font-size:.92rem;font-family:inherit;cursor:pointer;border:none;display:inline-flex;align-items:center;justify-content:center;gap:8px;transition:all .2s}
.btn-primary{background:linear-gradient(135deg,var(--primary),#4f46e5);color:#fff;box-shadow:0 4px 20px var(--glow)}
.btn-primary:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 6px 24px var(--glow)}
.btn-primary:disabled{opacity:.6;cursor:not-allowed;transform:none}
.btn-secondary{background:rgba(255,255,255,.06);color:var(--text);border:1px solid var(--border)}
.btn-secondary:hover:not(:disabled){background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.2)}
.btn-secondary:disabled{opacity:.5;cursor:not-allowed}
.btn-danger{background:rgba(239,68,68,.15);color:#fca5a5;border:1px solid rgba(239,68,68,.3)}
.btn-danger:hover:not(:disabled){background:var(--danger);color:#fff}
.btn-sm{padding:8px 14px;font-size:.82rem;border-radius:10px}

/* Chips */
.chip{background:rgba(255,255,255,.05);border:1px solid var(--border);color:var(--muted);font-size:.75rem;font-weight:600;padding:4px 10px;border-radius:20px;cursor:pointer;transition:all .2s;font-family:inherit;white-space:nowrap}
.chip:hover{background:rgba(99,102,241,.2);color:#c7d2fe;border-color:var(--primary)}

/* Checkbox */
.chk-label{display:flex;align-items:center;gap:8px;cursor:pointer;font-size:.82rem;color:#cbd5e1;text-transform:none;letter-spacing:normal;margin-bottom:0}
.chk-label input[type=checkbox]{width:16px;height:16px;accent-color:var(--warning);cursor:pointer}

/* Inspector panel */
.inspector{background:rgba(10,15,29,.85);border:1px solid rgba(99,102,241,.35);border-radius:12px;padding:14px 16px;margin-bottom:1.15rem;display:none;flex-direction:column;gap:10px;animation:fade .25s ease}
@keyframes fade{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
.ins-hdr{display:flex;justify-content:space-between;align-items:center}
.ins-stats{display:flex;gap:8px;font-size:.78rem;font-weight:700}
.bs{padding:3px 8px;border-radius:12px;border:1px solid;display:inline-flex;align-items:center;gap:3px;font-size:.7rem;font-weight:700}
.bs-syn{background:rgba(16,185,129,.15);color:var(--success);border-color:rgba(16,185,129,.3)}
.bs-pend{background:rgba(6,182,212,.15);color:var(--accent);border-color:rgba(6,182,212,.3)}
.ins-list{max-height:230px;overflow-y:auto;display:flex;flex-direction:column;gap:6px;padding-right:4px}
.ins-row{background:rgba(255,255,255,.03);border:1px solid var(--border);border-radius:8px;padding:8px 10px;display:flex;align-items:center;justify-content:space-between;font-size:.8rem;gap:8px;transition:all .15s}
.ins-row:hover{background:rgba(99,102,241,.12);border-color:rgba(99,102,241,.4)}
.ins-row.syn{border-left:3px solid var(--success)}.ins-row.pend{border-left:3px solid var(--accent)}
.pb-syn{background:rgba(16,185,129,.15);color:var(--success)}.pb-pend{background:rgba(6,182,212,.15);color:var(--accent)}
.pb-dl{background:rgba(99,102,241,.2);border:1px solid var(--primary);color:#c7d2fe;padding:3px 8px;border-radius:6px;font-size:.72rem;cursor:pointer;font-weight:600;transition:all .15s;white-space:nowrap}
.pb-dl:hover{background:var(--primary);color:#fff}
.pb-redl{background:rgba(245,158,11,.15);border:1px solid rgba(245,158,11,.4);color:#fde68a;padding:3px 8px;border-radius:6px;font-size:.72rem;cursor:pointer;font-weight:600;transition:all .15s;white-space:nowrap}
.pb-redl:hover{background:var(--warning);color:#000}

/* Now-downloading widget */
.nd-card{background:rgba(15,23,42,.9);border:1px solid rgba(99,102,241,.35);border-radius:12px;padding:12px 16px;margin-bottom:1rem;display:none;flex-direction:column;gap:8px}
.nd-hdr{display:flex;justify-content:space-between;align-items:center}
.nd-title{font-weight:700;font-size:.88rem;color:#c7d2fe;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:75%}
.nd-pill{padding:3px 8px;border-radius:12px;font-size:.72rem;font-weight:700;background:rgba(99,102,241,.25);color:#a5b4fc;border:1px solid rgba(99,102,241,.4)}
.pb-bg{height:8px;width:100%;background:rgba(255,255,255,.08);border-radius:6px;overflow:hidden}
.pb-fill{height:100%;width:0%;background:linear-gradient(90deg,#6366f1,#06b6d4,#10b981);border-radius:6px;transition:width .3s ease}
.nd-meta{display:flex;justify-content:space-between;font-size:.75rem;color:var(--muted);font-family:'JetBrains Mono',monospace}

/* Saved playlists */
.pl-list{display:flex;flex-direction:column;gap:10px;max-height:260px;overflow-y:auto;padding-right:4px}
.pl-item{background:rgba(10,15,29,.65);border:1px solid var(--border);border-radius:12px;padding:12px 14px;display:flex;justify-content:space-between;align-items:center;transition:all .2s}
.pl-item:hover{background:var(--card-h);border-color:rgba(99,102,241,.3)}
.pl-info h4{font-size:.92rem;font-weight:700}.pl-info p{font-size:.76rem;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
.pl-btns{display:flex;gap:8px;flex-shrink:0}

/* Console */
.con-card{grid-column:1/-1;min-height:300px}
.con-box{background:#050811;border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:1rem;font-family:'JetBrains Mono',monospace;font-size:.84rem;height:260px;overflow-y:auto;display:flex;flex-direction:column;gap:4px}
.ll{line-height:1.4;word-break:break-all}
.lt{color:#64748b;margin-right:8px;font-size:.76rem}
.lh{color:#818cf8;font-weight:600}.lsk{color:#38bdf8}.lpr{color:#fbbf24}.ldl{color:#34d399;font-weight:600}.lau{color:#a78bfa}.lsu{color:#4ade80;font-weight:700}.ler{color:#f87171;font-weight:700}.lw{color:#fb923c}.li{color:#cbd5e1}

/* Spinner */
.sp{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-radius:50%;border-top-color:#fff;animation:spin .8s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}

/* Info banner */
.info-banner{background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.2);border-radius:12px;padding:10px 14px;font-size:.82rem;color:#c7d2fe;margin-top:.75rem;display:flex;align-items:center;gap:10px}
</style>
</head>
<body>

<div id="toastBox"></div>

<header>
  <div class="logo">
    <div class="logo-icon">
      <svg viewBox="0 0 24 24"><path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816-.029-6.185-.484-8.549-4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z"/></svg>
    </div>
    <div class="logo-text">
      <h1>YouTube Sync</h1>
      <span>Incremental Downloader</span>
    </div>
  </div>
  <div class="hdr-right">
    <div class="badge-arc">
      <span>●</span> <span id="arcCount">0</span> items archived
    </div>
    <button class="btn-exit" onclick="shutdownApp()" id="btnExit">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10"/></svg>
      Exit App
    </button>
  </div>
</header>

<main>
  <!-- ── Card 1: Direct Download ── -->
  <div class="card">
    <div class="card-hdr">
      <div class="card-title">⚡ Direct Download</div>
    </div>

    <div class="fg">
      <label>YouTube Playlist or Video URL</label>
      <div style="display:flex;gap:8px">
        <input type="text" id="dlUrl" placeholder="https://www.youtube.com/playlist?list=..." />
        <button type="button" class="btn btn-secondary btn-sm" onclick="inspectPlaylist()" id="btnInspect" style="white-space:nowrap">
          🔍 Check Items
        </button>
      </div>
    </div>

    <!-- Inspector panel -->
    <div class="inspector" id="inspectorPanel">
      <div class="ins-hdr">
        <div class="ins-stats">
          <span class="bs bs-syn" id="stSynced">🟢 0 Synced</span>
          <span class="bs bs-pend" id="stPending">⚪ 0 Pending</span>
        </div>
        <button type="button" class="chip" onclick="closeInspector()">✕ Hide</button>
      </div>
      <div class="ins-list" id="insList"></div>
      <div style="display:flex;align-items:center;gap:6px;font-size:.8rem;color:var(--muted)">
        <span>Quick range:</span>
        <button type="button" class="chip" onclick="setRange(1,null)">All from #1</button>
        <button type="button" class="chip" onclick="setRange(2,null)">Skip #1</button>
        <button type="button" class="chip" onclick="setRange(4,null)">Skip first 3</button>
      </div>
    </div>

    <div class="row">
      <div class="fg">
        <label>Start from # (skip earlier)</label>
        <input type="number" id="dlStart" min="1" value="1" />
      </div>
      <div class="fg">
        <label>End # (optional)</label>
        <input type="number" id="dlEnd" min="1" placeholder="End" />
      </div>
    </div>

    <div class="row">
      <div class="fg">
        <label>Mode</label>
        <select id="dlMode" onchange="onModeChange()">
          <option value="audio">🎵 Audio (MP3 for music)</option>
          <option value="video">🎬 Video (MP4 best quality)</option>
        </select>
      </div>
      <div class="fg" id="fmtGroup">
        <label>Audio Format</label>
        <select id="dlFormat">
          <option value="mp3">MP3 (Best compatibility)</option>
          <option value="m4a">M4A / AAC</option>
          <option value="opus">Opus</option>
          <option value="flac">FLAC (lossless)</option>
        </select>
      </div>
    </div>

    <div class="fg">
      <label>Destination Folder</label>
      <div style="display:flex;gap:8px">
        <input type="text" id="dlOutput" value="MUSIC_DIR_PLACEHOLDER" style="flex:1" />
        <button type="button" class="btn btn-secondary btn-sm" onclick="browseFolder('dlOutput')" id="btnBrowse" style="white-space:nowrap">
          📁 Browse...
        </button>
      </div>
      <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
        <button type="button" class="chip" onclick="setFolder('dlOutput','MUSIC_DIR_PLACEHOLDER')">🎵 Music (phone)</button>
        <button type="button" class="chip" onclick="setFolder('dlOutput','VIDEO_DIR_PLACEHOLDER')">🎬 Videos</button>
        <button type="button" class="chip" onclick="setFolder('dlOutput','DL_DIR_PLACEHOLDER')">📥 Downloads</button>
      </div>
    </div>

    <div class="fg" style="margin-bottom:.85rem">
      <label class="chk-label">
        <input type="checkbox" id="dlForce" />
        <span>⚡ <strong>Download anyway</strong> — force re-download even if already synced</span>
      </label>
    </div>

    <div style="display:flex;gap:10px;margin-top:.5rem">
      <button type="button" class="btn btn-primary" id="btnDownload" onclick="startDownload()" style="flex:1">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
        <span id="btnDlText">Download / Sync Incremental</span>
      </button>
      <button type="button" class="btn btn-danger" id="btnStop" onclick="stopDownload()" style="display:none">
        ⏹ Stop
      </button>
    </div>

    <div class="info-banner">
      <span>💡</span>
      <span>Only <strong>new</strong> tracks are downloaded by default. Use <em>Download anyway</em> to force re-fetch existing tracks.</span>
    </div>
  </div>

  <!-- ── Card 2: Saved Playlists ── -->
  <div class="card">
    <div class="card-hdr">
      <div class="card-title">📑 Saved Auto-Sync Playlists</div>
      <button class="btn btn-primary btn-sm" onclick="syncAll()" id="btnSyncAll">🔄 Sync All</button>
    </div>

    <div class="pl-list" id="plContainer">
      <p style="color:var(--muted);font-size:.88rem;text-align:center;padding:2rem">Loading...</p>
    </div>

    <div style="margin-top:1.25rem;padding-top:1rem;border-top:1px solid var(--border)">
      <label style="font-size:.9rem;font-weight:700;color:var(--text);text-transform:none;letter-spacing:normal;margin-bottom:10px;display:block">➕ Add Playlist to Track</label>
      <div class="row" style="margin-bottom:8px">
        <input type="text" id="newName" placeholder="Playlist name (e.g. Chill Beats)" style="flex:1" />
        <select id="newMode" style="width:110px">
          <option value="audio">Audio</option>
          <option value="video">Video</option>
        </select>
      </div>
      <div style="margin-bottom:8px">
        <input type="text" id="newUrl" placeholder="Playlist URL (https://...)" />
      </div>
      <div class="row">
        <input type="text" id="newFolder" placeholder="Subfolder (optional, e.g. Chill)" style="flex:1" />
        <button class="btn btn-secondary btn-sm" onclick="addPlaylist()" id="btnAdd">+ Add</button>
      </div>
    </div>
  </div>

  <!-- ── Card 3: Console ── -->
  <div class="card con-card">
    <!-- Active download widget -->
    <div class="nd-card" id="ndCard">
      <div class="nd-hdr">
        <div class="nd-title" id="ndTitle">🎵 Preparing...</div>
        <div class="nd-pill" id="ndStage">Starting</div>
      </div>
      <div class="pb-bg"><div class="pb-fill" id="ndBar"></div></div>
      <div class="nd-meta">
        <span id="ndPct">0%</span>
        <span id="ndSize">--</span>
        <span id="ndSpeed">--</span>
        <span id="ndEta">ETA --</span>
      </div>
    </div>

    <div class="card-hdr" style="margin-bottom:.75rem">
      <div class="card-title">🖥️ Live Download Stream</div>
      <button class="btn btn-secondary btn-sm" onclick="clearLog()">Clear</button>
    </div>
    <div class="con-box" id="conBox">
      <div class="ll li"><span class="lt">[System]</span> Ready. Paste a URL and click Download.</div>
    </div>
  </div>
</main>

<script>
"use strict";

let isDownloading  = false;
let inspectedItems = [];

// ── Helpers ──────────────────────────────────────────────────────────────────

function esc(t) {
  if (!t) return '';
  return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function toast(msg, type='info', title='') {
  const c = document.getElementById('toastBox');
  const el = document.createElement('div');
  el.className = `toast ts-${type}`;
  const icons = {success:'✅',error:'❌',warning:'⚠️',info:'ℹ️'};
  const titles = {success:title||'Success',error:title||'Error',warning:title||'Warning',info:title||'Notice'};
  el.innerHTML = `<div class="t-icon">${icons[type]||'ℹ️'}</div>
    <div class="t-body"><div class="t-title">${esc(titles[type])}</div><div class="t-desc">${esc(msg)}</div></div>
    <button class="t-close" onclick="this.parentElement.remove()">✕</button>`;
  c.appendChild(el);
  setTimeout(() => { el.style.animation='tsOut .3s ease forwards'; setTimeout(()=>el.remove(),300); }, 4500);
}

function spin(btn, label) {
  btn.disabled = true;
  btn._orig = btn._orig || btn.innerHTML;
  btn.innerHTML = `<span class="sp"></span> ${label}`;
}
function unspin(btn, label) {
  btn.disabled = false;
  btn.innerHTML = label || btn._orig || '';
}

// ── Mode / folder helpers ────────────────────────────────────────────────────

function onModeChange() {
  const mode = document.getElementById('dlMode').value;
  document.getElementById('fmtGroup').style.display = mode === 'audio' ? '' : 'none';
  const out = document.getElementById('dlOutput');
  if (mode === 'video' && out.value === 'MUSIC_DIR_PLACEHOLDER') out.value = 'VIDEO_DIR_PLACEHOLDER';
  if (mode === 'audio' && out.value === 'VIDEO_DIR_PLACEHOLDER') out.value = 'MUSIC_DIR_PLACEHOLDER';
}

function setFolder(id, path) {
  document.getElementById(id).value = path;
  toast('Destination: ' + path, 'info', 'Folder Set');
}

async function browseFolder(inputId) {
  const btn = document.getElementById('btnBrowse');
  const cur = document.getElementById(inputId).value.trim();
  spin(btn, 'Browsing...');
  try {
    const r = await fetch('/api/browse-folder', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({current: cur})
    });
    const d = await r.json();
    if (d.folder) {
      document.getElementById(inputId).value = d.folder;
      toast('Selected: ' + d.folder, 'success', 'Folder Chosen');
    } else {
      toast('Folder selection cancelled', 'info');
    }
  } catch(e) {
    toast('Folder picker error: ' + e, 'error');
  } finally {
    unspin(btn, '📁 Browse...');
  }
}

function setRange(s, e) {
  document.getElementById('dlStart').value = s;
  document.getElementById('dlEnd').value = e || '';
  toast(e ? `Range: #${s} – #${e}` : `From #${s} to end`, 'info', 'Range Set');
  if (inspectedItems.length) renderInspector(inspectedItems);
}

// ── Inspector ────────────────────────────────────────────────────────────────

async function inspectPlaylist() {
  const url = document.getElementById('dlUrl').value.trim();
  if (!url) { toast('Enter a YouTube URL first', 'warning'); return; }
  const btn = document.getElementById('btnInspect');
  spin(btn, 'Checking...');
  try {
    const r = await fetch('/api/playlist/inspect', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url})
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error', 'Inspection Failed'); return; }
    inspectedItems = d.items || [];
    renderInspector(inspectedItems, d.synced_count, d.pending_count);
    toast(`${d.count} videos found — ${d.synced_count} synced, ${d.pending_count} pending`, 'success', 'Playlist Checked');
  } catch(e) {
    toast('Error: ' + e, 'error');
  } finally {
    unspin(btn, '🔍 Check Items');
  }
}

function renderInspector(items, synced, pending) {
  const syn  = synced  !== undefined ? synced  : items.filter(i=>i.is_downloaded).length;
  const pend = pending !== undefined ? pending : items.length - syn;
  document.getElementById('stSynced').textContent = `🟢 ${syn} Synced`;
  document.getElementById('stPending').textContent = `⚪ ${pend} Pending`;

  const curStart = parseInt(document.getElementById('dlStart').value) || 1;

  document.getElementById('insList').innerHTML = items.map(it => `
    <div class="ins-row ${it.is_downloaded ? 'syn' : 'pend'}" id="irow_${it.index}">
      <div style="display:flex;align-items:center;gap:8px;flex:1;overflow:hidden">
        <span style="font-weight:700;color:var(--accent);min-width:28px">#${it.index}</span>
        <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(it.title)}">${esc(it.title)}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
        ${it.duration ? `<span style="color:var(--muted);font-size:.75rem">${esc(it.duration)}</span>` : ''}
        ${it.is_downloaded
          ? `<span class="bs bs-syn">✓ Synced</span>
             <button class="pb-redl" onclick="dlSingle('${esc(it.id)}','${esc(it.title)}',true)">🔄 Re-dl</button>`
          : `<span class="bs bs-pend">Pending</span>
             <button class="pb-dl" onclick="dlSingle('${esc(it.id)}','${esc(it.title)}',false)">⬇️ Download</button>`
        }
        <button class="pb-dl" onclick="setStartItem(${it.index})" style="${it.index===curStart?'background:var(--primary);color:#fff':''}">
          ${it.index === curStart ? '★ Start' : `#${it.index}→`}
        </button>
      </div>
    </div>`).join('');

  document.getElementById('inspectorPanel').style.display = 'flex';
}

function closeInspector() {
  document.getElementById('inspectorPanel').style.display = 'none';
}

function setStartItem(idx) {
  document.getElementById('dlStart').value = idx;
  toast(`Start set to video #${idx}`, 'info', 'Start Updated');
  if (inspectedItems.length) renderInspector(inspectedItems);
}

async function dlSingle(vid, title, force) {
  if (isDownloading) { toast('A download is already running. Stop it first.', 'warning'); return; }
  const url    = `https://www.youtube.com/watch?v=${vid}`;
  const mode   = document.getElementById('dlMode').value;
  const format = document.getElementById('dlFormat').value;
  const output = document.getElementById('dlOutput').value.trim();
  toast(`${force ? 'Re-downloading' : 'Downloading'}: ${title.slice(0,40)}...`, 'info', force?'Force Re-download':'Download');
  try {
    const r = await fetch('/api/download', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, mode, format, output, start:1, end:null, force, single:true})
    });
    const d = await r.json();
    if (d.error) toast(d.error, 'error');
  } catch(e) { toast('Error: '+e, 'error'); }
  fetchStatus();
}

// ── Download ─────────────────────────────────────────────────────────────────

async function startDownload() {
  const url    = document.getElementById('dlUrl').value.trim();
  const mode   = document.getElementById('dlMode').value;
  const format = document.getElementById('dlFormat').value;
  const output = document.getElementById('dlOutput').value.trim();
  const start  = parseInt(document.getElementById('dlStart').value) || 1;
  const endRaw = parseInt(document.getElementById('dlEnd').value);
  const end    = isNaN(endRaw) ? null : endRaw;
  const force  = document.getElementById('dlForce').checked;

  if (!url) { toast('Please enter a YouTube URL', 'warning'); return; }

  const btn  = document.getElementById('btnDownload');
  const btnT = document.getElementById('btnDlText');
  spin(btn, 'Starting...');

  try {
    const r = await fetch('/api/download', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, mode, format, output, start, end, force, single:false})
    });
    const d = await r.json();
    if (d.error) {
      toast(d.error, 'error', 'Download Error');
    } else {
      toast(force ? 'Force re-download started!' : 'Incremental sync started!', 'success', 'Download Started');
    }
  } catch(e) {
    toast('Failed to start: '+e, 'error');
  } finally {
    unspin(btn, '');
    btnT.textContent = 'Download / Sync Incremental';
    fetchStatus();
  }
}

async function stopDownload() {
  const btn = document.getElementById('btnStop');
  spin(btn, 'Stopping...');
  try {
    await fetch('/api/stop', {method:'POST'});
    toast('Download stopped', 'warning', 'Stopped');
  } catch(e) {
    toast('Stop error: '+e, 'error');
  } finally {
    unspin(btn, '⏹ Stop');
    fetchStatus();
  }
}

// ── Status polling ───────────────────────────────────────────────────────────

async function fetchStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    document.getElementById('arcCount').textContent = d.archive_count;
    isDownloading = d.is_downloading;

    const btn  = document.getElementById('btnDownload');
    const btnT = document.getElementById('btnDlText');
    const stop = document.getElementById('btnStop');
    const nd   = document.getElementById('ndCard');

    if (d.is_downloading) {
      btn.disabled = true;
      btnT.innerHTML = '<span class="sp"></span> Downloading...';
      stop.style.display = 'inline-flex';
      nd.style.display = 'flex';
      const ad = d.active_download || {};
      if (ad.title) document.getElementById('ndTitle').textContent = '🎵 ' + ad.title;
      document.getElementById('ndStage').textContent = ad.status || 'Downloading';
      const pct = (ad.percent || 0).toFixed(1);
      document.getElementById('ndBar').style.width   = pct + '%';
      document.getElementById('ndPct').textContent   = pct + '%';
      document.getElementById('ndSize').textContent  = ad.size  || '--';
      document.getElementById('ndSpeed').textContent = ad.speed || '--';
      document.getElementById('ndEta').textContent   = ad.eta ? 'ETA ' + ad.eta : '--';
    } else {
      btn.disabled = false;
      btnT.textContent = 'Download / Sync Incremental';
      stop.style.display = 'none';
      const st = (d.active_download || {}).status || '';
      if (!st || st === 'Idle' || st === 'Completed' || st === 'Stopped') {
        nd.style.display = 'none';
      }
    }
  } catch(_) {}
}

// ── Saved playlists ──────────────────────────────────────────────────────────

async function loadPlaylists() {
  try {
    const r = await fetch('/api/playlists');
    const list = await r.json();
    const c = document.getElementById('plContainer');
    if (!list.length) {
      c.innerHTML = '<p style="color:var(--muted);font-size:.88rem;text-align:center;padding:2rem">No saved playlists yet. Add one below!</p>';
      return;
    }
    c.innerHTML = list.map((p, i) => `
      <div class="pl-item" id="plitem_${i}">
        <div class="pl-info">
          <h4>${esc(p.name)} <span style="font-size:.75rem;color:var(--accent);font-weight:normal">[${(p.mode||'audio').toUpperCase()}]</span></h4>
          <p title="${esc(p.url)}">${esc(p.url.length>50 ? p.url.slice(0,50)+'…' : p.url)}</p>
        </div>
        <div class="pl-btns">
          <button class="btn btn-secondary btn-sm" id="bsync_${i}" onclick="syncOne(${i})">▶ Sync</button>
          <button class="btn btn-danger btn-sm"    id="bdel_${i}"  onclick="removePlaylist(${i},'${esc(p.name)}')">✕</button>
        </div>
      </div>`).join('');
    window._playlists = list;
  } catch(e) { console.error(e); }
}

async function addPlaylist() {
  const name   = document.getElementById('newName').value.trim();
  const url    = document.getElementById('newUrl').value.trim();
  const mode   = document.getElementById('newMode').value;
  const folder = document.getElementById('newFolder').value.trim();
  if (!name || !url) { toast('Enter both name and URL', 'warning', 'Missing Fields'); return; }
  const btn = document.getElementById('btnAdd');
  spin(btn, 'Adding...');
  try {
    const r = await fetch('/api/playlists/add', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name, url, mode, subfolder: folder})
    });
    const d = await r.json();
    if (d.success) {
      toast(`Added: ${name}`, 'success', 'Playlist Saved');
      document.getElementById('newName').value   = '';
      document.getElementById('newUrl').value    = '';
      document.getElementById('newFolder').value = '';
      loadPlaylists();
    } else {
      toast(d.error || 'Failed', 'error');
    }
  } catch(e) { toast('Error: '+e,'error'); }
  finally { unspin(btn, '+ Add'); }
}

async function removePlaylist(idx, name) {
  if (!confirm(`Remove "${name}" from auto-sync?`)) return;
  const btn = document.getElementById(`bdel_${idx}`);
  if (btn) spin(btn, '');
  try {
    const r = await fetch('/api/playlists/remove', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({index: idx})
    });
    const d = await r.json();
    if (d.success) { toast(`Removed: ${name}`, 'info', 'Removed'); loadPlaylists(); }
    else toast(d.error || 'Failed', 'error');
  } catch(e) { toast('Error: '+e,'error'); }
}

async function syncOne(idx) {
  const playlists = window._playlists || [];
  const p = playlists[idx];
  if (!p) { toast('Playlist not found', 'error'); return; }
  if (isDownloading) { toast('A download is already running', 'warning'); return; }
  const btn = document.getElementById(`bsync_${idx}`);
  if (btn) spin(btn, '');
  try {
    const r = await fetch('/api/download', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        url: p.url,
        mode: p.mode || 'audio',
        format: 'mp3',
        output: p.subfolder ? '' : '',   // server resolves with subfolder from body
        subfolder: p.subfolder || '',
        start: 1, end: null, force: false, single: false
      })
    });
    const d = await r.json();
    if (d.error) toast(d.error, 'error');
    else toast(`Sync started: ${p.name}`, 'success', 'Syncing');
  } catch(e) { toast('Error: '+e,'error'); }
  finally {
    if (btn) unspin(btn, '▶ Sync');
    fetchStatus();
  }
}

async function syncAll() {
  if (isDownloading) { toast('A download is already running. Stop it first.', 'warning'); return; }
  const btn = document.getElementById('btnSyncAll');
  spin(btn, 'Syncing...');
  try {
    const r = await fetch('/api/sync-all', {method:'POST'});
    const d = await r.json();
    if (d.error) toast(d.error, 'error');
    else toast('Auto-sync started for all playlists!', 'success', 'Syncing All');
  } catch(e) { toast('Error: '+e,'error'); }
  finally { unspin(btn,'🔄 Sync All'); fetchStatus(); }
}

// ── Shutdown ─────────────────────────────────────────────────────────────────

async function shutdownApp() {
  if (!confirm('Shutdown YouTube Sync? (Closes background server)')) return;
  const btn = document.getElementById('btnExit');
  spin(btn, 'Exiting...');
  toast('Shutting down server...', 'info', 'Shutting Down');
  try { await fetch('/api/shutdown', {method:'POST'}); } catch(_) {}
  setTimeout(() => {
    window.close();
    document.body.innerHTML = '<div style="display:flex;height:100vh;align-items:center;justify-content:center;color:white;font-family:sans-serif;background:#090d16"><h1>✅ App closed. You may close this tab.</h1></div>';
  }, 500);
}

function clearLog() {
  document.getElementById('conBox').innerHTML = '<div class="ll li"><span class="lt">[System]</span> Log cleared.</div>';
  toast('Log cleared', 'info');
}

// ── SSE log stream ───────────────────────────────────────────────────────────

function startSSE() {
  const es = new EventSource('/api/logs/stream');
  es.onmessage = e => {
    try {
      const entry = JSON.parse(e.data);
      const box = document.getElementById('conBox');
      const d   = document.createElement('div');
      const cls = {header:'lh',skip:'lsk',progress:'lpr',download:'ldl',audio:'lau',success:'lsu',error:'ler',warning:'lw',info:'li'}[entry.type] || 'li';
      d.className = `ll ${cls}`;
      d.innerHTML = `<span class="lt">[${entry.time}]</span> ${esc(entry.text)}`;
      box.appendChild(d);
      box.scrollTop = box.scrollHeight;
    } catch(_) {}
    fetchStatus();
  };
  es.onerror = () => setTimeout(startSSE, 3000);
}

// ── Heartbeat + status polling ───────────────────────────────────────────────

setInterval(() => fetch('/api/heartbeat', {method:'POST'}).catch(()=>{}), 3000);
setInterval(fetchStatus, 1500);

// ── Init ─────────────────────────────────────────────────────────────────────
fetchStatus();
loadPlaylists();
startSSE();
</script>
</body>
</html>
"""

# Replace placeholders at import time
_HTML = (
    _HTML
    .replace("MUSIC_DIR_PLACEHOLDER", str(DEFAULT_MUSIC_DIR))
    .replace("VIDEO_DIR_PLACEHOLDER", str(DEFAULT_VIDEO_DIR))
    .replace("DL_DIR_PLACEHOLDER",    str(DEFAULT_DOWNLOADS_DIR))
)


# ─── HTTP Request Handler ──────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence default logs

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode())
        except Exception:
            return {}

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/":
            body = _HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type",   "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/status":
            with app_state["lock"]:
                self._json({
                    "is_downloading": app_state["is_downloading"],
                    "archive_count":  get_archive_count(),
                    "task_url":       app_state["current_task_url"],
                    "active_download": dict(app_state["active_download"]),
                })

        elif path == "/api/playlists":
            self._json(get_saved_playlists())

        elif path == "/api/logs/stream":
            self.send_response(200)
            self.send_header("Content-Type",  "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection",    "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q, lock = [], threading.Lock()

            def cb(entry):
                with lock:
                    q.append(entry)

            _log_subscribers.append(cb)

            # Replay recent history
            with app_state["lock"]:
                history = list(app_state["log_history"][-40:])
            for h in history:
                try:
                    self.wfile.write(("data: " + json.dumps(h) + "\n\n").encode())
                except Exception:
                    break
            try:
                self.wfile.flush()
            except Exception:
                pass

            try:
                while not app_state["shutdown_requested"]:
                    with lock:
                        batch = list(q); q.clear()
                    for item in batch:
                        self.wfile.write(("data: " + json.dumps(item) + "\n\n").encode())
                    if batch:
                        self.wfile.flush()
                    time.sleep(0.1)
            except Exception:
                pass
            finally:
                if cb in _log_subscribers:
                    _log_subscribers.remove(cb)

        else:
            self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._read_body()

        # ── Heartbeat ──
        if path == "/api/heartbeat":
            with app_state["lock"]:
                app_state["last_heartbeat"] = time.time()
            self._json({"ok": True})

        # ── Browse folder (runs in this thread – POST thread, not main) ──
        elif path == "/api/browse-folder":
            folder = choose_folder_dialog(body.get("current", ""))
            if folder:
                self._json({"folder": folder})
            else:
                self._json({"cancelled": True})

        # ── Inspect playlist ──
        elif path == "/api/playlist/inspect":
            url = body.get("url", "").strip()
            if not url:
                self._json({"error": "URL required"}, 400); return
            self._json(inspect_playlist(url))

        # ── Add playlist ──
        elif path == "/api/playlists/add":
            name  = body.get("name",      "").strip()
            url   = body.get("url",       "").strip()
            mode  = body.get("mode",      "audio")
            sub   = body.get("subfolder", "").strip()
            if not name or not url:
                self._json({"error": "name and url required"}, 400); return
            pls = get_saved_playlists()
            pls.append({"name": name, "url": url, "mode": mode, "subfolder": sub})
            save_playlists(pls)
            broadcast_log(f"✅ Saved playlist: '{name}'", "success")
            self._json({"success": True})

        # ── Remove playlist ──
        elif path == "/api/playlists/remove":
            idx = body.get("index")
            if not isinstance(idx, int):
                self._json({"error": "invalid index"}, 400); return
            pls = get_saved_playlists()
            if 0 <= idx < len(pls):
                removed = pls.pop(idx)
                save_playlists(pls)
                broadcast_log(f"ℹ️ Removed playlist: '{removed['name']}'", "info")
                self._json({"success": True})
            else:
                self._json({"error": "index out of range"}, 400)

        # ── Download ──
        elif path == "/api/download":
            url    = body.get("url",    "").strip()
            mode   = body.get("mode",   "audio")
            fmt    = body.get("format", "mp3")
            out    = body.get("output", "").strip()
            start  = int(body.get("start", 1) or 1)
            end_r  = body.get("end")
            end    = int(end_r) if end_r else None
            force  = bool(body.get("force", False))
            single = bool(body.get("single", False))
            sub    = body.get("subfolder", "").strip()

            if not url:
                self._json({"error": "URL required"}, 400); return
            if app_state["is_downloading"]:
                self._json({"error": "A download is already running. Stop it first."}, 409); return

            # Resolve output path (subfolder for saved-playlist sync)
            if not out and sub:
                base = DEFAULT_MUSIC_DIR if mode == "audio" else DEFAULT_VIDEO_DIR
                out  = str(base / sub)

            threading.Thread(
                target=run_download_job,
                args=(url, mode, fmt, out, start, end, force, single),
                daemon=True,
            ).start()
            self._json({"success": True})

        # ── Sync all ──
        elif path == "/api/sync-all":
            if app_state["is_downloading"]:
                self._json({"error": "A download is already running."}, 409); return
            threading.Thread(target=run_sync_all_job, daemon=True).start()
            self._json({"success": True})

        # ── Stop ──
        elif path == "/api/stop":
            with app_state["lock"]:
                proc = app_state["current_process"]
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            self._json({"success": True})

        # ── Shutdown ──
        elif path == "/api/shutdown":
            broadcast_log("Shutting down...", "warning")
            app_state["shutdown_requested"] = True
            self._json({"success": True})
            threading.Thread(target=lambda: (time.sleep(0.6), os._exit(0)), daemon=True).start()

        else:
            self.send_error(404)


# ─── Auto-shutdown watcher ─────────────────────────────────────────────────────

def _shutdown_watcher():
    """Kill server if browser heartbeat is absent for 20 s after 20 s grace."""
    time.sleep(20)  # grace: browser may take time to launch
    while not app_state["shutdown_requested"]:
        time.sleep(3)
        with app_state["lock"]:
            elapsed = time.time() - app_state["last_heartbeat"]
        if elapsed > 20:
            print("\n[yt-sync] Browser window closed – shutting down.")
            os._exit(0)


# ─── Browser launcher ─────────────────────────────────────────────────────────

def _open_browser(url: str):
    time.sleep(0.8)

    if sys.platform.startswith("win"):
        for exe in [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ]:
            if os.path.isfile(exe):
                try:
                    subprocess.Popen([exe, f"--app={url}"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except Exception:
                    pass
        import webbrowser
        webbrowser.open(url)
        return

    # Linux / macOS – try app-mode browser in preference order
    browsers = [
        shutil.which("opera-gx"),
        shutil.which("opera"),
        "/usr/bin/chromium",
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        shutil.which("brave"),
    ]
    for b in browsers:
        if b and os.path.isfile(b):
            try:
                subprocess.Popen([b, f"--app={url}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except Exception:
                pass

    import webbrowser
    webbrowser.open(url)


# ─── Entry Point ───────────────────────────────────────────────────────────────

def main():
    port = 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv_url = f"http://127.0.0.1:{port}"

    print("=" * 58)
    print("  YouTube Playlist Incremental Downloader – GUI")
    print("=" * 58)
    print(f"  URL: {srv_url}")
    print("  Auto-closes when browser window is shut.")
    print("=" * 58)

    threading.Thread(target=_open_browser, args=(srv_url,), daemon=True).start()
    threading.Thread(target=_shutdown_watcher, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[yt-sync] Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
