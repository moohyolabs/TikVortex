"""Download page: input bar, current batch and the queue."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..engine import ATTENTION, Item, QueueManager, fmt_bytes
from ..theme import BAD, GREY, OK, WARN, Palette
from ..widgets import (Card, ConfirmDialog, Pill, SegGroup, StatusPill, Thumb, button, centred_pixmap, glass, initials, label, optical_centre)


def reveal(path: str) -> None:
    """Show a file in Explorer/Finder (or open its folder)."""
    import subprocess
    import sys
    if not path:
        return
    if sys.platform == "win32" and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif sys.platform == "darwin" and os.path.exists(path):
        subprocess.Popen(["open", "-R", path])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path) if os.path.isfile(path) else path))


def reveal_many(paths: list) -> None:
    """Show several files in Explorer with ALL of them selected (one window per folder). Explorer's
    /select switch only takes one file, so this uses the Windows shell call that takes a list."""
    import sys
    paths = [os.path.normpath(p) for p in paths if p and os.path.exists(p)]
    if not paths:
        return
    if sys.platform != "win32" or len(paths) == 1:
        reveal(paths[0]); return
    import ctypes
    from ctypes import wintypes
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p; shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
    ole32.CoInitialize(None)
    by_folder: dict = {}
    for p in paths:
        by_folder.setdefault(os.path.dirname(p), []).append(p)
    for folder, files in by_folder.items():
        parent = shell32.ILCreateFromPathW(folder)
        kids = [shell32.ILCreateFromPathW(f) for f in files]
        kids = [k for k in kids if k]
        try:
            if parent and kids:
                arr = (ctypes.c_void_p * len(kids))(*kids)
                if shell32.SHOpenFolderAndSelectItems(parent, len(kids), arr, 0) != 0:
                    reveal(files[0])
            else:
                reveal(files[0])
        finally:
            for k in kids:
                shell32.ILFree(k)
            if parent:
                shell32.ILFree(parent)


class QueueRow(QFrame):
    retry = Signal(object)
    remove = Signal(object)          # the X: clear this item (asks first if it's still downloading)
    trash = Signal(object)
    pause = Signal(object, bool)
    pressed = Signal(object, object)   # item, keyboard modifiers (click to select)
    menu = Signal(object, object)      # item, global position

    def __init__(self, it: Item, pal: Palette, covers):
        super().__init__(); self.setObjectName("row"); self.it = it; self.pal = pal; self.covers = covers
        self.setFixedHeight(80)
        h = QHBoxLayout(self); h.setContentsMargins(12, 11, 12, 11); h.setSpacing(12)
        self.thumb = Thumb(it.username or it.url, initials(it.username or "TV"), 46, 58); h.addWidget(self.thumb)
        mid = QVBoxLayout(); mid.setContentsMargins(0, 0, 0, 0); mid.setSpacing(0)
        top = QHBoxLayout(); top.setSpacing(8)
        self.name = QLabel(); self.name.setStyleSheet("font-weight: 800; font-size: 13px;"); top.addWidget(self.name)
        self.cap = label("", "mute"); self.cap.setMinimumWidth(10); self.cap.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.cap, 1)
        mid.addStretch(1); mid.addLayout(top); mid.addSpacing(5)
        self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setFixedHeight(7); mid.addWidget(self.bar)
        self.gap2 = mid.count(); mid.addSpacing(5)
        self.meta = label("", "mute", 11); self.meta.setTextFormat(Qt.RichText)
        f = self.meta.font(); f.setFeature(QFont.Tag("tnum"), 1); self.meta.setFont(f)
        mid.addWidget(self.meta); mid.addStretch(1)
        h.addLayout(mid, 1)
        self.btn_retry = button("Retry original", "ghost", "ph.arrow-clockwise-bold", pal); self.btn_retry.clicked.connect(lambda: self.retry.emit(self.it))

        def small(ic, tip, fn):
            b = button("", "ghost", ic, pal); b.setFixedSize(34, 32); b.setToolTip(tip); b.clicked.connect(fn); return b
        self.btn_pause = small("ph.pause-fill", "Pause", lambda: self.pause.emit(self.it, not self.it._paused))
        self.btn_play = small("ph.play-fill", "Play", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.it.path)))
        self.btn_folder = small("ph.folder-simple-fill", "Show in folder", lambda: reveal(self.it.path or ""))
        self.btn_link = small("ph.link-simple-bold", "Copy link", lambda: QGuiApplication.clipboard().setText(self.it.link))
        self.btn_trash = small("ph.trash-fill", "Move to Recycle Bin", lambda: self.trash.emit(self.it))
        for b in (self.btn_retry, self.btn_pause, self.btn_play, self.btn_folder, self.btn_link, self.btn_trash):
            h.addWidget(b, 0, Qt.AlignVCenter)
        self.pill = StatusPill(it.state); h.addWidget(self.pill, 0, Qt.AlignVCenter)
        self.btn_x = small("ph.x-bold", "Clear from the queue", lambda: self.remove.emit(self.it)); h.addWidget(self.btn_x, 0, Qt.AlignVCenter)
        self.setContextMenuPolicy(Qt.CustomContextMenu); self.customContextMenuRequested.connect(self._menu)
        self.refresh()

    def refresh(self) -> None:
        it = self.it
        self.name.setText("@" + it.username if it.username else (it.vid or "post"))
        self.cap.setText(f"“{it.caption}”" if it.caption else "")
        self.pill.set_state("Paused" if it._paused and not it.terminal else it.state)
        active = it.state in ("Downloading", "Waiting for TikWM", "Queued", "Paused")
        self.bar.setVisible(active)
        if it.state == "Downloading" and it.total:
            self.bar.setRange(0, 1000); self.bar.setValue(int(1000 * it.done / max(1, it.total)))
        elif it.state == "Waiting for TikWM":
            self.bar.setRange(0, 0)                       # animated "working" bar
        else:
            self.bar.setRange(0, 1000); self.bar.setValue(0 if active else 1000)
        if it.state == "Downloading" and not it.done:
            info = "starting download…"
        elif it.state == "Downloading":
            info = f"{fmt_bytes(it.done)} of {fmt_bytes(it.total)}" if it.total else fmt_bytes(it.done)
            if it.speed:
                info += f" · {fmt_bytes(it.speed)}/s"
        elif it.state == "Waiting for TikWM":
            info = f"preparing the original file… {int(time.monotonic() - it._t)} s" if it._t else "preparing…"
        else:
            info = it.info or ("queued" if it.state == "Queued" else "")
        if it._paused and not it.terminal:
            info = (f"paused at {fmt_bytes(it.done)} of {fmt_bytes(it.total)}" if it.done and it.total
                    else ("paused - waits for TikWM, then stops" if it.state == "Waiting for TikWM" else "paused"))
        vid = f" &nbsp;·&nbsp; <span style='color:{self.pal.faded}'>{it.vid}</span>" if it.vid else ""
        self.meta.setText(f"{info}{vid}")
        attention = it.state in ATTENTION
        have = bool(it.path) and it.state in ("Original", "Compressed", "Already saved")
        self.btn_retry.setVisible(attention)
        self.btn_pause.setVisible(not it.terminal)
        paused = it._paused or it.state == "Paused"
        if paused != getattr(self, "_shown_paused", None):        # pause <-> play, depending on what it'll do
            from PySide6.QtGui import QIcon
            self._shown_paused = paused
            self.btn_pause.setIcon(QIcon(centred_pixmap("ph.play-fill" if paused else "ph.pause-fill", self.pal.icon, 15)))
            self.btn_pause.setToolTip("Resume" if paused else "Pause")
        self.btn_play.setVisible(have and it.kind != "photo")
        self.btn_folder.setVisible(have)
        self.btn_link.setVisible(True)
        self.btn_trash.setVisible(have)
        if it.cover and self.thumb.pix is None:
            self.covers.request(it.cover, self.thumb.set_pixmap)

    def _menu(self, pos) -> None:
        self.menu.emit(self.it, self.mapToGlobal(pos))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pressed.emit(self.it, e.modifiers())
        super().mousePressEvent(e)

    def set_selected(self, on: bool) -> None:
        if bool(self.property("sel")) != on:
            self.setProperty("sel", on); self.style().unpolish(self); self.style().polish(self)


class DownloadPage(QWidget):
    title = "DOWNLOAD"
    subtitle = "Paste profiles, links or handles to queue"
    clipboardToggled = Signal(bool)

    def __init__(self, qm: QueueManager, pal: Palette, covers, store):
        super().__init__(); self.qm, self.pal, self.covers, self.store = qm, pal, covers, store
        self.rows: dict[int, QueueRow] = {}
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)

        # input card
        card = Card(); r = QHBoxLayout(); r.setSpacing(12)
        self.field = glass("field"); fh = QHBoxLayout(self.field); fh.setContentsMargins(6, 4, 6, 4); fh.setSpacing(6)
        self.edit = QLineEdit(); self.edit.setObjectName("bare"); self.edit.setMinimumHeight(40)
        self.edit.setPlaceholderText("Paste links, post IDs, @users, a group name - or drop a .txt here")
        self.edit.returnPressed.connect(self.submit); fh.addWidget(self.edit, 1)
        self.txt = button("Open .txt", "ghost", "ph.file-text-fill", pal); self.txt.setFixedHeight(32)
        self.txt.setToolTip("Queue every link in a .txt file"); self.txt.clicked.connect(self._open_txt)
        fh.addWidget(self.txt, 0, Qt.AlignVCenter)
        self.clip = button("Clipboard monitor", "ghost", "ph.clipboard-text-fill", pal, checkable=True); self.clip.setFixedHeight(32)
        self.clip.setToolTip("Clipboard monitor: TikTok links you copy anywhere are added automatically")
        self.clip.toggled.connect(self._clip_toggled); fh.addWidget(self.clip, 0, Qt.AlignVCenter)
        self.clip.setText(" Clipboard monitor  ●"); self.clip.ensurePolished()
        self.clip.setFixedWidth(self.clip.sizeHint().width() + 4)   # same width on and off, so nothing shifts
        self.force = button("Force", "ghost", "ph.arrow-clockwise-bold", pal, checkable=True); self.force.setFixedHeight(32)
        self.force.setToolTip("Force: re-download even if you already have it (replaces the file, keeps its date and folder)")
        self.force.toggled.connect(lambda on: self._recolor()); fh.addWidget(self.force, 0, Qt.AlignVCenter)
        self.force.ensurePolished(); self.force.setFixedWidth(self.force.sizeHint().width() + 4)   # same size on and off
        r.addWidget(self.field, 1)
        self.go = button("Download", "primary", "mdi6.download", pal); self.go.setMinimumWidth(128); self.go.clicked.connect(self.submit)
        r.addWidget(self.go, 0, Qt.AlignVCenter)
        card.v.addLayout(r); v.addWidget(card)

        # batch card
        self.batch = Card(margins=(20, 14, 20, 14), spacing=12)
        bh = QHBoxLayout(); bh.setSpacing(10)
        self.b_title = QLabel(); self.b_title.setObjectName("h2"); bh.addWidget(self.b_title, 0, Qt.AlignVCenter)
        self.b_stats = label("", "mute"); f = self.b_stats.font(); f.setFeature(QFont.Tag("tnum"), 1); self.b_stats.setFont(f)
        bh.addWidget(self.b_stats, 0, Qt.AlignVCenter); bh.addStretch()
        self.b_pause = button("Pause", "ghost", "ph.pause-fill", pal); self.b_pause.clicked.connect(self._pause)
        self.b_stop = button("Stop", "ghost", "ph.stop-fill", pal); self.b_stop.clicked.connect(self.qm.stop)
        bh.addWidget(self.b_pause, 0, Qt.AlignVCenter); bh.addWidget(self.b_stop, 0, Qt.AlignVCenter)
        self.batch.v.addLayout(bh)
        self.b_bar = QProgressBar(); self.b_bar.setTextVisible(False); self.b_bar.setFixedHeight(10); self.batch.v.addWidget(self.b_bar)
        ch = QHBoxLayout(); ch.setSpacing(8)
        self.c_orig = Pill("0 original", OK); self.c_comp = Pill("0 compressed", WARN); self.c_fail = Pill("0 failed", BAD); self.c_left = Pill("0 to go", GREY)
        for p in (self.c_orig, self.c_comp, self.c_fail, self.c_left):
            ch.addWidget(p)
        ch.addStretch()
        self.b_note = label("Compressed ones are retried automatically at the end", "mute", 11); ch.addWidget(self.b_note)
        self.batch.v.addLayout(ch); v.addWidget(self.batch)
        for lab in (self.b_title, self.b_stats, self.b_note):          # capitals centred on the row, not the text box
            optical_centre(lab)

        # queue card
        self.qcard = Card(margins=(20, 14, 20, 16), spacing=13)
        qh = QHBoxLayout(); qh.setSpacing(12)
        ql = QLabel("QUEUE"); ql.setObjectName("h2"); qh.addWidget(ql, 0, Qt.AlignVCenter); optical_centre(ql)
        self.filter = SegGroup(["All", "Active", "Done", "Needs attention"], store.ui("queue_filter"))
        self.filter.changed.connect(self._filter_changed); qh.addWidget(self.filter, 0, Qt.AlignVCenter); qh.addStretch()
        self.retry_all = button("Retry all compressed", "ghost", "ph.arrow-clockwise-bold", pal)
        self.retry_all.clicked.connect(self.qm.retry_all_compressed)
        self.clear = button("Clear finished", "ghost"); self.clear.clicked.connect(self.qm.clear_finished)
        qh.addWidget(self.retry_all, 0, Qt.AlignVCenter); qh.addWidget(self.clear, 0, Qt.AlignVCenter)
        self.qcard.v.addLayout(qh)
        self.sel: set = set(); self._anchor = None
        self.selbar = Card("row", margins=(14, 6, 8, 6)); sb = QHBoxLayout(); sb.setSpacing(8)
        self.s_lbl = QLabel(""); self.s_lbl.setFixedWidth(110); self.s_lbl.setStyleSheet(f"font-weight: 900; font-size: 13px; color: {pal.nav_icon};")
        sb.addWidget(self.s_lbl)
        hint = label("Ctrl+click to add · Shift+click for a range · Ctrl+A for all · Del to clear", "mute", 11)
        hint.setMinimumWidth(1); hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred); sb.addWidget(hint, 1)

        def sbtn(t, ic, what):
            b = button(t, "ghost", ic, pal); b.clicked.connect(lambda: self._act(what)); sb.addWidget(b); return b
        self.s_pause = sbtn("Pause", "ph.pause-fill", "pause"); self.s_resume = sbtn("Resume", "ph.play-fill", "resume")
        self.s_retry = sbtn("Re-download", "ph.arrow-clockwise-bold", "retry"); sbtn("Copy links", "ph.link-simple-bold", "links")
        self.s_trash = button("Recycle bin", "danger"); self.s_trash.clicked.connect(lambda: self._act("trash")); sb.addWidget(self.s_trash)
        sbtn("Clear", "ph.x-circle-fill", "clear")
        x = button("", "ghost", "ph.x-bold", pal); x.setFixedWidth(34); x.setToolTip("Clear selection"); x.clicked.connect(self.clear_selection); sb.addWidget(x)
        self.selbar.v.addLayout(sb); self.selbar.hide(); self.qcard.v.addWidget(self.selbar)
        # A list view, not one tall widget holding every row: with hundreds of posts that widget grew past
        # Windows' 32,767-pixel limit and the rows were drawn squashed on top of each other. The list only
        # places the rows you can see.
        from PySide6.QtWidgets import QAbstractItemView, QListWidget
        self.scroll = QListWidget(); self.scroll.setFrameShape(QFrame.NoFrame); self.scroll.setSelectionMode(QAbstractItemView.NoSelection)
        self.scroll.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel); self.scroll.verticalScrollBar().setSingleStep(24)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.scroll.setSpacing(0)
        self.scroll.setStyleSheet("QListWidget { background: transparent; border: none; } QListWidget::item { background: transparent; border: none; padding: 0; }")
        self.list_w = self.scroll; self._li: dict = {}
        self.empty = label("Nothing in the queue yet.\nPaste a TikTok link above, or press Ctrl+V anywhere.", "empty")
        self.empty.setAlignment(Qt.AlignCenter); self.empty.setMinimumHeight(160)
        self.qcard.v.addWidget(self.scroll, 1); self.qcard.v.addWidget(self.empty, 1); self.scroll.hide()
        self.list_w.setFocusPolicy(Qt.ClickFocus)
        from PySide6.QtGui import QKeySequence, QShortcut
        for keys, fn in (("Ctrl+A", self.select_all), ("Escape", self.clear_selection), ("Delete", lambda: self._act("clear"))):
            sc = QShortcut(QKeySequence(keys), self.scroll); sc.setContext(Qt.WidgetWithChildrenShortcut); sc.activated.connect(fn)
        v.addWidget(self.qcard, 1)

        qm.itemAdded.connect(self._added); qm.itemChanged.connect(self._changed); qm.itemsRemoved.connect(self._removed)
        qm.batchChanged.connect(self._batch_changed); qm.activeChanged.connect(lambda n: self._update_counts())
        self._tick = QTimer(self); self._tick.timeout.connect(self._tick_waiting); self._tick.start(1000)
        self._files_timer = QTimer(self); self._files_timer.timeout.connect(self._check_files); self._files_timer.start(3000)
        self._counts_timer = QTimer(self); self._counts_timer.setSingleShot(True); self._counts_timer.setInterval(150)
        self._counts_timer.timeout.connect(self._update_counts)
        self._recolor(); self._update_counts(); self._batch_changed(None)

    # ---------------------------------------------------------- actions
    def submit(self) -> None:
        text = self.edit.text().strip()
        if not text:
            text = QGuiApplication.clipboard().text().strip()
        if not text:
            return
        # (names are remembered once their posts are actually in the library - not on typing, so typos never stick)
        if self.qm.submit(text, force=self.force.isChecked(), bare_names=True):      # "sunny.studio" works without the @
            self.edit.clear(); self.force.setChecked(False)

    def _open_txt(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Open a list of links", "", "Text files (*.txt *.url);;All files (*)")
        if path:
            self.qm.submit(f'"{path}"', force=self.force.isChecked())

    def _clip_toggled(self, on: bool) -> None:
        self._recolor(); self.clipboardToggled.emit(on)

    def _pause(self) -> None:
        self.qm.pause(not self.qm.paused)
        for row in self.rows.values():
            row.refresh()

    def _recolor(self) -> None:
        for b, ic in ((self.clip, "ph.clipboard-text-fill"), (self.force, "ph.arrow-clockwise-bold")):
            from PySide6.QtGui import QIcon
            b.setIcon(QIcon(centred_pixmap(ic, "white" if b.isChecked() else self.pal.icon, 15)))
        self.clip.setText(" Clipboard monitor  ●" if self.clip.isChecked() else " Clipboard monitor")

    # ---------------------------------------------------------- queue rows
    def _added(self, it: Item) -> None:
        row = QueueRow(it, self.pal, self.covers)
        row.retry.connect(self.qm.retry); row.remove.connect(self._clear_item)
        row.trash.connect(self._trash_item); row.pause.connect(self.qm.pause_item)
        row.pressed.connect(self._row_pressed); row.menu.connect(self._row_menu)
        self.rows[it.id] = row
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QListWidgetItem
        holder = QWidget(); hl = QVBoxLayout(holder); hl.setContentsMargins(0, 0, 6, 10); hl.addWidget(row)   # 10 px between rows
        li = QListWidgetItem(); li.setSizeHint(QSize(0, row.height() + 10)); li.setFlags(Qt.ItemIsEnabled)
        self.scroll.addItem(li); self.scroll.setItemWidget(li, holder); self._li[it.id] = li
        self.empty.hide(); self.scroll.show(); self._apply_filter(row); self._counts_timer.start()

    def _changed(self, it: Item) -> None:
        row = self.rows.get(it.id)
        if row:
            row.refresh(); self._apply_filter(row)
        self._counts_timer.start()                # many items change at once: count them once

    def _removed(self, ids: list) -> None:
        self.sel -= set(ids)
        for i in ids:
            row = self.rows.pop(i, None); li = self._li.pop(i, None)
            if li is not None:
                self.scroll.takeItem(self.scroll.row(li))
            if row:
                row.deleteLater()
        self.empty.setVisible(not self.rows); self.scroll.setVisible(bool(self.rows)); self._update_counts(); self._sync_sel()

    def _clear_item(self, it: Item) -> None:
        if not it.terminal and it.state not in ("Queued", "Paused") and not ConfirmDialog.ask(
                self, "Stop this download?", "It's still downloading. Clearing it stops the download and removes the unfinished file.",
                self.pal, confirm_text="Stop and clear", cancel_text="Keep it", danger=True):
            return
        self.qm.cancel(it)

    def _trash_item(self, it: Item) -> None:
        self._trash_items([it])

    def _trash_items(self, items: list) -> None:
        from tikvortex.core.download import find_all_downloads
        files = []
        for it in items:
            fs = (find_all_downloads(self.qm.output_dir, it.vid) if it.vid else []) or ([it.path] if it.path else [])
            files += [f for f in fs if os.path.isfile(f)]
        if not files:
            for it in items:
                self.qm.mark_deleted(it)
            return
        lib = getattr(self.window(), "library", None)
        if lib is not None:
            lib.stop_preview()                        # a hover preview would keep the file open
        what = "this post" if len(items) == 1 else f"{len(items)} posts"
        if not ConfirmDialog.ask(self, "Move to Recycle Bin",
                                 f"Move {what} ({len(files)} file(s)) to the Recycle Bin?\nYou can restore them from there.",
                                 self.pal, confirm_text="Yes", cancel_text="No", danger=True):
            return
        from PySide6.QtCore import QFile
        failed = 0
        for f in files:
            ok = QFile.moveToTrash(f)
            failed += not (ok[0] if isinstance(ok, tuple) else ok)
        if failed:
            ConfirmDialog.info(self, "Recycle Bin", f"{failed} file(s) couldn't be moved.", self.pal)
        self._check_files()

    def _check_files(self) -> None:
        """Notice files deleted outside the app too (e.g. from the folder), and mark them in the queue."""
        for row in list(self.rows.values()):
            it = row.it
            if it.state in ("Original", "Compressed", "Already saved") and it.path and not os.path.exists(it.path):
                self.qm.mark_deleted(it)

    # ---------------------------------------------------------- selecting queue items
    def _visible_rows(self) -> list:
        out = []
        for k in range(self.scroll.count()):
            li = self.scroll.item(k)
            if not li.isHidden():
                row = self.scroll.itemWidget(li).findChild(QueueRow)
                if row is not None:
                    out.append(row)
        return out

    def _row_pressed(self, it: Item, mods) -> None:
        """Click selects; Ctrl+click adds or removes; Shift+click selects a range."""
        self.list_w.setFocus()
        ids = [r.it.id for r in self._visible_rows()]
        if mods & Qt.ShiftModifier and self._anchor in ids and it.id in ids:
            a, b = ids.index(self._anchor), ids.index(it.id)
            rng = set(ids[min(a, b): max(a, b) + 1])
            self.sel = (self.sel | rng) if mods & Qt.ControlModifier else rng
        elif mods & Qt.ControlModifier:
            self.sel ^= {it.id}; self._anchor = it.id
        else:
            self.sel = set() if self.sel == {it.id} else {it.id}; self._anchor = it.id
        self._sync_sel()

    def select_all(self) -> None:
        self.sel = {r.it.id for r in self._visible_rows()}; self._sync_sel()

    def clear_selection(self) -> None:
        self.sel = set(); self._sync_sel()

    def _sel_items(self) -> list:
        return [r.it for r in self._visible_rows() if r.it.id in self.sel]

    def _sync_sel(self) -> None:
        if not hasattr(self, "selbar"):
            return
        for i, row in self.rows.items():
            row.set_selected(i in self.sel)
        items = self._sel_items(); n = len(items)
        self.selbar.setVisible(n > 0)
        if n:
            act = [i for i in items if not i.terminal]
            self.s_lbl.setText(f"{n} selected")
            self.s_pause.setVisible(any(not i._paused for i in act)); self.s_resume.setVisible(any(i._paused for i in act))
            self.s_retry.setVisible(any(i.terminal for i in items))
            self.s_trash.setVisible(any(i.path and i.state in ("Original", "Compressed", "Already saved") for i in items))

    def _act(self, what: str, items=None) -> None:
        items = items if items is not None else self._sel_items()
        if not items:
            return
        if what == "pause":
            for i in items:
                self.qm.pause_item(i, True)
        elif what == "resume":
            for i in items:
                self.qm.pause_item(i, False)
        elif what == "retry":
            for i in items:
                if i.terminal:
                    self.qm.retry(i)
        elif what == "links":
            QGuiApplication.clipboard().setText("\n".join(i.link for i in items))
        elif what == "trash":
            self._trash_items([i for i in items if i.path and i.state in ("Original", "Compressed", "Already saved")])
        elif what == "clear":
            running = [i for i in items if not i.terminal and i.state not in ("Queued", "Paused") and not i._paused]
            if running and not ConfirmDialog.ask(
                    self, "Stop these downloads?",
                    f"{len(running)} of them {'is' if len(running) == 1 else 'are'} still downloading. Clearing stops them and removes the unfinished files.",
                    self.pal, confirm_text="Stop and clear", cancel_text="Keep them", danger=True):
                return
            for i in items:
                self.qm.cancel(i)
        self._sync_sel()

    def _row_menu(self, it: Item, at) -> None:
        from ..widgets import add_action, styled_menu
        if it.id not in self.sel:
            self.sel = {it.id}; self._anchor = it.id; self._sync_sel()
        items = self._sel_items() or [it]; one = len(items) == 1
        m = styled_menu(self)
        have = bool(it.path) and it.state in ("Original", "Compressed", "Already saved")
        if one and have:
            if it.kind != "photo":
                add_action(m, "Play", "ph.play-fill", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(it.path)))
            add_action(m, "Show in folder", "ph.folder-simple-fill", lambda: reveal(it.path))
        add_action(m, "Copy link" if one else f"Copy {len(items)} links", "ph.link-simple-bold", lambda: self._act("links", items))
        if one and it.vid:
            add_action(m, "Copy post ID", "ph.hash-bold", lambda: QGuiApplication.clipboard().setText(it.vid))
        m.addSeparator()
        act = [i for i in items if not i.terminal]
        if any(not i._paused for i in act):
            add_action(m, "Pause", "ph.pause-fill", lambda: self._act("pause", items))
        if any(i._paused for i in act):
            add_action(m, "Resume", "ph.play-fill", lambda: self._act("resume", items))
        if any(i.terminal for i in items):
            add_action(m, "Re-download in original quality", "ph.arrow-clockwise-bold", lambda: self._act("retry", items))
        if any(i.path and i.state in ("Original", "Compressed", "Already saved") for i in items):
            add_action(m, "Move to Recycle Bin", "ph.trash-fill", lambda: self._act("trash", items))
        add_action(m, "Clear from the queue", "ph.x-bold", lambda: self._act("clear", items))
        m.addSeparator()
        add_action(m, "Select all", "ph.checks-bold", self.select_all)
        m.exec(at)

    def _filter_changed(self, f: str) -> None:
        self.store.set_ui("queue_filter", f)
        for row in self.rows.values():
            self._apply_filter(row)

    def _apply_filter(self, row: QueueRow) -> None:
        f = self.filter.value; s = row.it.state
        show = (f == "All" or (f == "Active" and not row.it.terminal) or (f == "Done" and s in ("Original", "Already saved"))
                or (f == "Needs attention" and s in ATTENTION))
        li = self._li.get(row.it.id)
        if li is not None and li.isHidden() == show:
            li.setHidden(not show)

    def _update_counts(self) -> None:
        items = [r.it for r in self.rows.values()]
        n = len(items); act = sum(1 for i in items if not i.terminal)
        done = sum(1 for i in items if i.state in ("Original", "Already saved")); att = sum(1 for i in items if i.state in ATTENTION)
        for k, v in (("All", n), ("Active", act), ("Done", done), ("Needs attention", att)):
            self.filter.set_count(k, v)
        self.retry_all.setEnabled(att > 0); self.clear.setEnabled(n - act > 0)

    def _tick_waiting(self) -> None:
        for row in self.rows.values():
            if row.it.state == "Waiting for TikWM" and row.isVisible():
                row.refresh()

    def _batch_changed(self, b) -> None:
        cur = self.qm.current
        if cur is None:
            self.b_title.setText("DOWNLOAD STATUS"); self.b_stats.setText("Nothing downloading yet")
            self.b_bar.setRange(0, 1000); self.b_bar.setValue(0)
            for p_, t in ((self.c_orig, "0 original"), (self.c_comp, "0 compressed"), (self.c_fail, "0 failed"), (self.c_left, "0 to go")):
                p_.setText(t)
            self.b_pause.setEnabled(False); self.b_stop.setEnabled(False)
            self.b_note.setVisible(self.qm.cfg.retry_compressed_at_end)
            return
        c = cur.counts(self.qm.items)
        self.batch.setVisible(True)
        self.b_title.setText(f"DOWNLOAD STATUS  ·  {cur.label}")
        total = max(1, c["total"]); finished = total - c["left"]
        # bytes in flight for a smoother bar
        frac = 0.0
        for i in cur.items:
            it = self.qm.items.get(i)
            if it and it.state == "Downloading" and it.total:
                frac += it.done / it.total
        self.b_bar.setRange(0, 1000)
        self.b_bar.setValue(int(1000 * (finished + frac) / total) if c["total"] else 0)
        speed = sum(self.qm.items[i].speed for i in cur.items if i in self.qm.items and self.qm.items[i].state == "Downloading")
        bits = [f"{finished} of {c['total']}"] if c["total"] else ["listing posts…" if cur.listing else "waiting"]
        if speed:
            bits.append(f"{fmt_bytes(speed)}/s")
        el = time.time() - cur.started
        if finished and c["left"] and not cur.finished:
            eta = el / finished * c["left"]
            bits.append(f"about {int(eta // 60)} min left" if eta >= 90 else ("almost done" if eta < 5 else f"about {int(eta)} s left"))
        bits.append(f"{self.qm.cfg.download_workers} worker" + ("s" if self.qm.cfg.download_workers != 1 else ""))
        if self.qm.paused:
            bits.append("paused")
        if cur.finished:
            bits = [f"finished · {c['total']} post" + ("s" if c["total"] != 1 else "")]
        self.b_stats.setText("  ·  ".join(bits))
        self.c_orig.setText(f"{c['Original'] + c['Already saved']} original"); self.c_comp.setText(f"{c['Compressed']} compressed")
        self.c_fail.setText(f"{c['Failed']} failed"); self.c_left.setText(f"{c['left']} to go")
        self.b_pause.setText(" Resume" if self.qm.paused else " Pause")
        if self.qm.paused != getattr(self, "_b_paused", None):           # play icon when it will resume
            from PySide6.QtGui import QIcon
            self._b_paused = self.qm.paused
            self.b_pause.setIcon(QIcon(centred_pixmap("ph.play-fill" if self.qm.paused else "ph.pause-fill", self.pal.icon, 15)))
        self.b_pause.setEnabled(not cur.finished); self.b_stop.setEnabled(not cur.finished)
        self.b_note.setVisible(self.qm.cfg.retry_compressed_at_end)

    def restyle(self, pal: Palette) -> None:
        self.pal = pal
        for row in self.rows.values():
            row.pal = pal; row.refresh()
        self._recolor()
