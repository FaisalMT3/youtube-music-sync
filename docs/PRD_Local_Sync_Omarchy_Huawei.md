# Product Requirements Document (PRD)
## Project: Automated Local Network File & Music Synchronization (Omarchy ↔ Huawei Note)

**Document Version:** 1.0.0  
**Author:** AI Agent & heaven  
**Target Platform:** Arch Linux ("omarchy") & Huawei Note (Android / HarmonyOS / EMUI)  
**Status:** Draft / Ready for Review  

---

## 1. Executive Summary & Objective

The goal is to establish a robust, automated, zero-touch synchronization pipeline between a host computer running Arch Linux (**"omarchy"**) and a mobile device (**"Huawei Note"**). 

Whenever the mobile device joins the home/local network, it should automatically detect the PC (or vice-versa), compute changes in the target directory (e.g., `~/Music`), and sync only the incremental changes (e.g., if 10 tracks exist and an 11th track is added, only the 11th track is transferred). When the phone is disconnected or outside the local network, the system should gracefully pause, queue changes, and avoid unnecessary network errors or high battery drain.

---

## 2. Problem Statement & User Pain Points

- **Manual Transfer Friction:** Manually connecting USB cables or using cloud intermediaries (Google Drive, Telegram, etc.) is slow, consumes external bandwidth, and creates duplicate file management overhead.
- **Whole-Directory Overwrite vs. Delta Sync:** Re-uploading the entire music/file library wastes Wi-Fi bandwidth and mobile storage I/O. Only delta (new, updated, or deleted) files should be synced.
- **Offline / Transient Connectivity:** Mobile devices constantly enter and leave local Wi-Fi coverage. The sync mechanism must handle network drops, sleeping devices, and roaming without corrupting files or hanging processes.
- **Huawei Background Process Management:** Huawei devices (HarmonyOS / EMUI) aggressively kill background processes. The solution must provide a resilient connection/wake mechanism.

---

## 3. User Stories & Use Cases

### User Story 1: Incremental Sync on Local Network Entry
> *As a user, when I arrive home and my Huawei phone connects to my Wi-Fi, I want the music folder on my PC to automatically sync newly added tracks to my phone's local storage without touching any buttons.*

### User Story 2: Offline Resilience & Queued Updates
> *As a user, if I add new songs to my PC while my phone is away, I want the system to wait patiently and sync them the moment my phone re-enters the network.*

### User Story 3: Smart Differential Synchronization
> *As a user, if 10 songs are already synced and I add an 11th song on my PC, I only want that single 11th song transferred, not the entire 10 songs re-downloaded.*

### User Story 4: Two-Way or Master-Replica Sync Flexibility
> *As a user, I want the option to choose whether the PC is the source of truth (Master → Replica) or if additions/deletions on the phone also reflect back to the PC (Two-Way).*

---

## 4. Technical Architecture Comparison & Options Analysis

| Criteria | Option A: Syncthing (Recommended) | Option B: Custom Rsync over SSH / Termux | Option C: KDE Connect / GSConnect Scripting | Option D: Local WebDAV / Nextcloud |
| :--- | :--- | :--- | :--- | :--- |
| **Architecture** | P2P Continuous Daemon (mDNS / Broadcast) | Client-driven shell script / cron / ping | D-Bus & Local Notification daemon | Client-Server HTTP/WebDAV |
| **Sync Type** | Block-level & File-level Delta | File-level / Rsync Delta | File pushes / manual transfer | Full / Delta depending on client |
| **Network Discovery** | Automatic Local Discovery (Zero-conf) | Hardcoded IP / ARP scan / ping triggers | Automatic Pairing via UDP | Static IP / Local DNS |
| **Huawei Battery Impact** | Minimal (can run on Wi-Fi + AC only) | Low (wakes only when triggered) | Low | Medium |
| **Handling Mobile Sleeping** | Native Android/HarmonyOS app with WakeLocks | Requires Termux:Boot or Tasker | App background service | Requires dedicated sync client |
| **Complexity to Setup** | Low (5-10 minutes) | High (SSH keys, scripts, Termux, Tasker) | Medium (KDE Connect CLI scripts) | High (WebDAV server, reverse proxy) |

### Recommended Solution: **Option A (Syncthing / Syncthing-Fork)**
- **Why?** 
  1. Built specifically for continuous, decentralized incremental synchronization.
  2. Features **Local Discovery**: devices discover each other over LAN via broadcast packets without needing static IP addresses.
  3. Uses the **Block Exchange Protocol (BEP)**: breaks files into chunks so only modified/new chunks are transferred.
  4. Fully open-source, encrypted end-to-end (TLS), and operates 100% locally without cloud servers.
  5. The Android client (`Syncthing-Fork` on F-Droid / GitHub) has specific optimizations for Huawei / aggressive battery managers (run on specific Wi-Fi SSIDs, run when charging, wake locks).

