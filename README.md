# HyDownload Manager

A cross-platform desktop download manager for **macOS and Windows** that combines high-concurrency multi-connection downloading with BitTorrent support, automated split archive processing, and browser download interception.

[![macOS Release](https://img.shields.io/github/v/release/shikharfordiscord/hybrid-download-manager?label=Download%20for%20macOS&color=007AFF)](https://github.com/shikharfordiscord/hybrid-download-manager/releases/latest)
[![Windows Release](https://img.shields.io/github/v/release/shikharfordiscord/hybrid-download-manager?label=Download%20for%20Windows&color=0078D4)](https://github.com/shikharfordiscord/hybrid-download-manager/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ⬇️ Downloads

> No Python installation required — just download, extract, and run.

| Platform | Download | Requirements |
|---|---|---|
| **macOS** (Apple Silicon & Intel) | [Latest macOS Release →](https://github.com/shikharfordiscord/hybrid-download-manager/releases/latest) | macOS 12 (Monterey)+ |
| **Windows** (10 / 11, 64-bit) | [Latest Windows Release →](https://github.com/shikharfordiscord/hybrid-download-manager/releases/latest) | Windows 10 (64-bit)+ |

### macOS Quick Start
1. Download `HyDownload-Manager-macOS.zip` from the latest release.
2. Extract the ZIP — you'll get `hydownload managers.app`.
3. Drag it to `/Applications`.
4. **First launch only:** right-click the app → **Open** (to bypass Gatekeeper).

### Windows Quick Start
1. Download `HyDownload-Manager-Windows.zip` from the latest release.
2. Extract the ZIP anywhere (e.g., `C:\Users\<You>\Apps\hydownload managers\`).
3. Run `hydownload managers.exe`. No installation needed.

---

## Architecture Overview

```
+-------------------------------------------------------------+
|                Web Browser (Chrome / Arc)                   |
|  - Extension (Manifest V3) catches matching file downloads  |
+------------------------------+------------------------------+
                               |
                               | HTTP POST :6801/add
                               v
+-------------------------------------------------------------+
|           HyDownload Manager Application (PyQt6)             |
|                                                             |
|  +-------------------------+   +-------------------------+  |
|  | Local HTTP Interceptor  |   | Multi-Part Archive Mgr  |  |
|  | (127.0.0.1:6801)        |   | (Queue, Hash, Extract)  |  |
|  +------------+------------+   +------------+------------+  |
|               |                             |               |
|               +--------------+--------------+               |
|                              v                              |
|               +-----------------------------+               |
|               | Download Controller         |               |
|               | (aria2p RPC interface)      |               |
|               +--------------+--------------+               |
+------------------------------|------------------------------+
                               |
                               | JSON-RPC (localhost)
                               v
+-------------------------------------------------------------+
|                     aria2c Daemon                           |
|  - 16 split connections per host                            |
|  - BitTorrent engine with DHT and public peer discovery     |
+-------------------------------------------------------------+
```

---

## Key Capabilities

- **Accelerated Transfers**: Splits downloads across up to 16 concurrent connections per server.
- **BitTorrent & Magnet Transfers**: Built-in support for magnet links and `.torrent` files with DHT routing and active peer discovery.
- **Download Analytics & Speed Telemetry**: Live duration countdown and completion time tracking with accurate average throughput and peak speed monitoring, visible directly in the queue table, desktop completion notifications, and an inspector dialog (double-click or right-click any item).
- **Split Archive Automation**: Parses multi-part archives (`.part01.rar` ... `.partN.rar`, `.001` ... `.030`), queues them sequentially or concurrently, verifies hashes (`SHA-256`, `MD5`), and auto-extracts contents via `7z` / `7zz`.
- **Browser Catcher**: Local background service captures incoming transfers directly from Chromium-based browsers (Chrome, Brave, Arc, Edge) with authenticated session cookies.
- **Cross-Platform System Integration**: Sleep prevention during active transfers (caffeinate on macOS, SetThreadExecutionState on Windows), menu bar / system tray icon, and native OS notification alerts.

---

## Prerequisites

### macOS
- macOS 12.0 or newer
- Python 3.10+
- Homebrew packages:
  ```bash
  brew install aria2 sevenzip
  ```

### Windows
- Windows 10 or 11 (64-bit)
- Python 3.10+
- Chocolatey packages:
  ```powershell
  choco install aria2 7zip -y
  ```
  Or install [aria2](https://github.com/aria2/aria2/releases) and [7-Zip](https://www.7-zip.org/) manually.

---

## Setup & Running from Source

### macOS

1. **Clone the repository and enter directory:**
   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch the application:**
   ```bash
   python main_ui.py
   ```

### Windows

1. **Clone the repository:**
   ```powershell
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. **Install Python dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Launch the application:**
   ```powershell
   python main_ui.py
   ```

---

## Building Application Bundles

### macOS (.app bundle)

A packaging script compiles a standalone `.app` bundle with internal binaries:

```bash
chmod +x bundle_app.sh
./bundle_app.sh
```

Output: `dist/hydownload managers.app`

To install system-wide:
```bash
cp -R "dist/hydownload managers.app" /Applications/
```

### Windows (standalone folder)

A PowerShell packaging script creates a portable distribution:

```powershell
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```

Output: `dist/hydownload managers/` (run `hydownload managers.exe` inside)

### Automated Releases

Pushing a version tag triggers GitHub Actions to build both platforms automatically:

```bash
git tag v3.0.0
git push origin v3.0.0
```

The CI workflow (`.github/workflows/release.yml`) builds on `macos-latest` and `windows-latest` in parallel, then publishes a GitHub Release with both ZIPs attached.

---

## Browser Extension Setup

1. Open `chrome://extensions` or `arc://extensions` in your browser.
2. Enable **Developer mode** via the top-right toggle.
3. Click **Load unpacked**.
4. Select the `extension/` directory from this repository (or from `/Applications/hydownload managers.app/Contents/Resources/extension` when installed).

---

## Repository Structure

```
├── main_ui.py               # Main PyQt6 interface and event loop
├── engine_manager.py        # aria2c process lifecycle and configuration
├── download_controller.py   # High-level download queue and telemetry
├── repack_manager.py        # Split archive batch clustering and extraction
├── browser_interceptor.py   # Local HTTP listener on port 6801
├── platform_service.py      # Cross-platform OS integration (sleep, notifications, file reveal)
├── bundle_app.sh            # Build script for macOS .app bundle
├── build_windows.ps1        # Build script for Windows standalone distribution
├── requirements.txt         # Python package dependencies
├── .gitignore               # Git ignore rules
├── .github/
│   └── workflows/
│       └── release.yml      # Dual-platform GitHub Actions CI/CD release workflow
├── extension/               # Manifest V3 browser companion extension
│   ├── manifest.json
│   ├── background.js
│   ├── popup.html
│   └── popup.js
└── LICENSE                  # Open-source license
```

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for details.
