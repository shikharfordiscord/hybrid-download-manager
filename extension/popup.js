// popup.js — Connection check and preference toggle

document.addEventListener("DOMContentLoaded", async () => {
  const badge = document.getElementById("status-badge");
  const toggle = document.getElementById("toggle-enabled");

  // Load saved toggle state
  chrome.storage.local.get(["enabled"], (res) => {
    toggle.checked = res.enabled !== false;
  });

  toggle.addEventListener("change", () => {
    chrome.storage.local.set({ enabled: toggle.checked });
  });

  // Ping desktop app
  try {
    const res = await fetch("http://127.0.0.1:6801/ping");
    if (res.ok) {
      badge.textContent = "Connected";
      badge.className = "badge online";
    } else {
      badge.textContent = "Offline";
      badge.className = "badge offline";
    }
  } catch (e) {
    badge.textContent = "Offline";
    badge.className = "badge offline";
  }
});
