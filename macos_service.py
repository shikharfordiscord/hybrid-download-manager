"""
macos_service.py — Native macOS Integration & Power Management
===============================================================
Provides:
  1. Sleep prevention via macOS `caffeinate` daemon when downloads are active.
  2. Native macOS notifications via AppleScript (`osascript`).
  3. Finder integration (reveal file in Finder).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_resource_path(relative_path: str) -> Path:
    """
    Resolves bundled resource paths reliably across:
    1. PyInstaller frozen environment (sys._MEIPASS)
    2. macOS .app bundle directory (Contents/Resources or Contents/Frameworks)
    3. Local development checkout
    """
    import sys
    if hasattr(sys, "_MEIPASS"):
        meipass_p = Path(sys._MEIPASS) / relative_path
        if meipass_p.exists():
            return meipass_p

    exe_dir = Path(sys.executable).parent
    contents_dir = exe_dir.parent
    if (contents_dir / "Resources" / relative_path).exists():
        return contents_dir / "Resources" / relative_path
    if (contents_dir / "Frameworks" / relative_path).exists():
        return contents_dir / "Frameworks" / relative_path

    # Source directory fallback
    dev_p = Path(__file__).parent.resolve() / relative_path
    if dev_p.exists():
        return dev_p

    return Path.cwd() / relative_path


class CaffeinateManager:
    """
    Prevents macOS from entering sleep mode while downloads are in progress.
    Invokes `caffeinate -d -i -m -u` as a managed subprocess.
    """

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        if not value:
            self.stop()

    @property
    def is_preventing_sleep(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Start caffeinate if not already active."""
        if not self._enabled or self.is_preventing_sleep:
            return

        try:
            # -d: prevent display sleep
            # -i: prevent system idle sleep
            # -m: prevent disk idle sleep
            # -u: declare user activity
            self._proc = subprocess.Popen(
                ["caffeinate", "-d", "-i", "-m", "-u"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("caffeinate active (pid=%d) — sleep prevented during download.", self._proc.pid)
        except Exception as e:
            logger.warning("Could not start caffeinate: %s", e)

    def stop(self) -> None:
        """Terminate caffeinate and restore normal power behavior."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except Exception:
                self._proc.kill()
            logger.info("caffeinate stopped — normal sleep behavior restored.")
        self._proc = None

    def sync_with_download_count(self, active_downloads_count: int) -> None:
        """Helper to dynamically start or stop based on queue activity."""
        if active_downloads_count > 0:
            self.start()
        else:
            self.stop()


def send_macos_notification(title: str, message: str, subtitle: str = "") -> None:
    """Send native macOS notification banner via osascript."""
    try:
        sub_clause = f'subtitle "{subtitle}"' if subtitle else ""
        script = f'display notification "{message}" with title "{title}" {sub_clause} sound name "Glass"'
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.debug("Failed to send notification: %s", e)


def reveal_in_finder(path: str | Path) -> None:
    """Open Finder with the specified file or folder selected."""
    try:
        p = Path(path).expanduser().resolve()
        if p.exists():
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["open", str(p.parent)])
    except Exception as e:
        logger.error("Could not reveal %s in Finder: %s", path, e)