---

## 5. System Workflow & Data Flow

```mermaid
sequenceDiagram
    autonumber
    participant PC as Arch Linux ("omarchy")
    participant LAN as Local Wi-Fi Network
    participant Phone as Huawei Note (Syncthing/Sync Client)

    Note over PC,Phone: Phone leaves network (Outside / 4G / Offline)
    PC->>PC: User adds "song_11.mp3" to ~/Music
    PC->>PC: Local index updated; marked as pending sync
    
    Note over Phone,LAN: Phone arrives home & connects to Wi-Fi
    Phone->>LAN: Broadcast presence / Local mDNS discovery
    LAN->>PC: Discovery Packet received
    
    PC->>Phone: Mutual TLS Handshake & Auth
    Phone->>PC: Compare folder index metadata
    Note over Phone: Detected 1 new file ("song_11.mp3")
    
    PC->>Phone: Stream block chunks of "song_11.mp3"
    Phone->>Phone: Save & index "song_11.mp3" in /Music
    Phone->>PC: Sync Complete Acknowledgment
    Note over PC,Phone: Idle state (Listening for next event)
```

---

## 6. Functional Requirements

### 6.1. Device Discovery & Connection
- **FR-1.1 Local Discovery:** Devices must discover each other automatically over LAN using mDNS / UDP broadcast without hardcoded dynamic DHCP leases.
- **FR-1.2 Offline State Management:** While disconnected, the PC and phone must remain in an idle listening state with exponential backoff to avoid CPU/network spinning.
- **FR-1.3 Reconnection Trigger:** Sync must initiate within 30 seconds of the phone connecting to the configured Wi-Fi network.

### 6.2. Synchronization & Delta Efficiency
- **FR-2.1 Differential Sync:** The system must inspect file modification times, hashes, or block chunks so that only new or changed files are transferred.
- **FR-2.2 Folder Mapping:** 
  - Host Path: e.g., `/home/<user>/Music`
  - Target Path: e.g., `/storage/emulated/0/Music` (or SD card path)
- **FR-2.3 Folder Types:**
  - *Send Only (Master)*: Changes on PC push to Phone; Phone changes are overwritten or ignored.
  - *Send & Receive (Bi-directional)*: Changes on either device propagate to the other.
  - *Receive Only*: Phone receives files from PC without modifying PC storage.
- **FR-2.4 File Ignoring:** Support `.stignore` or regex patterns (e.g., ignore `.tmp`, `.DS_Store`, hidden metadata).

### 6.3. Energy & Resource Constraints (Mobile & PC)
- **FR-3.1 Battery Saver Policies:** Allow configuring sync rules on the phone:
  - Sync only on unmetered Wi-Fi (SSID whitelist).
  - Optional: Sync only when connected to a charger.
- **FR-3.2 Low Overhead on Linux:** Host daemon on Arch Linux must run as a lightweight `systemd --user` service consuming < 50MB RAM when idle.

---

## 7. Non-Functional Requirements

- **Security & Privacy:** Transfers must be encrypted over TLS (self-signed certs or device fingerprints). No data sent to third-party cloud servers.
- **Idempotency & Data Integrity:** Hash-verified transfers (SHA-256) to ensure media files (audio/video) are not corrupted during partial disconnects.
- **Compatibility:** Compatible with Arch Linux kernel / userland and Huawei EMUI / HarmonyOS (Android 10+ storage access framework / Scoped Storage).

---

## 8. Proposed Implementation Plan & Next Steps

1. **Host Setup (Omarchy / Arch Linux):**
   - Install Syncthing daemon: `sudo pacman -S syncthing`
   - Enable systemd user service: `systemctl --user enable --now syncthing.service`
   - Open necessary firewall ports (if `ufw` or `firewalld` is active: TCP 22000, UDP 22000, UDP 21027).

2. **Mobile Setup (Huawei Note):**
   - Install **Syncthing-Fork** (Recommended over standard Syncthing for EMUI/HarmonyOS background reliability from F-Droid or GitHub APK).
   - Configure Storage Permissions (Allow management of all files for the Music directory).
   - Configure Battery Optimization exceptions (Disable "Power-intensive prompt" and set "App Launch" to Manual / Run in Background).

3. **Pairing & Folder Configuration:**
   - Scan device QR code to pair Arch PC and Huawei Note.
   - Configure `Music` folder to "Send Only" on PC and "Receive Only" (or "Send & Receive") on Phone.
   - Set condition: "Run only on home Wi-Fi SSID".

4. **Alternative Scripted Solution (If you prefer a purely custom script without apps):**
   - We can build a custom `rsync` + `ping/nmap` daemon running on Arch Linux with a simple systemd timer, or an ADB-over-Wi-Fi automated sync script.
