# YouTube Playlist Incremental Downloader (`yt_sync` & `yt_gui`)

An on-demand, incremental YouTube playlist downloader powered by `yt-dlp` and `ffmpeg`.

---

## 🎨 Modern Desktop GUI (`yt_gui.py`)

Launch the GUI anytime from terminal or application menu:
```bash
./yt_gui.py
```

### 🌟 New Features & UI Feedback:

1. **🔔 Floating Toast Notification System:**
   - Every single button gives instant visual feedback:
     - **Success:** Green toast + checkmark.
     - **Error:** Red toast with descriptive error message.
     - **Info / Warning:** Yellow / Cyan toasts for updates.
   - All actions log automatically to the live terminal console below with timestamps.

2. **🔍 Playlist Video Inspector & Range Selector:**
   - Click the **`🔍 Check Items`** button next to your playlist URL.
   - It instantly fetches and displays all video titles with track numbers (`#1`, `#2`, `#3`...).
   - **Skip videos & start from any song:**
     - Click **`★ Start Here`** next to any video (e.g. video `#4`), and it will automatically skip videos #1–3 and download from #4 onwards!
     - Or manually type in the **Start from Video #** and **End Video #** boxes.

3. **📁 Native Folder Browser:**
   - Click **`📁 Browse...`** to pick any folder using your Linux file dialog.
   - Quick chips: `🎵 ~/phone (Syncthing)`, `🎬 ~/Videos`, `📥 ~/Downloads`.

4. **❌ Zero Background Service / No Autostart:**
   - When you close the app window or click **"Exit App"**, the server shuts down immediately.

---

## 💻 CLI Range Usage

You can also skip videos in the terminal:
```bash
# Skip first 3 videos (start downloading from video #4 to the end):
./yt_sync.py download "https://www.youtube.com/playlist?list=..." --start 4

# Download only videos #4 to #10:
./yt_sync.py download "https://www.youtube.com/playlist?list=..." --start 4 --end 10
```

---

## 🔄 Syncthing & Huawei Phone Sync

Any audio downloaded into `~/phone` is automatically detected by **Syncthing** and pushed straight to your **Huawei Note 15** over Wi-Fi!
