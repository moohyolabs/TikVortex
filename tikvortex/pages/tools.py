"""Tools page: re-order dates, fix dates, force re-downloads, creator groups and maintenance."""
from __future__ import annotations

import builtins
import os
import threading

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QComboBox, QSizePolicy, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget)

from tikvortex.core.bulk import reorder_targets
from tikvortex.core.media_dates import fix_existing, reorder_by_post

from ..engine import set_sink
from ..theme import Palette
from ..widgets import (Card, ConfirmDialog, DateTimePicker, EmptyBox, GroupRow, OptionCard, OptionSet,
                       StyledComboBox, button, glass, label)

_input_lock = threading.Lock()


def run_captured(fn, done, answer: str = "n"):
    """Run an engine function in a thread, collecting what it prints (and answering any prompt)."""
    lines: list[str] = []

    def work():
        set_sink(lines.append)
        with _input_lock:
            real = builtins.input
            builtins.input = lambda prompt="": (print(prompt), answer)[1]
            try:
                fn()
            except Exception as e:
                print(f"Error: {e}")
            finally:
                builtins.input = real
                set_sink(None)
        done("\n".join(lines))
    threading.Thread(target=work, daemon=True).start()


class Output(QPlainTextEdit):
    def __init__(self):
        super().__init__(); self.setReadOnly(True); self.setMinimumHeight(120); self.setMaximumHeight(220)
        f = QFont("Consolas"); f.setStyleHint(QFont.Monospace); f.setPixelSize(12); self.setFont(f)
        self.setPlaceholderText("Results appear here")


