# 🪟 Windows Setup & Sharing Guide for YouTube Music Sync

This guide explains how to run **YouTube Music Sync** on Windows 10/11 or share it with your friends.

---

## ⚡ Quick 1-Click Setup (For You & Friends)

### Step 1: Download or Extract the Folder
Send the `youtube_sync` folder (as a `.zip`) to your friend. Have them extract it anywhere (e.g. `Downloads` or `Desktop`).

### Step 2: Run the Installer
1. Inside the folder, **double-click** `install_windows.bat`.
2. The setup wizard will automatically:
   - Check/install Python (if missing).
   - Install required packages (`yt-dlp`, `mutagen`, `PyQt6`).
   - Check/install `ffmpeg` for high-quality MP3 conversion & album art.
   - Create a desktop shortcut named **"YouTube Music Sync"**.

### Step 3: Launch & Enjoy!
- Double-click the **"YouTube Music Sync"** icon on the desktop (or double-click `run_gui.bat`).
- The modern desktop GUI opens in native app window mode!

---

## 🎯 Key Features on Windows

- **Native App Mode**: Opens cleanly via Microsoft Edge / Chrome in standalone window mode (no browser clutter).
- **Incremental Sync**: Only downloads newly added songs, skipping existing ones automatically.
- **Playlist Video Inspector**: Preview all tracks in a playlist, check which are already synced vs pending, and choose which item # to start from.
- **Embedded Album Art & Tags**: Automatically embeds high-res thumbnail covers, artist, and title metadata for Windows Media Player, iTunes, Groove Music, and phone sync.
- **Custom Folder Selector**: Click `📁 Folder...` to save music to `~/Music`, `~/Downloads`, an external USB drive, or a phone folder!

---

## 🛠️ Manual Installation (If Needed)

If you prefer installing manually via PowerShell / Command Prompt:

```cmd
# 1. Install Python packages
python -m pip install -r requirements.txt

# 2. Install FFmpeg (via winget)
winget install Gyan.FFmpeg

# 3. Launch GUI
python yt_gui.py
```

---

## 📦 Creating a Standalone Windows Executable (.exe)

If you want to give friends a **single standalone `.exe`** without them needing Python at all:

```cmd
pip install pyinstaller
pyinstaller --noconfirm --onedir --windowed --name "YouTubeSync" yt_gui.py
```
*(The compiled `.exe` will be generated in the `dist/YouTubeSync` folder).*
