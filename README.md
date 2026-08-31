# 🎵 YouTube Music Sync & Incremental Downloader

[![Version](https://img.shields.io/badge/version-v1.0-blue.svg)](https://github.com/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-brightgreen.svg)](https://github.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A modern, high-speed, incremental YouTube playlist downloader and local music synchronizer. Automatically tracks downloaded video IDs so subsequent runs **only download newly added songs**, embedding high-res album art and ID3 metadata for car audio, offline players, and mobile phone sync.

---

## ✨ Features

- **⚡ Incremental / Delta Sync**: Never re-downloads songs you already have.
- **🎨 Modern Web/App GUI**: Clean dark-mode interface with live download progress %, transfer speed, ETA, and stage indicators.
- **🔍 Playlist Video Inspector**: Preview all tracks in a playlist with `🟢 Synced` vs `⚪ Pending` status badges before downloading.
- **🔢 Track Range Selector**: Skip existing items or start downloading from any specific track number (e.g. download item #4 to the end).
- **🎛️ "Download Anyway" Toggle**: Force re-download individual songs or entire playlists.
- **🖼️ High-Quality Audio & Metadata**: Automatically embeds high-res thumbnail covers, artist, and title tags into MP3/Opus files.
- **🪟 100% Cross-Platform**: 1-click Windows setup wizard & launchers included (`install_windows.bat`, `run_gui.bat`).
- **📱 Phone & Car Audio Ready**: Ideal for syncing offline music to Android / Huawei / iOS and playing in your car.

---

## 🚀 Quick Start (Windows 10 / 11)

1. **Download the latest Release (`v1.0`)** from the Releases tab.
2. Extract the folder.
3. Double-click **`install_windows.bat`** (installs Python packages, FFmpeg, and creates a desktop shortcut).
4. Double-click **`run_gui.bat`** or the desktop icon **"YouTube Music Sync"** to launch!

---

## 🐧 Quick Start (Arch Linux / Ubuntu / Fedora)

1. Clone the repository:
   ```bash
   git clone https://github.com/<your-username>/youtube-music-sync.git
   cd youtube-music-sync
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   # Ensure ffmpeg and yt-dlp are installed:
   # Arch: sudo pacman -S ffmpeg yt-dlp
   # Ubuntu: sudo apt install ffmpeg
   ```

3. Launch GUI:
   ```bash
   python yt_gui.py
   ```

---

## 💻 CLI Usage

You can also run headless synchronization via terminal:

```bash
# Sync all saved playlists
python yt_sync.py sync-all

# Download a playlist directly
python yt_sync.py download "https://www.youtube.com/playlist?list=..."

# Add a playlist for automatic recurring sync
python yt_sync.py add "My Playlist" "https://www.youtube.com/playlist?list=..."
```

---

## 📄 License

MIT License © 2026
