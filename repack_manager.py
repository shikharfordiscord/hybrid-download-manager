"""
repack_manager.py — Smart Multi-Part Archive Parser & Auto-Extractor
====================================================================
Specialized engine for handling multi-part split archive sets
(e.g., .part01.rar ... .part30.rar, .001 ... .030, .r00 ... .r30).

Features:
  1. Multi-part link clustering (groups split archives by base name)
  2. Batch grouping into a unified RepackGroup / ArchiveGroup
  3. Sequential or parallel queue scheduling
  4. Post-download SHA256 / MD5 checksum verification (matches .md5 / .sha256)
  5. Auto-extraction using 7zz / unar / zipfile when all parts finish
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
#  Archive Pattern Matching & Normalization
# ════════════════════════════════════════════════════════════════════════

# Common multi-part archive regex patterns
RE_PART_PATTERNS = [
    # .part01.rar / .part1.rar / .part001.rar
    re.compile(r"^(?P<base>.+?)\.part(?P<num>\d+)\.(?P<ext>rar|zip|7z)$", re.IGNORECASE),
    # .rar, .r00, .r01, .r02 ...
    re.compile(r"^(?P<base>.+?)\.(?:r(?P<num>\d{2,3})|rar)$", re.IGNORECASE),
    # .7z.001, .zip.001, .001
    re.compile(r"^(?P<base>.+?)(?:\.(?:7z|zip|rar|tar))?\.(?P<num>\d{3,4})$", re.IGNORECASE),
    # _part01 / -part1
    re.compile(r"^(?P<base>.+?)[._-]part_?(?P<num>\d+)(?:\.(?P<ext>[a-zA-Z0-9]+))?$", re.IGNORECASE),
    # .z01, .z02 ...
    re.compile(r"^(?P<base>.+?)\.z(?P<num>\d{2,3})$", re.IGNORECASE),
]


def extract_filename_from_url(url: str) -> str:
    """Extract and unquote filename from URL or file path."""
    parsed = urlparse(url)
    path = parsed.path if parsed.scheme else url
    filename = Path(unquote(path)).name
    # Strip URL parameters if present in raw string
    filename = filename.split("?")[0].split("#")[0]
    return filename


def parse_multipart_name(filename: str) -> tuple[str, int, str]:
    """
    Parses a filename into (base_name, part_number, extension).
    If not recognized as multi-part, returns (stem, 1, ext).
    """
    for pat in RE_PART_PATTERNS:
        m = pat.match(filename)
        if m:
            base = m.group("base").rstrip("._-")
            num_str = m.groupdict().get("num")
            part_num = int(num_str) if num_str else 1
            ext = m.groupdict().get("ext") or Path(filename).suffix.lstrip(".")
            return base, part_num, ext

    path = Path(filename)
    return path.stem, 1, path.suffix.lstrip(".")


# ════════════════════════════════════════════════════════════════════════
#  Data Models
# ════════════════════════════════════════════════════════════════════════

class RepackStatus(Enum):
    QUEUED       = auto()
    DOWNLOADING  = auto()
    VERIFYING    = auto()
    EXTRACTING   = auto()
    COMPLETED    = auto()
    ERROR        = auto()


@dataclass
class RepackPart:
    url: str
    filename: str
    part_num: int
    gid: Optional[str] = None
    completed: bool = False
    file_path: Optional[Path] = None
    file_size: int = 0
    checksum: Optional[str] = None


@dataclass
class RepackGroup:
    group_id: str
    title: str
    parts: list[RepackPart] = field(default_factory=list)
    download_dir: Path = field(default_factory=lambda: Path.home() / "Downloads" / "hydownload managers")
    extract_dir: Optional[Path] = None
    status: RepackStatus = RepackStatus.QUEUED
    status_message: str = "Queued"
    auto_extract: bool = True
    auto_verify: bool = True
    sequential: bool = True       # Download 1 part at a time to avoid throttling
    current_part_index: int = 0
    total_bytes: int = 0
    completed_bytes: int = 0
    extraction_progress: float = 0.0

    @property
    def total_parts(self) -> int:
        return len(self.parts)

    @property
    def completed_parts(self) -> int:
        return sum(1 for p in self.parts if p.completed)

    @property
    def overall_progress(self) -> float:
        if self.total_parts == 0:
            return 0.0
        return (self.completed_parts / self.total_parts) * 100.0


# ════════════════════════════════════════════════════════════════════════
#  Repack Manager Engine
# ════════════════════════════════════════════════════════════════════════

class RepackManager:
    """Orchestrates multi-part link clustering, sequential queuing, and auto-extraction."""

    def __init__(self, download_controller, default_dir: Path | str = "~/Downloads/hydownload managers"):
        self.ctrl = download_controller
        self.default_dir = Path(default_dir).expanduser().resolve()
        self.groups: dict[str, RepackGroup] = {}
        self._seven_zip_bin = self._find_7z()
        self._lock = threading.Lock()

    @staticmethod
    def _find_7z() -> Optional[str]:
        """Locate 7zz, 7z, or unar on macOS, including inside .app bundles."""
        import sys
        if hasattr(sys, "_MEIPASS"):
            b7z = Path(sys._MEIPASS) / "bin" / "7zz"
            if b7z.is_file():
                return str(b7z)

        exe_dir = Path(sys.executable).parent
        contents_dir = exe_dir.parent
        for sub in ("Resources/bin/7zz", "Frameworks/bin/7zz", "MacOS/bin/7zz"):
            app_p = contents_dir / sub
            if app_p.is_file():
                return str(app_p)

        for candidate in ("7zz", "7z", "/opt/homebrew/bin/7zz", "/usr/local/bin/7zz", "unar"):
            p = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
            if p:
                return str(p)
        return None

    # ── Multi-Part Parser & Clustering ─────────────────────────────────

    def parse_links_blob(self, raw_text: str) -> dict[str, list[dict]]:
        """
        Takes raw multi-line pasted text (containing 1 to 100+ URLs or file paths),
        extracts valid URLs/paths, and groups them automatically by base archive name.

        Returns:
            dict mapping base_group_name -> list of {"url": url, "filename": name, "part": num}
        """
        lines = [line.strip() for line in raw_text.strip().splitlines() if line.strip()]
        clusters: dict[str, list[dict]] = {}

        for line in lines:
            # Handle potential markdown links or quote wrappers
            clean_line = line.strip("<>\"' \t")
            if not clean_line:
                continue

            # Extract filename
            filename = extract_filename_from_url(clean_line)
            if not filename:
                continue

            base, part_num, ext = parse_multipart_name(filename)
            # Normalize group name
            clean_base = re.sub(r"[._-]+", " ", base).strip().title()

            if clean_base not in clusters:
                clusters[clean_base] = []

            clusters[clean_base].append({
                "url": clean_line,
                "filename": filename,
                "part": part_num,
                "ext": ext
            })

        # Sort each group by part number
        for base in clusters:
            clusters[base].sort(key=lambda item: item["part"])

        return clusters

    def create_group(
        self,
        title: str,
        link_items: list[dict],
        download_dir: Optional[Path | str] = None,
        extract_dir: Optional[Path | str] = None,
        auto_extract: bool = True,
        auto_verify: bool = True,
        sequential: bool = True,
    ) -> RepackGroup:
        """Create and register a new RepackGroup."""
        dest = Path(download_dir or self.default_dir / title).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)

        ext_dest = Path(extract_dir or dest / "_extracted").expanduser().resolve() if auto_extract else None

        group_id = hashlib.md5(f"{title}_{len(link_items)}_{dest}".encode()).hexdigest()[:10]

        parts = [
            RepackPart(
                url=item["url"],
                filename=item["filename"],
                part_num=item["part"]
            )
            for item in sorted(link_items, key=lambda x: x["part"])
        ]

        group = RepackGroup(
            group_id=group_id,
            title=title,
            parts=parts,
            download_dir=dest,
            extract_dir=ext_dest,
            status=RepackStatus.QUEUED,
            auto_extract=auto_extract,
            auto_verify=auto_verify,
            sequential=sequential,
        )

        with self._lock:
            self.groups[group_id] = group

        logger.info("Created RepackGroup '%s' with %d parts in %s", title, len(parts), dest)
        return group

    # ── Queue Execution ────────────────────────────────────────────────

    def start_group(self, group_id: str) -> None:
        """Begin downloading the repack group."""
        group = self.groups.get(group_id)
        if not group or not group.parts:
            return

        group.status = RepackStatus.DOWNLOADING
        group.status_message = "Starting downloads..."

        if group.sequential:
            # Queue only the first part initially
            self._queue_next_part(group)
        else:
            # Queue all parts in aria2
            for part in group.parts:
                self._dispatch_part(group, part)

    def _queue_next_part(self, group: RepackGroup) -> None:
        """Queue the next uncompleted part in sequential mode."""
        for part in group.parts:
            if not part.completed and part.gid is None:
                self._dispatch_part(group, part)
                group.status_message = f"Downloading Part {part.part_num} of {group.total_parts}"
                return

        # If all parts are done, start post-processing!
        self._on_all_parts_downloaded(group)

    def _dispatch_part(self, group: RepackGroup, part: RepackPart) -> None:
        """Submit a single part to aria2."""
        try:
            dl = self.ctrl.add(part.url, download_dir=group.download_dir)
            part.gid = dl.gid
            logger.info("Dispatched part %d for group '%s' (GID: %s)", part.part_num, group.title, dl.gid)
        except Exception as e:
            logger.error("Failed to queue part %d: %s", part.part_num, e)
            group.status = RepackStatus.ERROR
            group.status_message = f"Part {part.part_num} error: {e}"

    # ── Status Sync Callback (Called during UI poll loop) ───────────────

    def update_status(self) -> None:
        """Polls active groups and checks for part completions."""
        with self._lock:
            for group in list(self.groups.values()):
                if group.status != RepackStatus.DOWNLOADING:
                    continue

                all_done = True
                active_count = 0

                for part in group.parts:
                    if part.completed:
                        continue

                    if part.gid:
                        try:
                            stats = self.ctrl.get_stats(part.gid)
                            if stats.is_complete:
                                part.completed = True
                                part.file_path = group.download_dir / part.filename
                                logger.info("Part %d completed: %s", part.part_num, part.filename)
                                if group.sequential:
                                    # Trigger next part in line
                                    self._queue_next_part(group)
                                    break
                            elif stats.is_error:
                                group.status = RepackStatus.ERROR
                                group.status_message = f"Part {part.part_num} failed: {stats.error_message}"
                                return
                            else:
                                active_count += 1
                                all_done = False
                        except Exception:
                            all_done = False
                    else:
                        all_done = False

                if all_done and group.completed_parts == group.total_parts:
                    self._on_all_parts_downloaded(group)

    # ── Post-Download Pipeline (Checksums & Extraction) ────────────────

    def _on_all_parts_downloaded(self, group: RepackGroup) -> None:
        """Fired when 100% of parts in a group are downloaded."""
        group.status_message = "All parts downloaded. Initializing post-processing..."
        logger.info("Group '%s' finished all %d downloads", group.title, group.total_parts)

        # Run verification and extraction in background thread
        thread = threading.Thread(target=self._post_process_worker, args=(group,), daemon=True)
        thread.start()

    def _post_process_worker(self, group: RepackGroup) -> None:
        """Background thread for checksum verification and auto-extraction."""
        try:
            # 1. Checksum verification if requested and checksum file exists
            if group.auto_verify:
                group.status = RepackStatus.VERIFYING
                group.status_message = "Verifying checksums..."
                self._verify_group_checksums(group)

            # 2. Auto-extraction if requested
            if group.auto_extract:
                group.status = RepackStatus.EXTRACTING
                group.status_message = "Extracting multi-part archive..."
                self.extract_group(group)

            group.status = RepackStatus.COMPLETED
            group.status_message = "Completed and Extracted!"
            logger.info("Group '%s' successfully processed!", group.title)

        except Exception as e:
            logger.error("Post-processing failed for '%s': %s", group.title, e, exc_info=True)
            group.status = RepackStatus.ERROR
            group.status_message = f"Post-processing failed: {e}"

    def _verify_group_checksums(self, group: RepackGroup) -> bool:
        """Looks for .sha256 or .md5 files in download_dir and verifies parts."""
        checksum_files = list(group.download_dir.glob("*.sha256")) + list(group.download_dir.glob("*.md5"))
        if not checksum_files:
            logger.info("No checksum file found in %s; skipping verification.", group.download_dir)
            return True

        for cfile in checksum_files:
            is_sha = cfile.suffix.lower() == ".sha256"
            hasher_factory = hashlib.sha256 if is_sha else hashlib.md5
            logger.info("Verifying against %s ...", cfile.name)

            with open(cfile, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        expected_hash = parts[0].strip().lower()
                        target_fname = Path(parts[-1].strip().lstrip("*")).name
                        target_file = group.download_dir / target_fname

                        if target_file.is_file():
                            group.status_message = f"Verifying {target_fname}..."
                            computed = self._compute_hash(target_file, hasher_factory)
                            if computed.lower() != expected_hash:
                                raise ValueError(
                                    f"Checksum mismatch for {target_fname}!\n"
                                    f"Expected: {expected_hash}\nGot: {computed}"
                                )
                            logger.info("✓ %s verified successfully", target_fname)

        return True

    @staticmethod
    def _compute_hash(path: Path, hasher_factory: Callable) -> str:
        """Compute file hash in 1MB chunks."""
        h = hasher_factory()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    def extract_group(self, group: RepackGroup) -> None:
        """Finds the primary archive part and extracts it using 7zz / unar."""
        if not self._seven_zip_bin:
            raise RuntimeError(
                "Neither 7zz, 7z nor unar found! Install with: brew install sevenzip"
            )

        # Find the starting file (part1, part01, or first .rar/.7z/.001)
        first_file = self._find_first_part(group)
        if not first_file:
            raise FileNotFoundError(f"Could not locate initial archive part in {group.download_dir}")

        out_dir = group.extract_dir or (group.download_dir / "_extracted")
        out_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Extracting %s -> %s using %s", first_file.name, out_dir, self._seven_zip_bin)
        group.status_message = f"Extracting {first_file.name}..."

        # Run 7zz extraction: x = extract with full paths, -y = assume yes on all queries, -o = out dir
        if "7z" in Path(self._seven_zip_bin).name:
            cmd = [self._seven_zip_bin, "x", "-y", f"-o{out_dir}", str(first_file)]
        else: # unar
            cmd = [self._seven_zip_bin, "-o", str(out_dir), "-f", str(first_file)]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Extraction failed with exit code {proc.returncode}:\n{stderr or stdout}")

        logger.info("Extraction complete for %s", group.title)
        group.extraction_progress = 100.0

    @staticmethod
    def _find_first_part(group: RepackGroup) -> Optional[Path]:
        """Identifies the root volume of the multi-part archive."""
        files = [group.download_dir / p.filename for p in group.parts if (group.download_dir / p.filename).is_file()]
        if not files:
            files = list(group.download_dir.glob("*"))

        # Look for explicit .part01.rar / .part1.rar / .001 / .rar
        for f in files:
            name = f.name.lower()
            if re.search(r"\.part0*1\.(rar|7z|zip)$", name) or name.endswith(".001") or name.endswith(".r00"):
                return f

        # Fallback: look for simple .rar or .7z or .zip
        for f in files:
            if f.suffix.lower() in (".rar", ".7z", ".zip"):
                return f

        return files[0] if files else None