class ToolsPage(QWidget):
    title = "TOOLS"
    subtitle = "Dates, re-downloads and creator groups"
    _done = Signal(object, str)
    groupsChanged = Signal()
    notify = Signal(str, str)

    def __init__(self, qm, pal: Palette, store, base_dir: str):
        super().__init__(); self.qm, self.pal, self.store, self.base_dir = qm, pal, store, base_dir
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea(); sc.setWidgetResizable(True); outer.addWidget(sc)
        body = QWidget(); sc.setWidget(body)
        grid = QGridLayout(body); grid.setContentsMargins(0, 0, 6, 0); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        self._done.connect(lambda out, text: out.setPlainText(text))
        self.options: list[OptionCard] = []

        # ---- re-order (full width: settings on the left, results on the right)
        c = Card(); c.v.addWidget(self._h("RE-ORDER BY POST DATE"))
        row = QHBoxLayout(); row.setSpacing(18); c.v.addLayout(row, 1)
        left = QVBoxLayout(); left.setSpacing(12); row.addLayout(left, 11)
        w = label("Re-dates files so a folder or creator group runs oldest post to newest, one second apart, "
                  "so photo apps show them in the order they were posted.", "mute"); w.setWordWrap(True); left.addWidget(w)
        self.target = self._target_box(); left.addLayout(self._labelled("Folder or group", self.target))
        self._start_section(left, pal)
        left.addStretch(1)
        r = self._pick_row                                   # [date][time][Now]  [Preview][Apply] on one line
        pv = button("Preview", "ghost", "ph.eye-fill", pal); pv.setFixedHeight(42); pv.setStyleSheet("font-size: 13px; font-weight: 800;"); pv.clicked.connect(lambda: self._reorder(False)); r.addWidget(pv)
        self.apply = button("Apply", "primary"); self.apply.setFixedHeight(42); self.apply.setEnabled(False); self.apply.setToolTip("Preview first, then apply")
        self.apply.clicked.connect(lambda: self._reorder(True)); r.addWidget(self.apply)
        self.out_reorder = Output(); self.out_reorder.setMaximumHeight(16777215)
        self.out_reorder.setPlaceholderText("Press Preview to see the new dates before anything changes")
        row.addWidget(self.out_reorder, 9)
        self.target.currentIndexChanged.connect(lambda *_: self.apply.setEnabled(False))
        grid.addWidget(c, 0, 0, 1, 2)

        # ---- fix dates
        c = Card(); c.v.addWidget(self._h("FIX DATES ON OLDER DOWNLOADS"))
        w = label("Writes dates into files saved before TikVortex did it automatically.", "mute"); w.setWordWrap(True); c.v.addWidget(w)
        self.fix_target = self._target_box("All downloads"); c.v.addLayout(self._labelled("Folder or group", self.fix_target))
        boxes = QHBoxLayout(); boxes.setSpacing(10)
        dl = OptionCard("Download time", "Keeps the order you downloaded them in.", pal)
        po = OptionCard("Post time", "Uses the date each post went up on TikTok.", pal)
        boxes.addWidget(dl, 1); boxes.addWidget(po, 1); c.v.addLayout(boxes); self.options += [dl, po]
        self.fix_mode = OptionSet({"downloaded": dl, "posted": po}, qm.cfg.media_dates if qm.cfg.media_dates in ("downloaded", "posted") else "downloaded")
        self.out_fix = Output(); self.out_fix.setMaximumHeight(16777215); c.v.addWidget(self.out_fix, 1)
        r = QHBoxLayout(); r.addStretch(); run = button("Fix dates", "primary"); run.clicked.connect(self._fix); r.addWidget(run); r.addStretch(); c.v.addLayout(r)
        grid.addWidget(c, 1, 0)

        # ---- creator groups
        c = Card(); c.v.addWidget(self._h("CREATOR GROUPS"))
        w = label("One creator with several accounts. A group downloads as one timeline into one folder.", "mute"); w.setWordWrap(True); c.v.addWidget(w)
        r = QHBoxLayout(); r.setSpacing(14)
        lv = QVBoxLayout(); lv.setSpacing(4)
        self.group_list = QVBoxLayout(); self.group_list.setSpacing(4); lv.addLayout(self.group_list)
        self.new_box = EmptyBox("+  New group", pal); self.new_box.setFixedHeight(46); self.new_box.setCursor(Qt.PointingHandCursor)
        self.new_box.clicked.connect(self._new_group); lv.addWidget(self.new_box); lv.addStretch(1)
        lw = QWidget(); lw.setLayout(lv); lv.setContentsMargins(0, 0, 0, 0)
        lw.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred); r.addWidget(lw, 10)   # widths come from the 10:11 split only
        ed = glass("row"); ev = QVBoxLayout(ed); ev.setContentsMargins(14, 14, 14, 14); ev.setSpacing(8)
        self.ed_title = label("NEW GROUP", "heading"); ev.addWidget(self.ed_title)
        self.g_name = QLineEdit(); self.g_name.setPlaceholderText("Group name (= folder name)"); ev.addWidget(self.g_name)
        ev.addSpacing(4); ev.addWidget(label("GROUP PICTURE", "heading"))
        pics = QWidget(); pics.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed); pics.setMinimumWidth(1)
        self.pic_row = QHBoxLayout(pics); self.pic_row.setContentsMargins(0, 0, 0, 0); self.pic_row.setSpacing(10)
        ev.addWidget(pics)                                        # never widens the editor, however many accounts
        self.pic_choice = ""
        self.g_accs = QPlainTextEdit(); self.g_accs.setPlaceholderText("@account_one\n@account_two"); self.g_accs.setMinimumHeight(80); ev.addWidget(self.g_accs, 1)
        self._pics_timer = QTimer(self); self._pics_timer.setSingleShot(True); self._pics_timer.setInterval(400)
        self._pics_timer.timeout.connect(self._fill_pics)       # rebuild the picture choices once typing pauses
        self.g_accs.textChanged.connect(self._pics_timer.start)
        b = QHBoxLayout(); b.addStretch(); self.g_del = button("Delete", "danger"); self.g_del.setMinimumHeight(42); self.g_del.clicked.connect(self._del_group); b.addWidget(self.g_del)
        s_ = button("Save group", "primary"); s_.clicked.connect(self._save_group); b.addWidget(s_); b.addStretch(); ev.addLayout(b)
        ed.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # so selecting a group (Delete appears) doesn't shift anything
        r.addWidget(ed, 11); c.v.addLayout(r, 1)
        grid.addWidget(c, 1, 1)
        self.group_rows: list[GroupRow] = []; self.cur_group: str | None = None

        # ---- force
        c = Card(); c.v.addWidget(self._h("RE-DOWNLOAD IN ORIGINAL QUALITY"))
        w = label("Paste links or post IDs (one per line). Each file is replaced in place and keeps its date and folder; "
                  "if the original still isn't ready, your copy is kept.", "mute"); w.setWordWrap(True); c.v.addWidget(w)
        self.force_text = QPlainTextEdit(); self.force_text.setPlaceholderText("7689799376637676820\nhttps://www.tiktok.com/@user/video/..."); 
        self.force_text.setMinimumHeight(110); c.v.addWidget(self.force_text, 1)
        r = QHBoxLayout(); r.addStretch(); f = button("Re-download", "primary", "ph.arrow-clockwise-bold", pal); f.clicked.connect(self._force); r.addWidget(f); r.addStretch()
        c.v.addLayout(r); grid.addWidget(c, 2, 0)

        # ---- maintenance
        c = Card(); c.v.addWidget(self._h("MAINTENANCE"))
        g = QGridLayout(); g.setSpacing(8)
        for i, (txt, ic, fn) in enumerate((("Check TikWM certificate", "ph.shield-check-fill", self._cert), ("Open log", "ph.file-text-fill", self._log),
                                           ("Open app folder", "ph.folder-simple-fill", lambda: self._open(self.base_dir)),
                                           ("Open downloads", "ph.download-simple-bold", lambda: self._open(self.qm.output_dir)))):
            bb = button(txt, "ghost", ic, pal); bb.clicked.connect(fn); g.addWidget(bb, i // 2, i % 2)
        c.v.addLayout(g); self.out_misc = Output(); self.out_misc.setMaximumHeight(16777215); c.v.addWidget(self.out_misc, 1)
        grid.addWidget(c, 2, 1)
        grid.setRowStretch(3, 1)
        self.refresh(); self._new_group()

    def _start_section(self, left, pal) -> None:
        from ..widgets import SegGroup
        keep_t, keep_s = "Keep current place", "Files stay around the dates they have now; only the order changes."
        cus_t, cus_s = "Custom start", "The oldest post starts at this date and time."
        self.picker = DateTimePicker(pal)
        seg = SegGroup([keep_t, cus_t], keep_t); note = label(keep_s, "mute", 11); note.setWordWrap(True)
        r = QHBoxLayout(); r.setSpacing(12); r.addWidget(seg); r.addWidget(note, 1); left.addLayout(r)
        self._pick_row = QHBoxLayout(); self._pick_row.setSpacing(8); self._pick_row.addWidget(self.picker, 1); self._pick_row.addSpacing(6)
        left.addLayout(self._pick_row)                  # (only the picker dims in "Keep current place")
        page = self

        class _Mode:
            value = "keep"

            def select(me, key: str) -> None:
                me.value = key; seg.select(keep_t if key == "keep" else cus_t, False)
                note.setText(keep_s if key == "keep" else cus_s); page.picker.set_active(key == "custom")
                page.apply.setEnabled(False)
        self.start_mode = _Mode()
        seg.changed.connect(lambda v: self.start_mode.select("keep" if v == keep_t else "custom"))
        self.picker.changed.connect(lambda: self.start_mode.select("custom"))
        self.picker.opened.connect(lambda: self.start_mode.select("custom"))       # calendar / time, like Now
        self.picker.set_active(False)

    def _h(self, t: str) -> QLabel:
        l = QLabel(t); l.setObjectName("h2"); return l

    def _labelled(self, text: str, w: QWidget) -> QHBoxLayout:
        r = QHBoxLayout(); r.setSpacing(12); l = label(text, "mute"); l.setFixedWidth(108); r.addWidget(l); r.addWidget(w, 1); return r

    def _target_box(self, everything: str = "Every folder, each on its own") -> QComboBox:
        b = StyledComboBox(); b.setMinimumWidth(220); b.setMaxVisibleItems(14); b.setProperty("everything", everything); return b

    def _fill_targets(self, box: QComboBox) -> None:
        from ..widgets import centred_pixmap
        from PySide6.QtGui import QIcon
        cur = box.currentData()
        box.blockSignals(True); box.clear()
        ic = lambda n: QIcon(centred_pixmap(n, self.pal.mute, 14))
        box.addItem(ic("ph.stack-fill"), box.property("everything"), "")
        for g in self.qm.cfg.creators:
            box.addItem(ic("ph.users-three-fill"), f"{g}  (group)", g)
        out = self.qm.output_dir
        if os.path.isdir(out):
            for d in sorted(os.listdir(out), key=str.lower):
                if os.path.isdir(os.path.join(out, d)) and d not in self.qm.cfg.creators and not d.startswith("."):
                    box.addItem(ic("ph.folder-simple-fill"), d, d)
        i = box.findData(cur); box.setCurrentIndex(max(0, i)); box.blockSignals(False)

    def refresh(self) -> None:
        self._fill_targets(self.target); self._fill_targets(self.fix_target)
        for row in self.group_rows:
            row.setParent(None); row.deleteLater()
        self.group_rows = []
        for name, accs in self.qm.cfg.creators.items():
            row = GroupRow(name, list(accs), self.pal); row.clicked.connect(lambda n=name: self._pick(n))
            self.group_list.addWidget(row); self.group_rows.append(row)
        self._mark()

    # ---------------------------------------------------------- re-order / fix
    def _reorder(self, apply: bool) -> None:
        target = self.target.currentData() or ""
        start = self.picker.text() if self.start_mode.value == "custom" else None
        out, cache, cfg = self.qm.output_dir, self.qm.cache, self.qm.cfg
        if start and not target:
            self.out_reorder.setPlainText("Pick a folder or creator group to use a custom start time."); return

        def fn():
            if not target:
                reorder_by_post(out, None, cache, start=start, confirm=not apply)
            else:
                folders = reorder_targets(target, cfg)
                if folders is None:
                    print(f"Could not understand: {target}")
                else:
                    reorder_by_post(out, [folders], cache, start=start, confirm=not apply)
        self.out_reorder.setPlainText("Working...")
        run_captured(fn, lambda text: self._done.emit(self.out_reorder, text.replace("Nothing changed.", "Preview only - press Apply to change the dates.")))
        self.apply.setEnabled(not apply)

    def _fix(self) -> None:
        mode = self.fix_mode.value; out, cache, cfg = self.qm.output_dir, self.qm.cache, self.qm.cfg
        target = self.fix_target.currentData() or ""

        def fn():
            if not target:
                fix_existing(out, cache, mode); return
            for name in (reorder_targets(target, cfg) or [target]):
                path = os.path.join(out, name)
                if os.path.isdir(path):
                    print(f"{name}:"); fix_existing(path, cache, mode)
        self.out_fix.setPlainText("Working...")
        run_captured(fn, lambda text: self._done.emit(self.out_fix, text))

    def _force(self) -> None:
        t = self.force_text.toPlainText().strip()
        if t and self.qm.submit(t, force=True, label="re-download"):
            self.force_text.clear()

    # ---------------------------------------------------------- groups
    def _mark(self) -> None:
        for row in self.group_rows:
            row.set_selected(row.name == self.cur_group)
        self.ed_title.setText(f"EDIT  {self.cur_group.upper()}" if self.cur_group else "NEW GROUP")
        self.g_del.setEnabled(bool(self.cur_group)); self.g_del.setVisible(True)
        t = self.ed_title.text()
        self.ed_title.setToolTip(t); self.ed_title.setMinimumWidth(1); self.ed_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def _accounts_in_editor(self) -> list:
        return [a.strip().lstrip("@") for a in self.g_accs.toPlainText().replace(",", "\n").split() if a.strip().lstrip("@")]

    def _pic_options(self) -> list:
        """(key, kind, key-for-painter, accounts, label) for every picture this group could use."""
        name = self.g_name.text().strip() or "group"; accs = self._accounts_in_editor()
        opts = []
        if len(accs) >= 2:
            opts.append(("", "group", name, accs, "Automatic (two overlap)"))
        opts += [("@" + a, "user", a, None, "@" + a) for a in accs]
        opts.append(("initials", "initials", name, None, "Initials"))
        if self.pic_choice.startswith("file:"):
            opts.append((self.pic_choice, "file", name, None, "Your image"))
        return opts

    def _fill_pics(self) -> None:
        """Current group picture + its name; 'Change' opens a menu with every choice (so nothing overflows)."""
        from ..avatars import AvatarWidget
        while self.pic_row.count():
            it = self.pic_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        name = self.g_name.text().strip() or "group"; accs = self._accounts_in_editor()
        chosen = self.pic_choice or (("@" + accs[0]) if len(accs) == 1 else "")
        cap = next((o[4] for o in self._pic_options() if o[0] == chosen), "Initials")
        if chosen.startswith("@"):
            av = AvatarWidget("user", chosen[1:], 44)
        elif chosen == "" and len(accs) >= 2:
            av = AvatarWidget("group", name, 44, accs)
        else:
            av = AvatarWidget("group", name, 44)
            if chosen != "initials" and not chosen.startswith("file:"):
                cap = "Initials"
        self.pic_row.addWidget(av)
        cl = QLabel(cap); cl.setStyleSheet("font-weight: 700; font-size: 13px;"); cl.setMinimumWidth(1)
        cl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred); self.pic_row.addWidget(cl, 1)
        ch = button("Change", "ghost", "ph.image-fill", self.pal); ch.clicked.connect(lambda: self._pic_menu(ch))
        self.pic_row.addWidget(ch)

    def _pic_menu(self, anchor) -> None:
        from PySide6.QtGui import QIcon
        from ..avatars import STORE, avatar_pixmap
        from ..widgets import styled_menu
        m = styled_menu(self)
        for key, kind, k, accs, cap in self._pic_options():
            if kind == "user":
                pm = avatar_pixmap(STORE[0], "user", k, 22)
            elif kind == "initials" or kind == "file":
                pm = avatar_pixmap(None, "group", k, 22)
            else:
                pm = avatar_pixmap(STORE[0], "group", k, 22, accs)
            a = m.addAction(QIcon(pm), cap); a.triggered.connect(lambda _=False, kk=key: self._choose_pic(kk))
        m.addSeparator()
        a = m.addAction("Your image…"); a.triggered.connect(self._pick_image_file)
        m.popup(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _choose_pic(self, key: str) -> None:
        self.pic_choice = key
        self._fill_pics()

    def _pick_image_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Choose a group picture", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if path:
            self._choose_pic("file:" + path)

    def _pick(self, name: str) -> None:
        from ..avatars import STORE
        self.pic_choice = STORE[0].group_choice(name) if STORE[0] else ""
        self.cur_group = name
        self.g_name.setText(name); self.g_accs.setPlainText("\n".join("@" + a.lstrip("@") for a in self.qm.cfg.creators.get(name, [])))
        self._mark()

    def _new_group(self) -> None:
        self.cur_group = None; self.pic_choice = ""; self.g_name.clear(); self.g_accs.clear(); self.g_name.setFocus(); self._mark()
        self._fill_pics()

    def _save_group(self) -> None:
        name = self.g_name.text().strip()
        accs = [a.strip().lstrip("@") for a in self.g_accs.toPlainText().replace(",", "\n").split() if a.strip().lstrip("@")]
        if not name or not accs:
            ConfirmDialog.info(self, "Creator groups", "Give the group a name and at least one @account.", self.pal)
            return
        creators = dict(self.store.get("creators") or {})
        old = self.cur_group
        if old and old != name:
            creators.pop(old, None)
        cleaned = ["@" + a for a in dict.fromkeys(accs)]
        creators[name] = cleaned
        from ..avatars import STORE
        if STORE[0] is not None:
            if old and old != name:
                STORE[0].set_group_choice(old, "")
            STORE[0].set_group_choice(name, self.pic_choice)
        self.cur_group = name
        self.store.set("creators", creators); self.groupsChanged.emit()
        self.notify.emit(
            "Group saved",
            f"“{name}” ({len(cleaned)} account{'s' if len(cleaned) != 1 else ''})",
        )

    def _del_group(self) -> None:
        if not self.cur_group:
            return
        if not ConfirmDialog.ask(
            self,
            "Creator groups",
            f"Delete the group \u201c{self.cur_group}\u201d? Your files aren't touched.",
            self.pal,
            confirm_text="Yes",
            cancel_text="No",
            danger=True,
        ):
            return
        creators = dict(self.store.get("creators") or {}); creators.pop(self.cur_group, None)
        self.store.set("creators", creators); self._new_group(); self.groupsChanged.emit()

    # ---------------------------------------------------------- maintenance
    def _cert(self) -> None:
        self.out_misc.setPlainText("Checking v16.tokcdn.com…")
        def work():
            import runpy
            lines: list[str] = []; set_sink(lines.append)
            try:
                runpy.run_module("tikvortex.core.certcheck", run_name="__main__")   # works in the .exe too
            except SystemExit:
                pass
            except Exception as e:
                lines.append(f"Error: {e}")
            finally:
                set_sink(None)
            text = "\n".join(lines)
            self._done.emit(self.out_misc, text)
        threading.Thread(target=work, daemon=True).start()

    def _log(self) -> None:
        self._open(os.path.join(self.base_dir, "log.txt"))

    def _open(self, path: str) -> None:
        if os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def restyle(self, pal: Palette) -> None:
        self.pal = pal; self.picker.restyle(pal); self.new_box.pal = pal; self.new_box.update()
        for o in self.options:
            o.restyle(pal)
        self.refresh()