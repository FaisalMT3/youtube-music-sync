# Huawei Note 15: Step-by-Step Setup & Battery Configuration Guide

This guide ensures your **Huawei Note 15** (EMUI / HarmonyOS) automatically syncs with your Arch Linux PC (**omarchy**) over local Wi-Fi without being killed by Huawei's aggressive background battery manager.

---

## Step 1: Install Syncthing-Fork on Huawei Note

We recommend **Syncthing-Fork** over standard Syncthing on Huawei devices because it includes specialized background keep-alive and network condition management.

- **Option 1 (F-Droid):** Search for `Syncthing-Fork` on F-Droid and install.
- **Option 2 (GitHub Direct APK):** Download the latest release APK from [Catfriend1/syncthing-android](https://github.com/Catfriend1/syncthing-android/releases).

---

## Step 2: Huawei EMUI / HarmonyOS Battery & Permission Setup (Crucial!)

Huawei devices aggressively close background apps. Follow these exact settings:

### 1. Disable Huawei App Auto-Management
1. Open **Settings** on your phone.
2. Go to **Apps** → **App Launch** (or **Battery** → **App Launch**).
3. Find **Syncthing-Fork** in the list.
4. Toggle it from **Manage automatically** to **Manage manually**.
5. Ensure all three options are enabled:
   - ✅ **Auto-launch**
   - ✅ **Secondary launch**
   - ✅ **Run in background**

### 2. Ignore Battery Optimizations
1. In **Settings**, go to **Apps** → **Apps** → (three dots menu) → **Special access** → **Battery optimization**.
2. Change the dropdown filter from *Not allowed* to *All apps*.
3. Select **Syncthing-Fork** and choose **Don't allow** (this prevents Huawei from throttling the sync service).

### 3. Grant Storage Permissions
1. When opening Syncthing-Fork for the first time, grant **All files management** (or Storage permission) so it can write directly to your phone's `/storage/emulated/0/Music` folder.

---

## Step 3: Configure Wi-Fi Run Conditions (Zero-Touch Sync)

1. Open **Syncthing-Fork** → Tap the **Settings** gear icon.
2. Under **Run Conditions**:
   - **Sync on Wi-Fi only:** Enable.
   - **Wi-Fi SSIDs:** Select your home Wi-Fi network.
   - *(Optional)* **Charging only:** Disable if you want it to sync immediately when you walk through the door, or Enable if you only want it to sync when plugged in.

---

## Step 4: Pair with Arch Linux ("omarchy")

1. On your Arch Linux machine, run:
   ```bash
   ./setup_syncthing.sh
   ```
   Or open [http://127.0.0.1:8384](http://127.0.0.1:8384) in your browser.
2. In the Web GUI, click **Actions** (top right) → **Show ID**. You will see a QR code.
3. On your Huawei phone in Syncthing-Fork:
   - Go to the **Devices** tab.
   - Tap the **`+`** icon.
   - Tap the **QR Code scanner** and scan the QR code displayed on your PC screen.
   - Set Name to `omarchy` and tap **Save**.
4. Within a few seconds, a prompt will appear on your PC Web GUI asking to accept the connection from your Huawei phone. Click **Add Device**.

---

## Step 5: Share the Music Folder

1. In the Arch Linux Web GUI ([http://127.0.0.1:8384](http://127.0.0.1:8384)):
   - Click **Add Folder** (or edit existing `Music` folder).
   - Folder Label: `Music`
   - Folder Path: `/home/heaven/Music`
   - Under the **Sharing** tab: Check your **Huawei Note**.
   - Under the **Advanced** tab: Set Folder Type to **Send Only** (PC pushes music to phone).
   - Click **Save**.
2. On your Huawei phone, a notification will pop up: *"omarchy wants to share folder Music"*.
   - Tap it, set the destination path to your phone's `Music` folder (e.g. `/storage/emulated/0/Music`).
   - Set Folder Type to **Receive Only** (or Send & Receive if you download songs on your phone too).
   - Tap **Save**.

---

## Step 6: Test the 11th Song Differential Sync!

1. On your PC, check existing songs or create a new test track:
   ```bash
   ./sync_manager.sh add-track
   ```
2. Look at your Huawei phone: within seconds, it will detect the change, transfer the single new file over local Wi-Fi, and finish!
3. Disconnect Wi-Fi on your phone, run `./sync_manager.sh add-track` again on your PC. When you reconnect Wi-Fi, the new track will immediately sync automatically.
