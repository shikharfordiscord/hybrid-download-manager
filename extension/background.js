// background.js — Intercepts downloads and forwards to hydownload managers (127.0.0.1:6801)

const DEFAULT_SERVER_URL = "http://127.0.0.1:6801/add";
const DEFAULT_EXTENSIONS = [
  "zip", "rar", "7z", "tar", "gz", "bz2", "xz",
  "iso", "dmg", "pkg", "exe", "msi", "bin",
  "part", "001", "002", "003", "r00", "r01"
];

// Initialize settings
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(["enabled", "extensions", "minSizeMB"], (res) => {
    if (res.enabled === undefined) {
      chrome.storage.local.set({
        enabled: true,
        extensions: DEFAULT_EXTENSIONS,
        minSizeMB: 10 // Files smaller than 10MB can download normally unless matched
      });
    }
  });
});

// Intercept downloads via onDeterminingFilename
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  chrome.storage.local.get(["enabled", "extensions", "minSizeMB"], async (res) => {
    const isEnabled = res.enabled !== false;
    if (!isEnabled) {
      return suggest(); // proceed with standard browser download
    }

    const filename = item.filename || "";
    const ext = filename.split(".").pop().toLowerCase();
    const extensions = res.extensions || DEFAULT_EXTENSIONS;
    const isMatchedExt = extensions.includes(ext);

    // Also check mime types / multi-part names
    const isMultipart = /\.part\d+\.rar$/i.test(filename) || /\.\d{3}$/.test(filename);

    if (!isMatchedExt && !isMultipart) {
      return suggest();
    }

    console.log(`[hydownload managers] Intercepting download: ${filename} (${item.url})`);

    // Fetch relevant cookies for the download domain to pass through authentication
    let cookieHeader = "";
    try {
      const cookies = await chrome.cookies.getAll({ url: item.url });
      cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join("; ");
    } catch (e) {
      console.warn("Could not retrieve cookies for URL:", e);
    }

    // Forward download to local listener
    try {
      const response = await fetch(DEFAULT_SERVER_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          url: item.finalUrl || item.url,
          filename: filename,
          referrer: item.referrer || "",
          fileSize: item.fileSize || 0,
          cookies: cookieHeader
        })
      });

      if (response.ok) {
        // Cancel the browser download so it is handled exclusively by hydownload managers
        chrome.downloads.cancel(item.id, () => {
          console.log(`[hydownload managers] Successfully forwarded to desktop app and cancelled browser download.`);
        });
        return;
      } else {
        console.warn(`[hydownload managers] Desktop app returned error ${response.status}. Allowing browser to download.`);
        return suggest();
      }
    } catch (err) {
      console.warn(`[hydownload managers] Desktop app not reachable at ${DEFAULT_SERVER_URL}. Falling back to browser download.`, err);
      return suggest();
    }
  });

  return true; // async suggestion
});
