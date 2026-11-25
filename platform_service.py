"""
platform_service.py — Cross-Platform OS Integration & Power Management
=======================================================================
Replaces macos_service.py with a unified abstraction that works on both
macOS and Windows 10/11 without breaking any existing macOS behaviour.

Provides:
  1. Sleep prevention (caffeinate on macOS, SetThreadExecutionState on Windows)
  2. Desktop notifications (osascript on macOS, QSystemTrayIcon on Windows)
  3. File-manager reveal (Finder on macOS, Explorer on Windows)
  4. Resource-path resolution (app bundle on macOS, _MEIPASS/exe-dir on Windows)
  5. UI helpers: monospace font, Qt application style
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Platform flags ───────────────────────────────────────────────────────
IS_WINDOWS: bool = sys.platform == "win32"
IS_MACOS:   bool = sys.platform == "darwin"


# ════════════════════════════════════════════════════════════════════════
#  Resource Path Resolution
# ════════════════════════════════════════════════════════════════════════

def get_resource_path(relative_path: str) -> Path:
    """
    Resolves bundled resource paths reliably across all execution contexts:

    macOS (frozen .app bundle):
        Contents/Resources/<relative>  or  Contents/Frameworks/<relative>
    Windows (PyInstaller frozen .exe):
        sys._MEIPASS/<relative>
    Development (source checkout):
        <script directory>/<relative>
    """
    # 1. PyInstaller frozen environment (_MEIPASS works on BOTH macOS and Windows)
    if hasattr(sys, "_MEIPASS"):
        meipass_p = Path(sys._MEIPASS) / relative_path
        if meipass_p.exists():
            return meipass_p

    if IS_MACOS:
        # 2. macOS .app bundle: executable lives in Contents/MacOS/
        exe_dir = Path(sys.executable).parent
        contents_dir = exe_dir.parent  # Contents/
        for sub in ("Resources", "Frameworks"):
            candidate = contents_dir / sub / relative_path
            if candidate.exists():
                return candidate
    else:
        # 2. Windows: look next to the .exe
        exe_dir = Path(sys.executable).parent
        candidate = exe_dir / relative_path
        if candidate.exists():
            return candidate

    # 3. Source / development fallback
    dev_p = Path(__file__).parent.resolve() / relative_path
    if dev_p.exists():
        return dev_p

    return Path.cwd() / relative_path


# ════════════════════════════════════════════════════════════════════════
#  Sleep Prevention
# ════════════════════════════════════════════════════════════════════════

class SleepPreventer:
    """
    Prevents the OS from entering sleep mode while downloads are active.

    macOS  — spawns `caffeinate -d -i -m -u` as a subprocess.
    Windows — calls SetThreadExecutionState via ctypes.
    """

    # Windows execution-state flags (from winbase.h)
    _ES_CONTINUOUS       = 0x80000000
    _ES_SYSTEM_REQUIRED  = 0x00000001
    _ES_DISPLAY_REQUIRED = 0x00000002
    _AWAKE_FLAGS = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None   # macOS only
        self._enabled: bool = True
        self._active: bool = False

    # ── Public properties ────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        if not value:
            self.stop()

    @property
    def is_preventing_sleep(self) -> bool:
        if IS_MACOS:
            return self._proc is not None and self._proc.poll() is None
        return self._active

    # ── start / stop ────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin preventing system sleep."""
        if not self._enabled or self.is_preventing_sleep:
            return
        try:
            if IS_MACOS:
                self._proc = subprocess.Popen(
                    ["caffeinate", "-d", "-i", "-m", "-u"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info("caffeinate active (pid=%d) — sleep prevented.", self._proc.pid)
            elif IS_WINDOWS:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(self._AWAKE_FLAGS)
                self._active = True
                logger.info("SetThreadExecutionState: sleep prevented on Windows.")
        except Exception as e:
            logger.warning("Could not start sleep prevention: %s", e)

    def stop(self) -> None:
        """Allow the system to sleep normally."""
        try:
            if IS_MACOS:
                if self._proc and self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=1.0)
                    except Exception:
                        self._proc.kill()
                    logger.info("caffeinate stopped — normal sleep restored.")
                self._proc = None
            elif IS_WINDOWS and self._active:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(self._ES_CONTINUOUS)
                self._active = False
                logger.info("SetThreadExecutionState cleared — normal sleep restored.")
        except Exception as e:
            logger.warning("Could not stop sleep prevention: %s", e)

    def sync_with_download_count(self, active_downloads_count: int) -> None:
        """Dynamically start or stop based on queue activity."""
        if active_downloads_count > 0:
            self.start()
        else:
            self.stop()


# Keep the old class name as an alias so any existing references still work
CaffeinateManager = SleepPreventer


# ════════════════════════════════════════════════════════════════════════
#  Desktop Notifications
# ════════════════════════════════════════════════════════════════════════

def send_notification(
    title: str,
    message: str,
    subtitle: str = "",
    tray_icon=None,          # QSystemTrayIcon instance (required on Windows)
) -> None:
    """
    Send a native desktop notification.

    macOS  — AppleScript `display notification` with Glass sound.
    Windows — QSystemTrayIcon.showMessage() (no extra dependency).
    """
    try:
        if IS_MACOS:
            sub_clause = f'subtitle "{subtitle}"' if subtitle else ""
            script = (
                f'display notification "{message}" '
                f'with title "{title}" {sub_clause} sound name "Glass"'
            )
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif IS_WINDOWS:
            if tray_icon is not None:
                from PyQt6.QtWidgets import QSystemTrayIcon
                tray_icon.showMessage(
                    title,
                    message,
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
            else:
                logger.debug("No tray_icon supplied for Windows notification.")
        else:
            logger.debug("Notification: [%s] %s", title, message)
    except Exception as e:
        logger.debug("Failed to send notification: %s", e)


# Legacy alias used in main_ui.py (keeps call-sites unchanged)
def send_macos_notification(title: str, message: str, subtitle: str = "") -> None:
    """Backward-compatible wrapper — calls send_notification on macOS."""
    send_notification(title, message, subtitle, tray_icon=None)


# ════════════════════════════════════════════════════════════════════════
#  File-Manager Reveal
# ════════════════════════════════════════════════════════════════════════

def reveal_in_file_manager(path: str | Path) -> None:
    """
    Open the system file manager with the given file or folder selected.

    macOS   — `open -R <path>`      (Finder)
    Windows — `explorer /select,<path>`  (File Explorer)
    """
    try:
        p = Path(path).expanduser().resolve()
        if IS_MACOS:
            if p.exists():
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["open", str(p.parent)])
        elif IS_WINDOWS:
            if p.exists():
                subprocess.Popen(["explorer", f"/select,{p}"],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.Popen(["explorer", str(p.parent)],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            # Linux / other POSIX
            subprocess.Popen(["xdg-open", str(p.parent)])
    except Exception as e:
        logger.error("Could not reveal %s in file manager: %s", path, e)


# Legacy alias so existing call-sites (reveal_in_finder) keep working
reveal_in_finder = reveal_in_file_manager


# ════════════════════════════════════════════════════════════════════════
#  UI Helpers
# ════════════════════════════════════════════════════════════════════════

def get_monospace_font() -> tuple[str, int]:
    """Return (font_family, point_size) for a good monospace font on this OS."""
    if IS_MACOS:
        return "Menlo", 11
    elif IS_WINDOWS:
        return "Consolas", 10
    else:
        return "Monospace", 10


def get_app_style() -> str:
    """Return the best Qt application style name for this OS."""
    if IS_MACOS:
        return "macOS"
    elif IS_WINDOWS:
        return "Fusion"
    return "Fusion"


def get_sleep_label() -> str:
    """Return the platform-appropriate sleep-prevention checkbox label."""
    if IS_MACOS:
        return "Prevent Mac sleep during active downloads (caffeinate)"
    return "Prevent sleep during active downloads"


def get_reveal_label() -> str:
    """Return the platform-appropriate 'reveal in file manager' action label."""
    if IS_MACOS:
        return "Reveal in Finder"
    return "Show in Explorer"
