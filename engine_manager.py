"""
engine_manager.py — aria2c RPC Daemon Lifecycle Manager
========================================================
Launches aria2c as a hidden subprocess in RPC mode on a dynamically-assigned
free port, with optimised settings for multi-connection HTTP downloads and
full BitTorrent/DHT support.  Guarantees clean shutdown via atexit, signal
handlers, and context-manager protocol.

Usage:
    engine = Aria2Engine(download_dir="~/Downloads")
    engine.start()          # aria2c is now listening on engine.rpc_port
    ...
    engine.stop()           # or let atexit / 'with' handle it
"""

from __future__ import annotations

import atexit
import logging
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import aria2p

logger = logging.getLogger(__name__)


# High-reliability modern BitTorrent trackers to supplement dead tracker links in magnets
DEFAULT_BT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://p4p.arenabg.ch:1337/announce",
]


class Aria2EngineError(Exception):
    """Raised when the aria2c daemon cannot start or is misconfigured."""


class Aria2Engine:
    """Manages the full lifecycle of an aria2c RPC daemon subprocess."""

    # ── Defaults ────────────────────────────────────────────────────────
    _DEFAULT_CONNECTIONS = 16        # -x (max-connection-per-server)
    _DEFAULT_SPLITS      = 16       # -s (split / concurrent chunks)
    _DEFAULT_RPC_SECRET  = ""       # empty ⇒ no auth (localhost only)
    _STARTUP_TIMEOUT_SEC = 8.0
    _STARTUP_POLL_SEC    = 0.25

    @staticmethod
    def _find_aria2c(explicit_path: Optional[str] = None) -> Optional[str]:
        if explicit_path and os.path.isfile(explicit_path):
            return explicit_path

        is_win = sys.platform == "win32"
        bin_name = "aria2c.exe" if is_win else "aria2c"

        # 1. Check PyInstaller _MEIPASS bundle (works on both macOS and Windows)
        if hasattr(sys, "_MEIPASS"):
            bundle_p = Path(sys._MEIPASS) / "bin" / bin_name
            if bundle_p.is_file():
                return str(bundle_p)

        exe_dir = Path(sys.executable).parent

        if is_win:
            # 2. Windows: check next to the .exe (dev mode staging)
            dev_p = exe_dir / "bin" / bin_name
            if dev_p.is_file():
                return str(dev_p)

            # 3. Windows: common install locations
            win_candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "aria2" / "aria2c.exe",
                Path("C:/ProgramData/chocolatey/bin/aria2c.exe"),
                Path("C:/Program Files/aria2/aria2c.exe"),
                Path("C:/Program Files (x86)/aria2/aria2c.exe"),
            ]
            for p in win_candidates:
                if p.is_file():
                    return str(p)
        else:
            # 2. macOS .app bundle paths (Contents/Resources/bin, Contents/Frameworks/bin)
            contents_dir = exe_dir.parent
            for sub in ("Resources/bin/aria2c", "Frameworks/bin/aria2c", "MacOS/bin/aria2c"):
                app_p = contents_dir / sub
                if app_p.is_file():
                    return str(app_p)

            # 3. macOS: PATH and standard Homebrew locations
            for cand in ("aria2c", "/opt/homebrew/bin/aria2c", "/usr/local/bin/aria2c"):
                p = shutil.which(cand) or (cand if os.path.isfile(cand) else None)
                if p:
                    return str(p)

        # 4. Generic PATH search (last resort, any OS)
        found = shutil.which(bin_name)
        if found:
            return found
        return None

    def __init__(
        self,
        download_dir: str | Path = "~/Downloads",
        connections: int = _DEFAULT_CONNECTIONS,
        splits: int = _DEFAULT_SPLITS,
        rpc_secret: str = _DEFAULT_RPC_SECRET,
        aria2c_path: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> None:
        self.download_dir = Path(download_dir).expanduser().resolve()
        self.connections = connections
        self.splits = splits
        self.rpc_secret = rpc_secret
        self.extra_args = extra_args or []

        # Locate the aria2c binary
        self._aria2c_bin = self._find_aria2c(aria2c_path)
        if not self._aria2c_bin:
            install_hint = (
                "choco install aria2  (or download from https://github.com/aria2/aria2/releases)"
                if sys.platform == "win32"
                else "brew install aria2"
            )
            raise Aria2EngineError(
                f"aria2c not found on PATH or app bundle. Install it with: {install_hint}"
            )

        # Runtime state (populated after .start())
        self._process: Optional[subprocess.Popen] = None
        self._rpc_port: Optional[int] = None
        self._client: Optional[aria2p.API] = None
        self._stopped = False

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def rpc_port(self) -> int:
        """The local TCP port aria2c is listening on."""
        if self._rpc_port is None:
            raise Aria2EngineError("Engine not started yet — call .start() first")
        return self._rpc_port

    @property
    def rpc_url(self) -> str:
        return f"http://localhost:{self.rpc_port}/jsonrpc"

    @property
    def client(self) -> aria2p.API:
        """Return a connected aria2p.API client, creating one if needed."""
        if self._client is None:
            self._client = aria2p.API(
                aria2p.Client(
                    host="http://localhost",
                    port=self.rpc_port,
                    secret=self.rpc_secret or "",
                )
            )
        return self._client

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # ── Public API ──────────────────────────────────────────────────────

    def start(self) -> "Aria2Engine":
        """Launch the aria2c daemon and block until the RPC port is ready."""
        if self.is_running:
            logger.info("aria2c is already running on port %d", self.rpc_port)
            return self

        self._rpc_port = self._find_free_port()
        self.download_dir.mkdir(parents=True, exist_ok=True)
        (Path.home() / ".aria2").mkdir(parents=True, exist_ok=True)

        cmd = self._build_command()
        logger.info("Starting aria2c: %s", " ".join(cmd))

        # Build platform-specific Popen kwargs
        popen_kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
        }
        if platform.system() == "Windows":
            # Suppress the black console window that would flash on screen
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            # Prevent the child from receiving Ctrl-C meant for the parent
            popen_kwargs["preexec_fn"] = os.setpgrp

        # Launch as a hidden daemon-like subprocess
        self._process = subprocess.Popen(cmd, **popen_kwargs)

        # Wait for the RPC port to become responsive
        if not self._wait_for_rpc():
            stderr_output = ""
            if self._process.stderr:
                stderr_output = self._process.stderr.read().decode(errors="replace")
            self._kill_process()
            raise Aria2EngineError(
                f"aria2c failed to start within {self._STARTUP_TIMEOUT_SEC}s.\n"
                f"stderr: {stderr_output}"
            )

        # Register cleanup handlers
        atexit.register(self.stop)
        # SIGTERM and SIGINT don't exist on Windows; guard accordingly
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self._signal_handler)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, self._signal_handler)

        logger.info(
            "aria2c ready — RPC on port %d, download dir: %s",
            self._rpc_port,
            self.download_dir,
        )
        return self

    def stop(self) -> None:
        """Gracefully shut down the aria2c daemon."""
        if self._stopped:
            return
        self._stopped = True

        try:
            if not self.is_running:
                logger.debug("aria2c process is not running; nothing to stop.")
                return

            logger.info("Shutting down aria2c (pid=%d) …", self._process.pid)

            # 1. Try graceful RPC shutdown
            try:
                self.client.client.shutdown()
                self._process.wait(timeout=2)
                logger.info("aria2c shut down gracefully via RPC.")
                return
            except Exception:
                pass

            # 2. SIGTERM
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
                logger.info("aria2c terminated via SIGTERM.")
                return
            except subprocess.TimeoutExpired:
                pass

            # 3. SIGKILL as a last resort
            self._kill_process()
            logger.warning("aria2c killed via SIGKILL.")
        except Exception as e:
            logger.debug("Engine stop exception: %s", e)

    def restart(self) -> "Aria2Engine":
        """Stop and re-start the daemon (port may change)."""
        self.stop()
        self._stopped = False
        self._client = None
        return self.start()

    # ── Context-manager protocol ────────────────────────────────────────

    def __enter__(self) -> "Aria2Engine":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ── Internal helpers ────────────────────────────────────────────────

    def _build_command(self) -> list[str]:
        """Assemble the aria2c CLI arguments."""
        cmd = [
            self._aria2c_bin,
            "--enable-rpc=true",
            f"--rpc-listen-port={self._rpc_port}",
            "--rpc-listen-all=false",          # localhost only
            f"--dir={self.download_dir}",
            f"--max-connection-per-server={self.connections}",
            f"--split={self.splits}",
            "--min-split-size=1M",
            "--continue=true",                 # resume partial downloads
            "--max-concurrent-downloads=5",
            "--max-overall-download-limit=0",  # unlimited
            "--file-allocation=falloc",        # fast pre-allocation (APFS on macOS, NTFS on Windows)

            # ── BitTorrent / DHT ────────────────────────────────────────
            "--enable-dht=true",
            "--enable-dht6=true",
            "--dht-listen-port=6881-6999",
            f"--dht-file-path={Path.home() / '.aria2' / 'dht.dat'}",
            f"--dht-file-path6={Path.home() / '.aria2' / 'dht6.dat'}",
            "--dht-entry-point=dht.transmissionbt.com:6881",
            "--dht-entry-point6=dht.transmissionbt.com:6881",
            "--dht-entry-point=router.bittorrent.com:6881",
            "--dht-entry-point=router.utorrent.com:6881",
            f"--bt-tracker={','.join(DEFAULT_BT_TRACKERS)}",
            "--bt-enable-lpd=true",            # local peer discovery
            "--bt-max-peers=80",
            "--seed-time=0",                   # stop seeding immediately after download completes
            "--seed-ratio=1.0",
            "--bt-save-metadata=true",
            "--follow-torrent=mem",            # parse .torrent in memory

            # ── Misc ────────────────────────────────────────────────────
            "--auto-file-renaming=true",
            "--allow-overwrite=false",
            "--check-integrity=true",
            "--console-log-level=warn",
            "--summary-interval=0",
            "--daemon=false",                  # stay in foreground for Popen
        ]

        if self.rpc_secret:
            cmd.append(f"--rpc-secret={self.rpc_secret}")

        cmd.extend(self.extra_args)
        return cmd

    @staticmethod
    def _find_free_port() -> int:
        """Ask the OS for an unused ephemeral TCP port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _wait_for_rpc(self) -> bool:
        """Poll the RPC port until it responds or we time out."""
        deadline = time.monotonic() + self._STARTUP_TIMEOUT_SEC
        while time.monotonic() < deadline:
            # Check if the process died
            if self._process.poll() is not None:
                return False
            try:
                with socket.create_connection(
                    ("127.0.0.1", self._rpc_port), timeout=0.5
                ):
                    return True
            except OSError:
                time.sleep(self._STARTUP_POLL_SEC)
        return False

    def _kill_process(self) -> None:
        """Force-kill the subprocess."""
        if self._process and self._process.poll() is None:
            self._process.kill()
            self._process.wait(timeout=2)

    def _signal_handler(self, signum: int, _frame) -> None:
        """Handle SIGTERM/SIGINT by cleaning up, then re-raising."""
        self.stop()
        sys.exit(128 + signum)


# ── Quick self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    with Aria2Engine(download_dir="~/Downloads/hydownload managers") as engine:
        print(f"✓ aria2c running on port {engine.rpc_port}")
        version = engine.client.client.get_version()
        print(f"  Version : {version}")
        stats = engine.client.get_stats()
        print(f"  Speed   : ↓ {stats.download_speed_string()}")
        input("  Press Enter to shut down …")

    print("✓ aria2c stopped cleanly.")
