# HyDownload Manager for macOS

A desktop download manager designed for macOS that combines high-concurrency multi-connection downloading with BitTorrent support, automated split archive processing, and browser download interception.

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
- **Split Archive Automation**: Parses multi-part archives (`.part01.rar` ... `.partN.rar`, `.001` ... `.030`), queues them sequentially or concurrently, verifies hashes (`SHA-256`, `MD5`), and auto-extracts contents via `7zz`.
- **Browser Catcher**: Local background service captures incoming transfers directly from Chromium-based browsers (Chrome, Brave, Arc, Edge) with authenticated session cookies.
- **macOS System Integration**: Dynamic sleep prevention using `caffeinate` during active transfers, menu bar status item, and system notification alerts.

---

## Prerequisites

- macOS 12.0 or newer
- Python 3.10+
- Homebrew packages:
  ```bash
  brew install aria2 sevenzip
  ```

---

## Setup & Running from Source

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

---

## Building the macOS Application Bundle

A packaging script is provided to compile a standalone `.app` bundle with internal binaries:

```bash
chmod +x bundle_app.sh
./bundle_app.sh
```

The resulting bundle is written to:
```
dist/hydownload managers.app
```

To install it system-wide:
```bash
cp -R "dist/hydownload managers.app" /Applications/
```

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
├── macos_service.py         # macOS caffeinate power and notification helper
├── bundle_app.sh            # Build script for macOS .app bundle
├── requirements.txt         # Python package dependencies
├── .gitignore               # Git ignore rules
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
