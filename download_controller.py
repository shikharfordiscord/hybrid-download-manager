"""
download_controller.py — Smart Download Queue & Real-Time Stats
================================================================
Wraps aria2p to provide a high-level, type-safe API for adding downloads
(HTTP/HTTPS, magnet links, .torrent files) and streaming real-time
statistics.  Designed to be consumed by a Qt GUI or CLI.

Usage:
    from engine_manager import Aria2Engine
    from download_controller import DownloadController, DownloadType

    engine = Aria2Engine().start()
    ctrl   = DownloadController(engine)

    dl = ctrl.add("https://example.com/big.zip")
    for stats in ctrl.stream_stats(dl.gid):
        print(stats)             # DownloadStats dataclass
        if stats.is_complete:
            break
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Generator, Optional

import aria2p

from engine_manager import Aria2Engine

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
#  Type Detection
# ════════════════════════════════════════════════════════════════════════

class DownloadType(Enum):
    HTTP     = auto()   # Direct HTTP/HTTPS link
    MAGNET   = auto()   # magnet:?xt=... URI
    TORRENT  = auto()   # Path to a local .torrent file

    @classmethod
    def detect(cls, source: str) -> "DownloadType":
        """Auto-classify a download source string."""
        source = source.strip()

        if source.lower().startswith("magnet:"):
            return cls.MAGNET

        if source.lower().endswith(".torrent") and (
            os.path.isfile(source) or os.path.isfile(os.path.expanduser(source))
        ):
            return cls.TORRENT

        # Broad HTTP/HTTPS pattern (also catches FTP, SFTP)
        if re.match(r"^https?://", source, re.IGNORECASE):
            return cls.HTTP

        # Last-resort: check if it's a file
        expanded = os.path.expanduser(source)
        if os.path.isfile(expanded) and expanded.lower().endswith(".torrent"):
            return cls.TORRENT

        raise ValueError(
            f"Cannot determine download type for: {source!r}\n"
            "Expected an HTTP(S) URL, magnet: URI, or path to a .torrent file."
        )


# ════════════════════════════════════════════════════════════════════════
#  Real-Time Stats Dataclass
# ════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DownloadStats:
    """Immutable snapshot of a download's current state."""

    gid: str
    name: str
    status: str                    # "active", "waiting", "paused", "error", "complete", "removed"
    total_bytes: int
    completed_bytes: int
    download_speed: int            # bytes/s
    upload_speed: int              # bytes/s
    progress: float                # 0.0 – 100.0
    eta_seconds: Optional[float]   # None if speed is 0 or unknown
    connections: int               # active HTTP connections
    num_seeders: int               # BitTorrent seeders
    num_peers: int                 # BitTorrent peers (leechers + seeders)
    files: list[str] = field(default_factory=list)
    error_message: str = ""
    is_metadata: bool = False
    followed_by: list[str] = field(default_factory=list)

    # ── Convenience properties ──────────────────────────────────────

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    @property
    def is_active(self) -> bool:
        return self.status in ("active", "waiting")

    @property
    def download_speed_human(self) -> str:
        return self._humanize_speed(self.download_speed)

    @property
    def upload_speed_human(self) -> str:
        return self._humanize_speed(self.upload_speed)

    @property
    def total_human(self) -> str:
        return self._humanize_bytes(self.total_bytes)

    @property
    def completed_human(self) -> str:
        return self._humanize_bytes(self.completed_bytes)

    @property
    def eta_human(self) -> str:
        if self.eta_seconds is None:
            return "∞"
        s = int(self.eta_seconds)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60}s"
        h, rem = divmod(s, 3600)
        m = rem // 60
        return f"{h}h {m}m"

    @staticmethod
    def _humanize_speed(bps: int) -> str:
        for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
            if bps < 1024:
                return f"{bps:.1f} {unit}"
            bps /= 1024
        return f"{bps:.1f} TB/s"

    @staticmethod
    def _humanize_bytes(b: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"


# ════════════════════════════════════════════════════════════════════════
#  Download Controller
# ════════════════════════════════════════════════════════════════════════

class DownloadController:
    """High-level manager for adding downloads and streaming live stats."""

    def __init__(self, engine: Aria2Engine) -> None:
        self._engine = engine
        self._api: aria2p.API = engine.client

    # ── Add Downloads ───────────────────────────────────────────────

    def add(
        self,
        source: str,
        download_dir: Optional[str | Path] = None,
        options: Optional[dict] = None,
    ) -> aria2p.Download:
        """
        Auto-detect the source type and add it to the aria2 queue.

        Returns the aria2p.Download handle for tracking.
        """
        dtype = DownloadType.detect(source)
        opts = dict(options or {})

        if download_dir:
            opts["dir"] = str(Path(download_dir).expanduser().resolve())

        logger.info("Adding %s download: %s", dtype.name, source[:120])

        if dtype == DownloadType.HTTP:
            downloads = self._api.add(source, options=opts)

        elif dtype == DownloadType.MAGNET:
            downloads = self._api.add_magnet(source, options=opts)

        elif dtype == DownloadType.TORRENT:
            torrent_path = Path(source).expanduser().resolve()
            if not torrent_path.is_file():
                raise FileNotFoundError(f"Torrent file not found: {torrent_path}")
            downloads = self._api.add_torrent(str(torrent_path), options=opts)

        else:
            raise ValueError(f"Unsupported download type: {dtype}")

        # aria2p returns a list; we want the first (and usually only) item
        dl = downloads[0] if isinstance(downloads, list) else downloads
        logger.info("Queued GID=%s  name=%s", dl.gid, dl.name)
        return dl

    def add_batch(
        self,
        sources: list[str],
        download_dir: Optional[str | Path] = None,
    ) -> list[aria2p.Download]:
        """Add multiple sources in one call."""
        return [self.add(s, download_dir=download_dir) for s in sources]

    # ── Live Stats ──────────────────────────────────────────────────

    def get_stats(self, gid: str) -> DownloadStats:
        """Get a one-shot stats snapshot for a given GID."""
        dl = self._api.get_download(gid)
        return self._make_stats(dl)

    def stream_stats(
        self,
        gid: str,
        interval: float = 0.5,
    ) -> Generator[DownloadStats, None, None]:
        """
        Yield DownloadStats every *interval* seconds until the download
        finishes, errors out, or is removed.

        Designed to be consumed in a worker thread feeding a Qt signal.
        """
        while True:
            try:
                dl = self._api.get_download(gid)
                dl.update()   # refresh from RPC
            except aria2p.ClientException:
                logger.warning("Lost RPC connection while streaming GID=%s", gid)
                return

            stats = self._make_stats(dl)
            yield stats

            if stats.status in ("complete", "error", "removed"):
                return

            time.sleep(interval)

    def get_all_stats(self) -> list[DownloadStats]:
        """Snapshot of every download in the queue."""
        downloads = self._api.get_downloads()
        return [self._make_stats(dl) for dl in downloads]

    def get_global_stats(self) -> dict:
        """Global speed / queue counts from aria2."""
        st = self._api.get_stats()
        return {
            "download_speed": st.download_speed,
            "upload_speed": st.upload_speed,
            "num_active": st.num_active,
            "num_waiting": st.num_waiting,
            "num_stopped": st.num_stopped,
        }

    # ── Queue Control ───────────────────────────────────────────────

    def pause(self, gid: str) -> bool:
        dl = self._api.get_download(gid)
        return dl.pause()

    def resume(self, gid: str) -> bool:
        dl = self._api.get_download(gid)
        return dl.resume()

    def cancel(self, gid: str) -> bool:
        dl = self._api.get_download(gid)
        return dl.remove(force=True)

    def pause_all(self) -> None:
        self._api.pause_all()

    def resume_all(self) -> None:
        self._api.resume_all()

    def purge_completed(self) -> None:
        """Remove finished/errored downloads from the results list."""
        self._api.purge()

    # ── Private ─────────────────────────────────────────────────────

    @staticmethod
    def _make_stats(dl: aria2p.Download) -> DownloadStats:
        """Convert an aria2p.Download into our immutable stats object."""
        total = dl.total_length
        done  = dl.completed_length
        speed = dl.download_speed

        # Progress
        progress = (done / total * 100.0) if total > 0 else 0.0

        # ETA
        remaining = total - done
        eta = (remaining / speed) if speed > 0 else None

        # BitTorrent peer info
        num_seeders = 0
        num_peers   = 0
        try:
            if dl.bittorrent:
                num_seeders = getattr(dl, "num_seeders", 0) or 0
                # Connections for BT means peers
                num_peers = dl.connections or 0
        except Exception:
            pass

        # File list
        try:
            files = [f.path for f in dl.files if f.path]
        except Exception:
            files = []

        # Error message
        error_msg = ""
        if dl.status == "error":
            error_msg = getattr(dl, "error_message", "") or str(
                getattr(dl, "error_code", "")
            )

        raw_name = dl.name or "(unknown)"
        is_meta = raw_name.startswith("[METADATA]")
        clean_name = raw_name.replace("[METADATA]", "").strip() if is_meta else raw_name

        status = dl.status
        if is_meta and status in ("active", "waiting"):
            status = "fetching metadata"

        followed = []
        raw_followed = getattr(dl, "followed_by", []) or []
        for f in raw_followed:
            if hasattr(f, "gid"):
                followed.append(str(f.gid))
            elif isinstance(f, str):
                followed.append(f)

        return DownloadStats(
            gid=dl.gid,
            name=clean_name,
            status=status,
            total_bytes=total,
            completed_bytes=done,
            download_speed=speed,
            upload_speed=dl.upload_speed,
            progress=round(progress, 2),
            eta_seconds=eta,
            connections=dl.connections,
            num_seeders=num_seeders,
            num_peers=num_peers,
            files=files,
            error_message=error_msg,
            is_metadata=is_meta,
            followed_by=followed,
        )


# ── Quick self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")

    from engine_manager import Aria2Engine

    with Aria2Engine(download_dir="~/Downloads/hydownload managers") as engine:
        ctrl = DownloadController(engine)

        # Small test file
        TEST_URL = "https://speed.hetzner.de/100MB.bin"
        dl = ctrl.add(TEST_URL)

        print(f"\n{'─' * 60}")
        print(f"  Downloading: {dl.name}")
        print(f"  GID:         {dl.gid}")
        print(f"{'─' * 60}\n")

        for stats in ctrl.stream_stats(dl.gid, interval=1.0):
            bar_len = 30
            filled  = int(bar_len * stats.progress / 100)
            bar     = "█" * filled + "░" * (bar_len - filled)
            print(
                f"\r  [{bar}] {stats.progress:5.1f}%  "
                f"{stats.download_speed_human:>12}  "
                f"ETA {stats.eta_human:>8}  "
                f"Conn {stats.connections}",
                end="", flush=True,
            )
            if stats.is_complete:
                print("\n\n  ✓ Download complete!")
                break
            if stats.is_error:
                print(f"\n\n  ✗ Error: {stats.error_message}")
                break

    print("  ✓ Engine stopped cleanly.")
