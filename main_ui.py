"""
main_ui.py — HyDownload Manager Desktop Interface
======================================================================
Combines:
  1. Live Download Queue Table with progress bar delegates & real-time stats
  2. Smart Multi-Part Archive Manager with split-archive link parsing, sequential queuing & auto-extraction
  3. Browser Interceptor & Clipboard Monitor (instant download catching)
  4. macOS Polish: Menu Bar Tray Icon, Sleep Prevention (caffeinate), Native Notifications
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QModelIndex,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSystemTrayIcon,
)

from browser_interceptor import BrowserInterceptorServer
from download_controller import DownloadController, DownloadStats, DownloadType
from engine_manager import Aria2Engine, Aria2EngineError
from macos_service import CaffeinateManager, reveal_in_finder, send_macos_notification, get_resource_path
from repack_manager import RepackGroup, RepackManager, RepackStatus

logger = logging.getLogger(__name__)

APP_NAME = "hydownload managers"
APP_VERSION = "2.0.0"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "hydownload managers"

# Table Columns for Main Queue
COL_NAME = 0
COL_SIZE = 1
COL_PROGRESS = 2
COL_SPEED = 3
COL_ETA = 4
COL_PEERS = 5
COL_STATUS = 6
QUEUE_COLUMNS = ["File Name", "Size", "Progress", "Speed", "ETA", "Peers / Conn", "Status"]


# ════════════════════════════════════════════════════════════════════════
#  Custom Cell Delegates
# ════════════════════════════════════════════════════════════════════════

class ProgressBarDelegate(QStyledItemDelegate):
    """Paints a native macOS-styled progress bar inside table cells."""

    def paint(self, painter: QPainter, option, index: QModelIndex):
        progress = index.data(Qt.ItemDataRole.UserRole)
        if progress is None:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionProgressBar()
        opt.rect = option.rect.adjusted(4, 4, -4, -4)
        opt.minimum = 0
        opt.maximum = 1000
        opt.progress = int(float(progress) * 10)
        opt.text = f"{float(progress):.1f}%"
        opt.textVisible = True

        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ProgressBar, opt, painter)


# ════════════════════════════════════════════════════════════════════════
#  Background Poller Thread
# ════════════════════════════════════════════════════════════════════════

class BackgroundPoller(QThread):
    """Smooth polling worker that streams aria2 stats & repack updates without blocking the UI."""

    stats_updated = pyqtSignal(list, dict)  # (all_stats, global_stats)
    repack_updated = pyqtSignal()

    def __init__(self, controller: DownloadController, repack_mgr: RepackManager, interval_ms: int = 750):
        super().__init__()
        self.ctrl = controller
        self.repack_mgr = repack_mgr
        self.interval = interval_ms / 1000.0
        self._running = True

    def run(self):
        while self._running:
            try:
                stats = self.ctrl.get_all_stats()
                global_stats = self.ctrl.get_global_stats()
                if self._running:
                    self.stats_updated.emit(stats, global_stats)

                # Update repack state machine
                if self._running:
                    self.repack_mgr.update_status()
                    self.repack_updated.emit()
            except Exception as e:
                logger.debug("Polling error: %s", e)

            # Sleep in small slices so stopping is instantaneous
            slice_count = max(1, int(self.interval * 10))
            for _ in range(slice_count):
                if not self._running:
                    break
                time.sleep(0.1)

    def stop(self):
        self._running = False
        try:
            self.wait(1500)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════
#  Add Download Dialog
# ════════════════════════════════════════════════════════════════════════

class AddDownloadDialog(QDialog):
    """Modal dialog for pasting single or direct URLs / magnets / torrents."""

    def __init__(self, parent=None, default_dir: Path | str = DEFAULT_DOWNLOAD_DIR, initial_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Add Download")
        self.setMinimumWidth(560)

        layout = QFormLayout(self)

        self.input_field = QLineEdit(initial_text)
        self.input_field.setPlaceholderText("Paste HTTP/HTTPS link, magnet URI, or path to .torrent...")
        row_src = QHBoxLayout()
        row_src.addWidget(self.input_field, stretch=1)

        self.btn_browse = QPushButton("Browse Torrent…")
        self.btn_browse.clicked.connect(self._browse_torrent)
        row_src.addWidget(self.btn_browse)
        layout.addRow("Source:", row_src)

        self.dir_field = QLineEdit(str(default_dir))
        row_dir = QHBoxLayout()
        row_dir.addWidget(self.dir_field, stretch=1)
        btn_dir = QPushButton("Choose…")
        btn_dir.clicked.connect(self._browse_dir)
        row_dir.addWidget(btn_dir)
        layout.addRow("Save To:", row_dir)

        self.lbl_type = QLabel("—")
        layout.addRow("Detected Type:", self.lbl_type)
        self.input_field.textChanged.connect(self._on_text_changed)
        if initial_text:
            self._on_text_changed(initial_text)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _browse_torrent(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open Torrent File", "", "Torrent Files (*.torrent);;All (*)")
        if p:
            self.input_field.setText(p)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Download Directory")
        if d:
            self.dir_field.setText(d)

    def _on_text_changed(self, text: str):
        try:
            dt = DownloadType.detect(text)
            txts = {
                DownloadType.HTTP: "🌐 Direct HTTP/HTTPS Link (16x Multi-Chunk)",
                DownloadType.MAGNET: "🧲 BitTorrent Magnet Link",
                DownloadType.TORRENT: "📦 Local BitTorrent File",
            }
            self.lbl_type.setText(txts.get(dt, str(dt)))
            self.lbl_type.setStyleSheet("color: #2ea043; font-weight: bold;")
        except ValueError:
            self.lbl_type.setText("Enter a valid URL or torrent file")
            self.lbl_type.setStyleSheet("color: #888;")


# ════════════════════════════════════════════════════════════════════════
#  Main Window
# ════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    """The central Hybrid Download Manager window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1080, 680)

        # 1. Start Engine & Controller
        DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.engine = Aria2Engine(download_dir=DEFAULT_DOWNLOAD_DIR, connections=16, splits=16).start()
        except Aria2EngineError as e:
            QMessageBox.critical(self, "Engine Failure", f"Could not start aria2 daemon:\n{e}")
            sys.exit(1)

        self.ctrl = DownloadController(self.engine)
        self.repack_mgr = RepackManager(self.ctrl, default_dir=DEFAULT_DOWNLOAD_DIR)
        self.caffeinate = CaffeinateManager()
        self.last_completed_gids: set[str] = set()
        self.known_clipboard_text = ""

        # 2. Browser Interceptor HTTP Server (Port 6801)
        self.interceptor = BrowserInterceptorServer(
            port=6801,
            on_download=self._on_browser_interception
        ).start()

        # 3. Build UI Layout
        self._build_menu()
        self._build_toolbar()
        self._build_central_tabs()
        self._build_status_bar()
        self._build_tray_icon()

        # 4. Start Background Poller
        self.poller = BackgroundPoller(self.ctrl, self.repack_mgr, interval_ms=750)
        self.poller.stats_updated.connect(self._on_stats_updated)
        self.poller.repack_updated.connect(self._on_repack_updated)
        self.poller.start()

        # 5. Clipboard Monitor Timer
        self.clipboard_timer = QTimer(self)
        self.clipboard_timer.timeout.connect(self._check_clipboard)
        self.clipboard_timer.start(1500)

        self.gids_order: list[str] = []

    # ── Top Menu & Toolbar ──────────────────────────────────────────────

    def _build_menu(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        act_add = QAction("&Add Download…", self)
        act_add.setShortcut(QKeySequence("Ctrl+N"))
        act_add.triggered.connect(self._on_add_download_dialog)
        file_menu.addAction(act_add)

        act_open_torrent = QAction("Open &Torrent File…", self)
        act_open_torrent.setShortcut(QKeySequence("Ctrl+O"))
        act_open_torrent.triggered.connect(self._on_open_torrent)
        file_menu.addAction(act_open_torrent)

        file_menu.addSeparator()
        act_reveal_dl = QAction("Open Downloads Folder", self)
        act_reveal_dl.triggered.connect(lambda: reveal_in_finder(DEFAULT_DOWNLOAD_DIR))
        file_menu.addAction(act_reveal_dl)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        queue_menu = mb.addMenu("&Queue")
        act_pause_all = QAction("Pause All", self)
        act_pause_all.triggered.connect(self.ctrl.pause_all)
        queue_menu.addAction(act_pause_all)

        act_resume_all = QAction("Resume All", self)
        act_resume_all.triggered.connect(self.ctrl.resume_all)
        queue_menu.addAction(act_resume_all)

        act_purge = QAction("Purge Finished / Removed", self)
        act_purge.triggered.connect(self.ctrl.purge_completed)
        queue_menu.addAction(act_purge)

    def _build_toolbar(self):
        tb = QToolBar("Main Controls")
        tb.setMovable(False)
        self.addToolBar(tb)

        btn_add = QPushButton("➕ Add URL / Magnet")
        btn_add.clicked.connect(self._on_add_download_dialog)
        tb.addWidget(btn_add)

        btn_torrent = QPushButton("📦 Open .torrent")
        btn_torrent.clicked.connect(self._on_open_torrent)
        tb.addWidget(btn_torrent)

        tb.addSeparator()

        btn_pause = QPushButton("⏸ Pause")
        btn_pause.clicked.connect(self._on_pause_selected)
        tb.addWidget(btn_pause)

        btn_resume = QPushButton("▶ Resume")
        btn_resume.clicked.connect(self._on_resume_selected)
        tb.addWidget(btn_resume)

        btn_delete = QPushButton("✖ Delete")
        btn_delete.clicked.connect(self._on_delete_selected)
        tb.addWidget(btn_delete)

        tb.addSeparator()

        btn_reveal = QPushButton("📂 Open Folder")
        btn_reveal.clicked.connect(self._on_reveal_selected)
        tb.addWidget(btn_reveal)

    # ── Central Tabs ────────────────────────────────────────────────────

    def _build_central_tabs(self):
        self.tabs = QTabWidget()

        # Tab 1: Active Downloads Table
        self.tab_queue = QWidget()
        vbox_q = QVBoxLayout(self.tab_queue)
        vbox_q.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(QUEUE_COLUMNS))
        self.table.setHorizontalHeaderLabels(QUEUE_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setItemDelegateForColumn(COL_PROGRESS, ProgressBarDelegate(self.table))

        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (COL_SIZE, COL_SPEED, COL_ETA, COL_PEERS, COL_STATUS):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_PROGRESS, 200)

        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)

        vbox_q.addWidget(self.table)
        self.tabs.addTab(self.tab_queue, "⚡ Downloads Queue")

        # Tab 2: Smart Multi-Part Archive Manager
        self.tab_repacks = self._build_repack_tab()
        self.tabs.addTab(self.tab_repacks, "📦 Multi-Part Archives")

        # Tab 3: Browser Integration & Extension Info
        self.tab_browser = self._build_browser_tab()
        self.tabs.addTab(self.tab_browser, "🌐 Browser & Integrations")

        self.setCentralWidget(self.tabs)

    # ── Tab 2: Smart Multi-Part Archive Manager UI ──────────────────────

    def _build_repack_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Upper section: Batch link input
        grp_input = QGroupBox("Batch Multi-Part Archive Parser (Paste multi-part .rar / .7z / .001 links)")
        vbox_in = QVBoxLayout(grp_input)

        self.txt_repack_links = QTextEdit()
        self.txt_repack_links.setPlaceholderText(
            "Paste a block of split archive links here...\n"
            "Example:\n"
            "https://example.com/dataset.part01.rar\n"
            "https://example.com/dataset.part02.rar\n"
            "...\n"
            "https://example.com/dataset.part30.rar"
        )
        self.txt_repack_links.setMaximumHeight(130)
        vbox_in.addWidget(self.txt_repack_links)

        # Options Row
        row_opts = QHBoxLayout()
        self.chk_sequential = QCheckBox("Sequential Queue (1 part at a time to prevent host blocking)")
        self.chk_sequential.setChecked(True)
        row_opts.addWidget(self.chk_sequential)

        self.chk_auto_verify = QCheckBox("Verify Checksums (.sha256 / .md5)")
        self.chk_auto_verify.setChecked(True)
        row_opts.addWidget(self.chk_auto_verify)

        self.chk_auto_extract = QCheckBox("Auto-Extract when all parts finish (7zz)")
        self.chk_auto_extract.setChecked(True)
        row_opts.addWidget(self.chk_auto_extract)

        btn_parse = QPushButton("✨ Analyze & Group Links")
        btn_parse.clicked.connect(self._on_parse_repack_links)
        row_opts.addWidget(btn_parse)

        vbox_in.addLayout(row_opts)
        layout.addWidget(grp_input)

        # Lower section: Archive Groups Tree
        grp_groups = QGroupBox("Archive Groups & Extraction Pipeline")
        vbox_grp = QVBoxLayout(grp_groups)

        self.tree_repacks = QTreeWidget()
        self.tree_repacks.setHeaderLabels(["Archive Set / Part", "Progress", "Status", "Destination"])
        self.tree_repacks.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree_repacks.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree_repacks.setColumnWidth(1, 160)
        vbox_grp.addWidget(self.tree_repacks)

        row_actions = QHBoxLayout()
        btn_open_extracted = QPushButton("📂 Open Extracted Folder")
        btn_open_extracted.clicked.connect(self._on_open_extracted_folder)
        row_actions.addWidget(btn_open_extracted)
        row_actions.addStretch()
        vbox_grp.addLayout(row_actions)

        layout.addWidget(grp_groups)
        return widget

    # ── Tab 3: Browser & Integrations UI ────────────────────────────────

    def _build_browser_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        grp_listener = QGroupBox("Browser Interception Daemon (The 'IDM Feel')")
        form = QFormLayout(grp_listener)

        lbl_status = QLabel("🟢 Active on http://127.0.0.1:6801")
        lbl_status.setStyleSheet("color: #2ea043; font-weight: bold; font-size: 14px;")
        form.addRow("Local Listener:", lbl_status)

        self.chk_clipboard = QCheckBox("Monitor Clipboard for Magnet Links & Download URLs")
        self.chk_clipboard.setChecked(True)
        form.addRow("Clipboard Monitor:", self.chk_clipboard)

        self.chk_caffeinate = QCheckBox("Prevent Mac sleep during active downloads (caffeinate)")
        self.chk_caffeinate.setChecked(True)
        self.chk_caffeinate.toggled.connect(lambda v: setattr(self.caffeinate, "enabled", v))
        form.addRow("Power Management:", self.chk_caffeinate)

        layout.addWidget(grp_listener)

        # Extension Guide
        grp_ext = QGroupBox("Chrome / Brave / Arc Browser Extension")
        vbox_ext = QVBoxLayout(grp_ext)

        ext_folder = get_resource_path("extension")
        lbl_guide = QLabel(
            "<b>To catch downloads automatically from Chrome, Brave, Arc, or Edge:</b><br><br>"
            "1. Open <code>chrome://extensions</code> (or <code>brave://extensions</code>) in your browser.<br>"
            "2. Enable <b>'Developer mode'</b> toggle in the top-right.<br>"
            "3. Click <b>'Load unpacked'</b> and choose the following folder:<br>"
            f"<b><code>{ext_folder}</code></b><br><br>"
            "Once installed, clicking downloads for <code>.zip</code>, <code>.rar</code>, <code>.7z</code>, "
            f"<code>.iso</code>, <code>.dmg</code>, or split archives will automatically trigger {APP_NAME}!"
        )
        lbl_guide.setTextFormat(Qt.TextFormat.RichText)
        lbl_guide.setWordWrap(True)
        vbox_ext.addWidget(lbl_guide)

        btn_copy_ext_path = QPushButton("📋 Copy Extension Folder Path")
        btn_copy_ext_path.clicked.connect(lambda: QApplication.clipboard().setText(str(ext_folder)))
        vbox_ext.addWidget(btn_copy_ext_path)

        layout.addWidget(grp_ext)
        layout.addStretch()
        return widget

    # ── Status Bar & Tray Icon ──────────────────────────────────────────

    def _build_status_bar(self):
        sb = self.statusBar()
        self.lbl_global_speed = QLabel("↓ 0 B/s   ↑ 0 B/s")
        self.lbl_global_speed.setFont(QFont("Menlo", 11))
        sb.addPermanentWidget(self.lbl_global_speed)

        self.lbl_queue_status = QLabel("Active: 0   Waiting: 0")
        sb.addWidget(self.lbl_queue_status)

    def _build_tray_icon(self):
        self.tray = QSystemTrayIcon(self)
        pix = QPixmap(16, 16)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setBrush(QColor(0, 122, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 12, 12)
        p.end()

        self.tray.setIcon(QIcon(pix))
        self.tray.setToolTip(APP_NAME)

        menu = QMenu()
        act_show = menu.addAction(f"Show {APP_NAME}")
        act_show.triggered.connect(self._restore_from_tray)
        menu.addSeparator()

        act_p_all = menu.addAction("Pause All")
        act_p_all.triggered.connect(self.ctrl.pause_all)

        act_r_all = menu.addAction("Resume All")
        act_r_all.triggered.connect(self.ctrl.resume_all)

        menu.addSeparator()
        act_exit = menu.addAction("Quit")
        act_exit.triggered.connect(self.close)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()

    # ── Actions & Slots ─────────────────────────────────────────────────

    @pyqtSlot()
    def _on_add_download_dialog(self, initial_url: str = ""):
        dlg = AddDownloadDialog(self, default_dir=DEFAULT_DOWNLOAD_DIR, initial_text=initial_url)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.input_field.text().strip():
            url = dlg.input_field.text().strip()
            folder = dlg.dir_field.text().strip() or str(DEFAULT_DOWNLOAD_DIR)
            try:
                dl = self.ctrl.add(url, download_dir=folder)
                self.gids_order.append(dl.gid)
                self.statusBar().showMessage(f"Queued: {dl.name}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Add Failed", str(e))

    @pyqtSlot()
    def _on_open_torrent(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select .torrent File", "", "Torrent Files (*.torrent);;All (*)")
        if path:
            self._on_add_download_dialog(initial_url=path)

    def _selected_gids(self) -> list[str]:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        return [self.gids_order[r] for r in rows if r < len(self.gids_order)]

    @pyqtSlot()
    def _on_pause_selected(self):
        for gid in self._selected_gids():
            self.ctrl.pause(gid)

    @pyqtSlot()
    def _on_resume_selected(self):
        for gid in self._selected_gids():
            self.ctrl.resume(gid)

    @pyqtSlot()
    def _on_delete_selected(self):
        gids = self._selected_gids()
        if not gids:
            return
        if QMessageBox.question(self, "Delete", f"Remove {len(gids)} download(s)?") == QMessageBox.StandardButton.Yes:
            for gid in gids:
                try:
                    self.ctrl.cancel(gid)
                except Exception:
                    pass

    @pyqtSlot()
    def _on_reveal_selected(self):
        gids = self._selected_gids()
        if not gids:
            return
        stats = self.ctrl.get_stats(gids[0])
        if stats.files:
            reveal_in_finder(stats.files[0])
        else:
            reveal_in_finder(DEFAULT_DOWNLOAD_DIR)

    def _on_table_context_menu(self, pos):
        if not self._selected_gids():
            return
        m = QMenu(self)
        m.addAction("Pause", self._on_pause_selected)
        m.addAction("Resume", self._on_resume_selected)
        m.addSeparator()
        m.addAction("Reveal in Finder", self._on_reveal_selected)
        m.addAction("Delete", self._on_delete_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ── Multi-Part Archive Parser & Tree UI ─────────────────────────────

    @pyqtSlot()
    def _on_parse_repack_links(self):
        text = self.txt_repack_links.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Empty Input", "Please paste download links first.")
            return

        clusters = self.repack_mgr.parse_links_blob(text)
        if not clusters:
            QMessageBox.warning(self, "No Links Found", "Could not parse any valid links from input.")
            return

        added_groups = []
        for title, items in clusters.items():
            grp = self.repack_mgr.create_group(
                title=title,
                link_items=items,
                download_dir=DEFAULT_DOWNLOAD_DIR / title,
                auto_extract=self.chk_auto_extract.isChecked(),
                auto_verify=self.chk_auto_verify.isChecked(),
                sequential=self.chk_sequential.isChecked(),
            )
            self.repack_mgr.start_group(grp.group_id)
            added_groups.append(f"• {title} ({len(items)} parts)")

        self.txt_repack_links.clear()
        QMessageBox.information(
            self,
            "Archive Group(s) Queued",
            f"Successfully identified and queued:\n" + "\n".join(added_groups)
        )

    def _on_open_extracted_folder(self):
        item = self.tree_repacks.currentItem()
        if item:
            grp_id = item.data(0, Qt.ItemDataRole.UserRole)
            grp = self.repack_mgr.groups.get(grp_id)
            if grp and grp.extract_dir:
                reveal_in_finder(grp.extract_dir)
                return
        reveal_in_finder(DEFAULT_DOWNLOAD_DIR)

    # ── Stats Update Synchronization ────────────────────────────────────

    @pyqtSlot(list, dict)
    def _on_stats_updated(self, all_stats: list[DownloadStats], global_stats: dict):
        # 1. Track followed downloads from metadata tasks
        meta_replaced = set()
        for s in all_stats:
            if s.is_metadata and s.followed_by:
                for target_gid in s.followed_by:
                    meta_replaced.add(s.gid)
                    if s.gid in self.gids_order and target_gid not in self.gids_order:
                        idx = self.gids_order.index(s.gid)
                        self.gids_order[idx] = target_gid

        # 2. Update GID order list (ignore obsolete metadata tasks)
        existing_gids = set(self.gids_order)
        for s in all_stats:
            if s.gid in meta_replaced:
                continue
            if s.gid not in existing_gids:
                self.gids_order.append(s.gid)
                existing_gids.add(s.gid)

            # Check for completion notification (only real files, not metadata)
            if s.is_complete and not s.is_metadata and s.gid not in self.last_completed_gids:
                self.last_completed_gids.add(s.gid)
                send_macos_notification("Download Complete", s.name, APP_NAME)

        # Clean any removed metadata tasks from order
        self.gids_order = [g for g in self.gids_order if g not in meta_replaced]

        # 3. Update Table Rows
        lookup = {s.gid: s for s in all_stats}
        self.table.setRowCount(len(self.gids_order))

        for row, gid in enumerate(self.gids_order):
            s = lookup.get(gid)
            if not s:
                continue

            self._set_cell(row, COL_NAME, s.name)
            self._set_cell(row, COL_SIZE, "Fetching..." if (s.is_metadata and s.total_bytes == 0) else s.total_human)

            # Progress Bar Cell
            p_item = self.table.item(row, COL_PROGRESS)
            if not p_item:
                p_item = QTableWidgetItem()
                self.table.setItem(row, COL_PROGRESS, p_item)
            p_item.setData(Qt.ItemDataRole.UserRole, 0.0 if s.is_metadata else s.progress)

            self._set_cell(row, COL_SPEED, s.download_speed_human)
            self._set_cell(row, COL_ETA, "—" if s.is_metadata else s.eta_human)

            if s.is_metadata:
                peer_str = f"DHT:{s.num_peers} peers"
            elif s.num_peers > 0:
                peer_str = f"P:{s.num_peers} S:{s.num_seeders}"
            else:
                peer_str = f"{s.connections} conn"
            self._set_cell(row, COL_PEERS, peer_str)

            display_status = "Fetching Metadata" if s.is_metadata else s.status.capitalize()
            self._set_cell(row, COL_STATUS, display_status)

            # Color-code status
            status_item = self.table.item(row, COL_STATUS)
            if status_item:
                colors = {
                    "active": QColor(46, 160, 67),
                    "complete": QColor(46, 160, 67),
                    "fetching metadata": QColor(0, 122, 255),
                    "paused": QColor(210, 153, 34),
                    "waiting": QColor(130, 130, 130),
                    "error": QColor(218, 54, 51),
                }
                c = colors.get(s.status.lower(), QColor(130, 130, 130))
                status_item.setForeground(c)

        # 3. Update Status Bar & Tray
        dl_spd = DownloadStats._humanize_speed(global_stats.get("download_speed", 0))
        ul_spd = DownloadStats._humanize_speed(global_stats.get("upload_speed", 0))
        self.lbl_global_speed.setText(f"↓ {dl_spd}   ↑ {ul_spd}")
        self.lbl_queue_status.setText(
            f"Active: {global_stats.get('num_active', 0)}  Waiting: {global_stats.get('num_waiting', 0)}"
        )
        self.tray.setToolTip(f"{APP_NAME} — ↓ {dl_spd}")

        # 4. Sync Sleep Prevention (caffeinate)
        self.caffeinate.sync_with_download_count(global_stats.get("num_active", 0))

    @pyqtSlot()
    def _on_repack_updated(self):
        """Refreshes the Multi-Part Archive Group Tree."""
        current_groups = list(self.repack_mgr.groups.values())
        if self.tree_repacks.topLevelItemCount() != len(current_groups):
            self.tree_repacks.clear()
            for grp in current_groups:
                top = QTreeWidgetItem([grp.title, f"{grp.overall_progress:.1f}%", grp.status_message, str(grp.download_dir.name)])
                top.setData(0, Qt.ItemDataRole.UserRole, grp.group_id)
                for part in grp.parts:
                    child = QTreeWidgetItem([f"Part {part.part_num:02d}: {part.filename}", "100%" if part.completed else "Queued", "", ""])
                    top.addChild(child)
                self.tree_repacks.addTopLevelItem(top)
                top.setExpanded(True)
        else:
            for i, grp in enumerate(current_groups):
                top = self.tree_repacks.topLevelItem(i)
                if top:
                    top.setText(1, f"{grp.overall_progress:.1f}%")
                    top.setText(2, grp.status_message)

    # ── Browser Interception & Clipboard Monitoring ─────────────────────

    def _on_browser_interception(self, data: dict):
        """Called on background thread when Chrome extension sends a download."""
        url = data.get("url")
        if not url:
            return

        logger.info("Interception event received: %s", url)
        QTimer.singleShot(0, lambda: self._handle_browser_download(data))

    def _handle_browser_download(self, data: dict):
        url = data["url"]
        fname = data.get("filename")
        options = {}
        if data.get("cookies"):
            options["header"] = f"Cookie: {data['cookies']}"
        if data.get("referrer"):
            options["referer"] = data["referrer"]

        try:
            dl = self.ctrl.add(url, download_dir=DEFAULT_DOWNLOAD_DIR, options=options)
            self.gids_order.append(dl.gid)
            send_macos_notification("Caught from Browser", fname or dl.name, APP_NAME)
            self._restore_from_tray()
        except Exception as e:
            logger.error("Failed to add intercepted download: %s", e)

    def _check_clipboard(self):
        """Watches clipboard for magnet links or archive URLs."""
        if not self.chk_clipboard.isChecked():
            return

        cb = QApplication.clipboard()
        text = cb.text().strip()
        if not text or text == self.known_clipboard_text:
            return

        self.known_clipboard_text = text

        if text.startswith("magnet:?") or (text.startswith("http") and any(text.lower().endswith(ext) for ext in (".torrent", ".rar", ".zip", ".7z", ".iso", ".dmg", ".part1.rar"))):
            reply = QMessageBox.question(
                self,
                "Download Link Detected",
                f"A downloadable link was detected in your clipboard:\n\n{text[:120]}...\n\nWould you like to queue this download?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._on_add_download_dialog(initial_url=text)

    # ── Helpers & Cleanup ───────────────────────────────────────────────

    def _set_cell(self, row: int, col: int, text: str):
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, col, item)
        else:
            item.setText(text)

    def closeEvent(self, event):
        """Ensure completely safe, graceful shutdown of all background services."""
        try:
            if hasattr(self, "clipboard_timer"):
                self.clipboard_timer.stop()
        except Exception:
            pass

        try:
            if hasattr(self, "tray"):
                self.tray.hide()
        except Exception:
            pass

        try:
            if hasattr(self, "poller"):
                self.poller.stop()
        except Exception:
            pass

        try:
            if hasattr(self, "interceptor"):
                self.interceptor.stop()
        except Exception:
            pass

        try:
            if hasattr(self, "caffeinate"):
                self.caffeinate.stop()
        except Exception:
            pass

        try:
            if hasattr(self, "engine"):
                self.engine.stop()
        except Exception:
            pass

        event.accept()


# ════════════════════════════════════════════════════════════════════════
#  Main Entrypoint
# ════════════════════════════════════════════════════════════════════════

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("macOS")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
