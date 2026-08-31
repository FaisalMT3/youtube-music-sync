#!/usr/bin/env python3
"""
==============================================================================
YouTube Playlist Incremental Downloader & Sync Tool (yt_sync)
==============================================================================
Features:
- Delta / Incremental Downloads: Automatically tracks downloaded video IDs via
  archive history so subsequent runs ONLY download new / unloaded videos.
- Music & Video Modes: High-quality MP3/Opus with album artwork & metadata
  embedding for your Music player and Syncthing to phone.
- Desktop Notifications: Alerts you via notify-send when new songs are auto-fetched.
- Playlist Management: Save multiple playlist URLs to auto-sync with a single command.
- Fast, Resilient & Safe: Automatically resumes interrupted transfers.
==============================================================================
"""

import sys
import os
import json
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict

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

CONFIG_DIR = Path.home() / ".config" / "yt_sync"
PLAYLISTS_FILE = CONFIG_DIR / "playlists.json"
ARCHIVE_FILE = CONFIG_DIR / "download_archive.txt"
DEFAULT_MUSIC_DIR = get_default_music_dir()
DEFAULT_VIDEO_DIR = Path.home() / "Videos"

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

def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not PLAYLISTS_FILE.exists():
        with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)

def send_notification(title: str, message: str):
    """Sends desktop notification across Windows / Linux / macOS."""
    if sys.platform.startswith("win"):
        try:
            ps_cmd = (
                f'[reflection.assembly]::loadwithpartialname("System.Windows.Forms") | Out-Null;'
                f'$notify = new-object system.windows.forms.notifyicon;'
                f'$notify.icon = [system.drawing.systemicons]::Information;'
                f'$notify.visible = $true;'
                f'$notify.showballoontip(10, "{title}", "{message}", [system.windows.forms.tooltipicon]::Info);'
            )
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    elif Path("/usr/bin/notify-send").exists():
        try:
            subprocess.run(
                ["notify-send", "-a", "YouTube Sync", "-i", "audio-x-generic", title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

def load_saved_playlists() -> List[Dict]:
    ensure_config_dir()
    try:
        with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_playlists(playlists: List[Dict]):
    ensure_config_dir()
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(playlists, f, indent=2)

def add_playlist(name: str, url: str, mode: str = "audio", output_subfolder: str = ""):
    playlists = load_saved_playlists()
    for p in playlists:
        if p["url"] == url:
            print(f"[!] Playlist URL already tracked as '{p.get('name')}'. Updating configuration...")
            p["name"] = name
            p["mode"] = mode
            p["subfolder"] = output_subfolder
            save_playlists(playlists)
            print("[+] Updated successfully!")
            return

    playlists.append({
        "name": name,
        "url": url,
        "mode": mode,
        "subfolder": output_subfolder
    })
    save_playlists(playlists)
    print(f"[+] Added playlist: '{name}' ({url}) [Mode: {mode.upper()}]")

def list_playlists():
    playlists = load_saved_playlists()
    if not playlists:
        print("No saved playlists yet. Add one using:")
        print("  ./yt_sync.py add --name \"My Playlist\" --url \"https://...\"")
        return
    print("\n--- [ Saved Playlists for Auto-Sync ] ---")
    for idx, p in enumerate(playlists, 1):
        print(f"[{idx}] {p['name']} ({p.get('mode', 'audio').upper()})")
        print(f"    URL:       {p['url']}")
        folder = p.get('subfolder') or "(Root Music folder)"
        print(f"    Subfolder: {folder}")
    print("-------------------------------------------\n")

def remove_playlist(name_or_idx: str):
    playlists = load_saved_playlists()
    initial_count = len(playlists)
    
    if name_or_idx.isdigit():
        idx = int(name_or_idx) - 1
        if 0 <= idx < len(playlists):
            removed = playlists.pop(idx)
            save_playlists(playlists)
            print(f"[+] Removed playlist: '{removed['name']}'")
            return
        else:
            print(f"[-] Index {name_or_idx} out of range.")
            return

    playlists = [p for p in playlists if p["name"].lower() != name_or_idx.lower()]
    if len(playlists) < initial_count:
        save_playlists(playlists)
        print(f"[+] Removed playlist matching: '{name_or_idx}'")
    else:
        print(f"[-] No playlist found matching '{name_or_idx}'.")

def download_playlist(
    url: str,
    output_dir: Path,
    mode: str = "audio",
    audio_format: str = "mp3",
    archive_path: Path = ARCHIVE_FILE,
    start: int = 1,
    end: Optional[int] = None
):
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    range_desc = f"Items #{start} to #{end}" if end else (f"Starting from Item #{start}" if start > 1 else "All Items")
    print(f"\n=======================================================")
    print(f"  Starting Sync: {url}")
    print(f"  Target Folder: {output_dir}")
    print(f"  Mode:          {mode.upper()}")
    print(f"  Range:         {range_desc}")
    print(f"  Archive file:  {archive_path}")
    print(f"=======================================================")

    cmd = get_ytdlp_base_cmd() + [
        "--yes-playlist",
        "--download-archive", str(archive_path),
        "--no-post-overwrites",
        "--ignore-errors",
        "--newline",
        "--add-metadata",
        "--embed-metadata",
    ] + get_ffmpeg_args()

    if start > 1 or end:
        start_val = start if start >= 1 else 1
        if end and end >= start_val:
            cmd.extend(["--playlist-items", f"{start_val}:{end}"])
        else:
            cmd.extend(["--playlist-start", str(start_val)])

    if mode == "audio":
        cmd.extend([
            "-x",
            "--audio-format", audio_format,
            "--audio-quality", "0",
            "--embed-thumbnail",
            "-o", str(output_dir / "%(title)s.%(ext)s")
        ])
    else:
        cmd.extend([
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--embed-thumbnail",
            "-o", str(output_dir / "%(title)s [%(id)s].%(ext)s")
        ])

    cmd.append(url)

    new_downloads = 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        for line in iter(proc.stdout.readline, ''):
            clean_line = line.strip()
            if clean_line:
                if "has already been recorded in the archive" in clean_line:
                    print(f"⏩ [Already Downloaded] {clean_line}")
                elif "[download] Destination:" in clean_line:
                    print(f"📥 {clean_line}")
                    new_downloads += 1
                elif "[ExtractAudio] Destination:" in clean_line:
                    print(f"🎵 {clean_line}")
                elif "ERROR" in clean_line:
                    print(f"❌ {clean_line}")
                else:
                    print(clean_line)

        proc.stdout.close()
        proc.wait()

        if proc.returncode == 0:
            print("\n[+] Playlist sync completed successfully!")
            if new_downloads > 0:
                send_notification(
                    "YouTube Playlist Auto-Sync",
                    f"Downloaded {new_downloads} new track(s) to {output_dir.name}!"
                )
        else:
            print(f"\n[!] yt-dlp exited with return code: {proc.returncode}")
    except KeyboardInterrupt:
        print("\n[!] Sync cancelled by user.")
    except Exception as e:
        print(f"\n[-] Error running yt-dlp: {e}")

def sync_all():
    playlists = load_saved_playlists()
    if not playlists:
        print("[!] No playlists saved. Add one first with './yt_sync.py add' or pass a direct URL.")
        return

    print(f"[*] Syncing {len(playlists)} saved playlist(s)...")
    for p in playlists:
        mode = p.get("mode", "audio")
        subfolder = p.get("subfolder", "").strip()
        base_dir = DEFAULT_MUSIC_DIR if mode == "audio" else DEFAULT_VIDEO_DIR
        out_dir = base_dir / subfolder if subfolder else base_dir
        
        download_playlist(
            url=p["url"],
            output_dir=out_dir,
            mode=mode,
            archive_path=ARCHIVE_FILE
        )

def main():
    parser = argparse.ArgumentParser(
        description="Incremental YouTube Playlist Downloader (Syncs only newly added videos)."
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Command: run / download direct URL
    dl_parser = subparsers.add_parser("download", help="Download/Sync a playlist directly from a URL")
    dl_parser.add_argument("url", help="YouTube playlist URL")
    dl_parser.add_argument("--mode", choices=["audio", "video"], default="audio", help="Download audio (mp3) or video (mp4)")
    dl_parser.add_argument("--format", default="mp3", help="Audio format (mp3, m4a, opus, flac) default: mp3")
    dl_parser.add_argument("--output", "-o", default="", help="Custom output directory")
    dl_parser.add_argument("--start", "-s", type=int, default=1, help="Start from video # (e.g. 4 skips the first 3)")
    dl_parser.add_argument("--end", "-e", type=int, default=None, help="End at video #")

    # Command: add playlist to auto-sync list
    add_parser = subparsers.add_parser("add", help="Save a playlist URL for auto-syncing")
    add_parser.add_argument("--name", "-n", required=True, help="Friendly name for the playlist")
    add_parser.add_argument("--url", "-u", required=True, help="YouTube playlist URL")
    add_parser.add_argument("--mode", "-m", choices=["audio", "video"], default="audio", help="audio (mp3) or video")
    add_parser.add_argument("--subfolder", default="", help="Optional subfolder inside ~/Music or ~/Videos")

    # Command: list saved playlists
    subparsers.add_parser("list", help="List all saved playlists")

    # Command: remove playlist
    rm_parser = subparsers.add_parser("remove", help="Remove a playlist from saved auto-sync list")
    rm_parser.add_argument("name_or_index", help="Name or index number of the playlist to remove")

    # Command: sync-all
    subparsers.add_parser("sync-all", help="Sync all saved playlists (downloads only newly added songs/videos)")

    args = parser.parse_args()

    if args.command == "download":
        mode = args.mode
        if args.output:
            out_dir = Path(args.output).expanduser()
        else:
            out_dir = DEFAULT_MUSIC_DIR if mode == "audio" else DEFAULT_VIDEO_DIR
        download_playlist(args.url, out_dir, mode=mode, audio_format=args.format, start=args.start, end=args.end)
    elif args.command == "add":
        add_playlist(args.name, args.url, mode=args.mode, output_subfolder=args.subfolder)
    elif args.command == "list":
        list_playlists()
    elif args.command == "remove":
        remove_playlist(args.name_or_index)
    elif args.command == "sync-all" or args.command is None:
        if len(sys.argv) == 1:
            playlists = load_saved_playlists()
            if playlists:
                sync_all()
            else:
                parser.print_help()
        else:
            sync_all()

if __name__ == "__main__":
    main()
