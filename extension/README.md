# hydownload managers — Browser Extension

This extension integrates your browser (Chrome, Brave, Arc, Edge) with your local **hydownload managers** application, intercepting large file downloads (.zip, .rar, .7z, .iso, .dmg, multi-part archives) and sending them to aria2 with 16-connection speeds.

---

## 🚀 How to Install (Takes 30 Seconds)

1. Open your browser's extension settings:
   - **Chrome**: `chrome://extensions`
   - **Brave**: `brave://extensions`
   - **Arc**: `arc://extensions`
   - **Edge**: `edge://extensions`
2. Turn **ON** the **"Developer mode"** toggle in the top-right corner.
3. Click the **"Load unpacked"** button in the top-left.
4. Select the extension directory:
   - When running from `/Applications/hydownload managers.app`:
     `/Applications/hydownload managers.app/Contents/Resources/extension`
   - Or when developing from source:
     `<path-to-project>/extension` (or the `extension` folder inside this repository)
5. Done! The extension icon will appear in your browser toolbar. When hydownload managers is running, clicking the icon will show **Connected**.

---

## How It Works

- Whenever you click a download link for a `.rar`, `.zip`, `.iso`, `.dmg`, or multi-part archive, the extension cancels the slow single-stream browser download.
- It grabs your session cookies and authentication headers.
- It automatically POSTs the download URL to `http://127.0.0.1:6801/add`.
- hydownload managers captures it and downloads with 16 parallel chunks!
