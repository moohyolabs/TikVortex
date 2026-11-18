"""Settings page. Everything saves to config.json as soon as it changes."""
from __future__ import annotations

import os
import sys
from datetime import datetime

from PySide6.QtCore import QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QIcon
from PySide6.QtWidgets import (QSizePolicy, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSlider, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..theme import COLOUR_THEMES, Palette
from ..widgets import Card, SegGroup, Switch, button, centred_pixmap, label

TOKENS = {"Date posted": "{date}", "Creator": "{username}", "Creator ID": "{profile_uid}"}
SEPARATORS = {"–": " - ", "_": "_", "·": " · "}
DATE_STYLES = {"26-11-17": "%y-%m-%d", "2026-11-17": "%Y-%m-%d", "17 Nov 2026": "%d %b %Y"}
SAMPLE = {"{date}": None, "{username}": "sunny.studio", "{profile_uid}": "6715929249566147590", "{video_id}": "7709086869107133704"}


def parse_template(t: str):
    """'{date} - {username} - {video_id}{ext}' -> (['Date posted', 'Creator'], ' - ') or None if it's custom."""
    t = t.replace("{ext}", "")
    if not t.endswith("{video_id}"):
        return None
    rev = {v: k for k, v in TOKENS.items()}
    for sep in sorted(SEPARATORS.values(), key=len, reverse=True):
        parts = t.split(sep)
        if parts[-1] == "{video_id}" and all(p in rev for p in parts[:-1]):
            return [rev[p] for p in parts[:-1]], sep
    if t == "{video_id}":
        return [], " - "
    return None


class Chip(QFrame):
    removed = Signal(object)

    def __init__(self, name: str, pal: Palette, locked: bool = False):
        super().__init__(); self.name = name; self.locked = locked
        a, b = pal.accent
        self.setStyleSheet(f"QFrame {{ border-radius: 12px; background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {a}, stop:1 {b}); }}"
                           "QLabel { color: white; background: transparent; border: none; font-weight: 800; font-size: 12px; }"
                           "QPushButton { background: transparent; border: none; padding: 0; border-radius: 8px; }"
                           "QPushButton:hover { background: rgba(255,255,255,0.22); }")
        self.setFixedHeight(26)
        h = QHBoxLayout(self); h.setContentsMargins(8, 0, 5 if not locked else 10, 0); h.setSpacing(5)
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon
        from ..widgets import IconLabel, centred_pixmap
        # a chunky Material lock reads clearly at this size (the thin Phosphor shackle didn't)
        h.addWidget(IconLabel("mdi6.lock" if locked else "ph.dots-six-vertical-bold", "white", 13), 0, Qt.AlignVCenter)
        t = QLabel(name); h.addWidget(t, 0, Qt.AlignVCenter)
        if not locked:
            x = QPushButton(); x.setFixedSize(16, 16); x.setCursor(Qt.PointingHandCursor); x.setToolTip("Remove")
            x.setIcon(QIcon(centred_pixmap("ph.x-bold", "white", 10))); x.setIconSize(QSize(10, 10))
            x.clicked.connect(lambda: self.removed.emit(self)); h.addWidget(x, 0, Qt.AlignVCenter)
            self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Post ID always comes last so TikVortex can recognise your files" if locked else "Drag to reorder")

    def mousePressEvent(self, e):
        if self.locked or e.button() != Qt.LeftButton:
            return
        drag = QDrag(self); md = QMimeData(); md.setText("chip:" + self.name); drag.setMimeData(md)
        pm = self.grab(); drag.setPixmap(pm); drag.setHotSpot(e.position().toPoint())
        self.setVisible(False); drag.exec(Qt.MoveAction); self.setVisible(True)


class ChipLane(QFrame):
    changed = Signal()

    def __init__(self, pal: Palette):
        super().__init__(); self.setObjectName("row"); self.pal = pal; self.setAcceptDrops(True)
        self.h = QHBoxLayout(self); self.h.setContentsMargins(10, 10, 10, 10); self.h.setSpacing(6)
        self.marker = QFrame(); self.marker.setFixedSize(4, 26); self.marker.setStyleSheet(f"background: {pal.accent[1]}; border-radius: 2px;"); self.marker.hide()
        self.chips: list[Chip] = []
        self.sep_lbls: list[QLabel] = []
        self.sep = " - "
        self.lock = Chip("Post ID", pal, locked=True)

    def set_tokens(self, names: list[str], sep: str) -> None:
        self.sep = sep
        for c in self.chips:
            c.setParent(None); c.deleteLater()
        self.chips = [Chip(n, self.pal) for n in names]
        for c in self.chips:
            c.removed.connect(self._remove)
        self._relayout()

    def names(self) -> list[str]:
        return [c.name for c in self.chips]

    def _relayout(self) -> None:
        while self.h.count():
            it = self.h.takeAt(0)
            if it.widget() and it.widget() not in self.chips and it.widget() is not self.lock and it.widget() is not self.marker:
                it.widget().deleteLater()
        seq = self.chips + [self.lock]
        for i, c in enumerate(seq):
            self.h.addWidget(c)
            if i < len(seq) - 1:
                s = QLabel(self.sep.strip() or "␣"); s.setStyleSheet(f"color: {self.pal.mute}; font-weight: 800; padding: 0 2px;"); self.h.addWidget(s)
        self.h.addStretch()

    def _remove(self, chip: Chip) -> None:
        self.chips.remove(chip); chip.setParent(None); chip.deleteLater(); self._relayout(); self.changed.emit()

    def add(self, name: str) -> None:
        if name in self.names():
            return
        c = Chip(name, self.pal); c.removed.connect(self._remove); self.chips.append(c); self._relayout(); self.changed.emit()

    def _index_at(self, x: int) -> int:
        for i, c in enumerate(self.chips):
            if c.isVisible() and x < c.geometry().center().x():
                return i
        return len(self.chips)

    def dragEnterEvent(self, e):
        if e.mimeData().text().startswith("chip:"):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()
        i = self._index_at(e.position().toPoint().x())
        target = self.chips[i] if i < len(self.chips) else self.lock
        self.marker.setParent(self); self.marker.move(target.geometry().left() - 5, target.geometry().top()); self.marker.show(); self.marker.raise_()

    def dragLeaveEvent(self, e):
        self.marker.hide()

    def dropEvent(self, e):
        self.marker.hide()
        name = e.mimeData().text()[5:]
        chip = next((c for c in self.chips if c.name == name), None)
        if not chip:
            return
        i = self._index_at(e.position().toPoint().x())
        old = self.chips.index(chip); self.chips.remove(chip)
        self.chips.insert(i - 1 if i > old else i, chip)
        e.acceptProposedAction(); self._relayout(); self.changed.emit()


class SettingRow(QHBoxLayout):
    """Title (and description) on the left, the control on the right. Every row is at least one control tall,
    so rows with and without a description keep the same rhythm."""
    H = 38                                            # the one control height used on this page

    def __init__(self, title: str, sub: str, widget: QWidget):
        super().__init__(); self.setSpacing(24)
        from PySide6.QtWidgets import QSpacerItem
        self.addSpacerItem(QSpacerItem(0, self.H, QSizePolicy.Fixed, QSizePolicy.Fixed))
        col = QVBoxLayout(); col.setSpacing(1); col.addStretch(1)        # text centred on the control
        t = QLabel(title); t.setStyleSheet("font-weight: 700; font-size: 13px;"); col.addWidget(t)
        self.sub = None
        if sub:
            s = label(sub, "mute", 11); s.setWordWrap(True); col.addWidget(s); self.sub = s
        col.addStretch(1)
        self.addLayout(col, 1); self.addWidget(widget, 0, Qt.AlignVCenter | Qt.AlignRight)


class SettingsPage(QWidget):
    title = "SETTINGS"
    subtitle = "Changes save automatically to config.json"
    engineChanged = Signal()
    uiChanged = Signal(str)
    previewSound = Signal()

    def __init__(self, store, pal: Palette):
        super().__init__(); self.store, self.pal = store, pal
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea(); sc.setWidgetResizable(True); outer.addWidget(sc)
        body = QWidget(); sc.setWidget(body)
        # A grid, so each pair of cards side by side shares its top and bottom edges.
        grid = QGridLayout(body); grid.setContentsMargins(0, 0, 6, 0); grid.setSpacing(14)
        grid.setColumnStretch(0, 11); grid.setColumnStretch(1, 9)
        # Downloads|Appearance, File names|Quality & dates, System|Shortcuts+About: pairs of similar height,
        # so cards side by side share edges without empty space inside them
        cells = iter([(0, 0), (1, 0), (1, 1), (0, 1), (2, 0)])
        place = lambda card: (card.v.addStretch(1), grid.addWidget(card, *next(cells)))
        self._debounce = QTimer(self); self._debounce.setSingleShot(True); self._debounce.setInterval(400)
        self._debounce.timeout.connect(self.engineChanged.emit)

        # ---- downloads
        c = Card(); c.v.addWidget(self._h("DOWNLOADS"))
        fw = QWidget(); fh = QHBoxLayout(fw); fh.setContentsMargins(0, 0, 0, 0)
        self.folder = QLineEdit(store.get("output_dir")); self.folder.setMinimumWidth(220); self.folder.editingFinished.connect(lambda: self._set("output_dir", self.folder.text().strip() or "profiles"))
        self.folder.setFixedHeight(SettingRow.H); self.folder.setMinimumWidth(0)
        fh.setSpacing(8); fh.addWidget(self.folder, 1); br = button("Browse", "ghost"); br.setFixedHeight(SettingRow.H); br.clicked.connect(self._browse); fh.addWidget(br)
        c.v.addLayout(SettingRow("Save to", "Main download folder (relative paths are next to the app)", fw))
        self.layout_seg = SegGroup(["By creator group", "By account", "One folder"],
                                   {"group": "By creator group", "account": "By account", "single": "One folder"}[store.get("folder_layout") or "group"])
        self.layout_seg.changed.connect(lambda v: self._set("folder_layout", {"By creator group": "group", "By account": "account", "One folder": "single"}[v]))
        c.v.addLayout(SettingRow("Folder layout", "Grouped accounts go into their group's folder; everyone else into their own", self.layout_seg))
        self.workers = self._slider(1, 8, int(store.get("download_workers") or 3), lambda v: str(v), "download_workers")
        c.v.addLayout(SettingRow("Parallel downloads", "How many files download at once", self.workers))
        self.wait = self._slider(3, 60, int(float(store.get("original_wait_sec") or 180) / 10), lambda v: f"{v * 10} s", "original_wait_sec", scale=10)
        c.v.addLayout(SettingRow("Wait for original file", "How long TikWM gets to prepare the original", self.wait))
        self.speed = self._slider(0, 40, int((store.get("speed_limit_kbps") or 0) / 512), lambda v: "Unlimited" if v == 0 else f"{v / 2:g} MB/s",
                                  "speed_limit_kbps", scale=512)
        c.v.addLayout(SettingRow("Speed limit", "So a big batch doesn't hog your connection", self.speed))
        self.order = SegGroup(["Oldest first", "Newest first"], "Newest first" if (store.get("profile_order") or "oldest") == "newest" else "Oldest first")
        self.order.changed.connect(lambda v: self._set("profile_order", "newest" if v.startswith("Newest") else "oldest"))
        c.v.addLayout(SettingRow("Download order", "For profiles and groups. Oldest first keeps folders in posting order", self.order))
        # the folder box and the sliders share the folder-layout switch's width, so their left edges line up
        col_w = self.layout_seg.sizeHint().width()
        for w in (fw, self.workers, self.wait, self.speed):
            w.setFixedWidth(col_w)
        place(c)

        # ---- file names
        c = Card(); c.v.addWidget(self._h("FILE NAMES"))
        c.v.addWidget(label("Drag the pieces to reorder · ✕ removes one · click below to add one back", "mute", 11))
        self.lane = ChipLane(pal); self.lane.changed.connect(self._template_changed); c.v.addWidget(self.lane)
        self.add_row = QHBoxLayout(); self.add_row.setSpacing(6); c.v.addLayout(self.add_row)
        r = QHBoxLayout(); r.addWidget(label("Date style", "mute"))
        fmt = store.get("date_format") or "%y-%m-%d"
        self.date_seg = SegGroup(list(DATE_STYLES), next((k for k, v in DATE_STYLES.items() if v == fmt), "26-11-17"))
        self.date_seg.changed.connect(lambda k: (self._set("date_format", DATE_STYLES[k]), self._preview()))
        r.addWidget(self.date_seg); r.addSpacing(10); r.addWidget(label("Separator", "mute"))
        self.sep_seg = SegGroup(list(SEPARATORS)); self.sep_seg.changed.connect(lambda k: self._template_changed()); r.addWidget(self.sep_seg); r.addStretch()
        c.v.addLayout(r)
        self.preview = QLabel(); self.preview.setStyleSheet(f"background: {pal.field}; border: 1px solid {pal.stroke}; border-radius: 10px; padding: 9px 12px; font-family: Consolas, monospace; font-size: 12px;")
        self.preview.setWordWrap(True); self.preview.setMinimumWidth(1)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # never widens the page
        c.v.addWidget(self.preview)
        self.custom_note = label("", "mute", 11); self.custom_note.setWordWrap(True); c.v.addWidget(self.custom_note)
        self.custom_note.hide()                       # only takes up room when there's something to say
        foot = label("Photo slideshows add _img_1, _img_2… to the name. Files you already have are recognised by their post ID, "
                     "so changing this never causes re-downloads.", "mute", 11); foot.setWordWrap(True); c.v.addWidget(foot)
        place(c)
        parsed = parse_template(store.get("naming_template") or "")
        if parsed:
            names, sep = parsed
            self.sep_seg.select(next((k for k, v in SEPARATORS.items() if v == sep), "–"), False)
        else:
            names, sep = ["Date posted", "Creator ID", "Creator"], " - "
            self.custom_note.setText(f"Your config.json has a custom pattern ({store.get('naming_template')}). It stays until you change the pieces here.")
            self.custom_note.show()
        self.lane.set_tokens(names, sep); self._custom = parsed is None
        self._refresh_add(); self._preview()

        # ---- quality & dates
        c = Card(); c.v.addWidget(self._h("QUALITY & DATES"))
        c.v.addLayout(SettingRow("Allow compressed fallback", "If the original isn't ready, save the compressed copy instead of skipping",
                                 self._switch("allow_compressed_fallback")))
        c.v.addLayout(SettingRow("Retry compressed at end of batch", "Tries once more for the original when the rest are done", self._switch("retry_compressed_at_end")))
        self.dates = SegGroup(["Download time", "Post time", "Off"], {"downloaded": "Download time", "posted": "Post time", "off": "Off"}[store.get("media_dates") or "downloaded"])
        self.dates.changed.connect(lambda v: self._set("media_dates", {"Download time": "downloaded", "Post time": "posted", "Off": "off"}[v]))
        c.v.addLayout(SettingRow("Write dates into files", "So photo apps show them in posting order", self.dates))
        c.v.addLayout(SettingRow("Accept TikWM's expired certificate", "Only for its file server (*.tokcdn.com) - like clicking “Proceed” in a browser",
                                 self._switch("accept_expired_tokcdn_cert")))
        c.v.addLayout(SettingRow("Try gallery-dl for profiles", "Last resort if TikWM and yt-dlp can't list a profile. Slower, but often works when they're blocked",
                                 self._switch("gallery_dl_fallback")))
        # sign-in details (both optional): sessionid.txt for TikWM, cookies.txt for yt-dlp / gallery-dl
        self.sid = QLineEdit(); self.sid.setEchoMode(QLineEdit.Password); self.sid.setPlaceholderText("Optional"); self.sid.setFixedHeight(SettingRow.H)
        self.sid.setText(self._read_sid()); self.sid.setTextMargins(0, 0, 36, 0)
        self.sid.setCursorPosition(0); self.sid.setProperty("noclear", True)      # starts from the left
        self.sid.editingFinished.connect(lambda: self.sid.setCursorPosition(0))
        # show/hide button inside the box, styled like the date box's calendar button
        self.eye = button("", "calbtn", "ph.eye-fill", self.pal); self.eye.setParent(self.sid); self.eye.setFixedSize(32, 30)
        self.eye.setCursor(Qt.PointingHandCursor); self.eye.setToolTip("Show or hide"); self.eye.clicked.connect(self._toggle_sid)
        self.sid.installEventFilter(self)
        self.sid.editingFinished.connect(self._save_sid)
        c.v.addLayout(SettingRow("TikTok session ID", "Your sessionid cookie, for sign-in-only posts", self.sid))
        cw = QWidget(); ch = QHBoxLayout(cw); ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(8)
        self.ck_pick = button("Choose file…", "ghost", "ph.cookie-fill", self.pal); self.ck_pick.clicked.connect(self._pick_cookies); ch.addWidget(self.ck_pick)
        self.ck_del = button("Remove", "ghost"); self.ck_del.clicked.connect(self._remove_cookies); ch.addWidget(self.ck_del)
        self.ck_row = SettingRow("Cookies", " ", cw); c.v.addLayout(self.ck_row); self._cookies_label()
        for b in (self.ck_pick, self.ck_del):
            b.setFixedHeight(SettingRow.H)
        self.sid.setFixedWidth(self.dates.sizeHint().width())      # same column as the dates switch above it
        place(c)

        # ---- appearance
        c = Card(); c.v.addWidget(self._h("APPEARANCE"))
        self.mode = SegGroup(["Light", "Dark", "Auto"], store.ui("theme_mode")); self.mode.changed.connect(lambda v: self._ui("theme_mode", v))
        c.v.addLayout(SettingRow("Theme", "Auto follows Windows", self.mode))
        sw = QWidget(); sg = QGridLayout(sw); sg.setContentsMargins(0, 0, 0, 0); sg.setSpacing(6)
        self.swatches = {}
        for i, (name, (_d, _l, acc, _al)) in enumerate(COLOUR_THEMES.items()):
            b = QPushButton(name); b.setCheckable(True); b.setChecked(name == store.ui("colour")); b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ border: 2px solid transparent; border-radius: 10px; padding: 6px 10px; color: white; font-weight: 800; font-size: 11px;"
                            f" background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {acc[0]}, stop:1 {acc[1]}); }}"
                            "QPushButton:checked { border: 2px solid white; }")
            b.clicked.connect(lambda _=False, n=name: self._colour(n)); sg.addWidget(b, i // 3, i % 3); self.swatches[name] = b
        c.v.addLayout(SettingRow("Colour theme", "", sw))
        self.glass_seg = SegGroup(["Strong", "Subtle", "Off"], store.ui("glass")); self.glass_seg.changed.connect(lambda v: self._ui("glass", v))
        c.v.addLayout(SettingRow("Glass", "How see-through the cards are", self.glass_seg))
        c.v.addLayout(SettingRow("Moving background", "Lets the background colours drift slowly (uses a little more power)",
                                 self._uiswitch("moving_background")))
        c.v.addLayout(SettingRow("Reduce motion", "Turns off sliding and fading animations", self._uiswitch("reduce_motion")))
        place(c)

        # ---- system
        c = Card(); c.v.addWidget(self._h("SYSTEM"))
        snd = QWidget(); sh = QHBoxLayout(snd); sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(8)
        pv = button("Preview", "ghost", "ph.play-fill", pal); pv.clicked.connect(self.previewSound.emit); sh.addWidget(pv); sh.addWidget(self._uiswitch("finish_sound"))
        c.v.addLayout(SettingRow("Finish sound", "Soft chime when a batch completes", snd))
        c.v.addLayout(SettingRow("Keep running in the tray", "Closing the window keeps downloads going", self._uiswitch("tray")))
        c.v.addLayout(SettingRow("Notify when a batch finishes", "A Windows notification with how it went", self._uiswitch("notify")))
        c.v.addLayout(SettingRow("Watch clipboard on launch", "Copied TikTok links are added as soon as the app opens", self._uiswitch("clipboard_on_launch")))
        if sys.platform == "win32":
            cur = ("Off" if not store.ui("start_with_windows") else
                   {"minimised": "Minimised", "tray": "Tray"}.get(store.ui("start_mode") or "", "Open"))
            self.startup = SegGroup(["Off", "Open", "Minimised", "Tray"], cur)
            self.startup.changed.connect(lambda v: (self._ui("start_mode", {"Minimised": "minimised", "Tray": "tray"}.get(v, "open")),
                                                    self._ui("start_with_windows", v != "Off")))
            c.v.addLayout(SettingRow("Start with Windows", "Open normally, minimised to the taskbar, or hidden in the tray", self.startup))
        self.failed = SegGroup(["Ask", "Always", "Never"], store.ui("failed_links")); self.failed.changed.connect(lambda v: self._ui("failed_links", v))
        c.v.addLayout(SettingRow("Save failed links to a .txt", "So you can drop the file back in later to retry", self.failed))
        c.v.addLayout(SettingRow("Check for updates", "Looks for a new release on the moohyo labs GitHub at launch", self._uiswitch("check_updates")))
        self.cache_btn = button("Clear", "ghost"); self.cache_btn.clicked.connect(self._clear_cache)
        self.pics_btn = button("Get profile pictures", "ghost", "ph.user-circle", self.pal); self.pics_btn.clicked.connect(self._get_pics)
        cw = QWidget(); ch = QHBoxLayout(cw); ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(8)
        ch.addWidget(self.pics_btn); ch.addWidget(self.cache_btn)
        for b in (self.pics_btn, self.cache_btn, pv):
            b.setFixedHeight(SettingRow.H)
        self.cache_row = SettingRow("Cache", " ", cw); c.v.addLayout(self.cache_row)
        self._cache_label()
        place(c)

        # ---- shortcuts + about
        c = Card(); c.v.addWidget(self._h("SHORTCUTS"))
        g = QGridLayout(); g.setHorizontalSpacing(14); g.setVerticalSpacing(8)
        for i, (k, d) in enumerate((("Ctrl+V", "Paste & queue"), ("Ctrl+Enter", "Start download"), ("Ctrl+1–5", "Switch page"),
                                    ("Ctrl+K", "Search / commands"), ("Ctrl+M", "Mini Mode"), ("Esc", "Clear selection"))):
            kl = QLabel(k); kl.setStyleSheet(f"font-weight: 800; font-size: 12px; background: {pal.glass2}; border: 1px solid {pal.stroke}; border-radius: 6px; padding: 2px 8px;")
            kl.setFixedWidth(kl.sizeHint().width() + 4)
            g.addWidget(kl, i // 2, (i % 2) * 2); g.addWidget(label(d, "mute"), i // 2, (i % 2) * 2 + 1)
        g.setColumnStretch(1, 1); g.setColumnStretch(3, 1)
        last = QVBoxLayout(); last.setSpacing(14); grid.addLayout(last, 2, 1)
        c.v.addLayout(g); last.addWidget(c)
        c = Card(); c.v.addWidget(self._h("ABOUT"))
        a = label(f"{APP_NAME} {__version__} by <b>moohyo labs</b> · free software under the GNU GPL v3.<br><br>"
                  "Built on Ez-TikTok-Downloader by AlexRabbit. Downloads go through TikWM. Not affiliated with TikTok. "
                  "See README.md for full credits.", "mute")
        a.setWordWrap(True); a.setTextFormat(Qt.RichText); c.v.addWidget(a)
        gh = QHBoxLayout(); gh.addStretch()
        from ..window import open_github
        b = button("moohyo labs on GitHub", "ghost", "ph.github-logo-fill", self.pal); b.clicked.connect(open_github); gh.addWidget(b)
        c.v.addStretch(1); c.v.addLayout(gh); last.addWidget(c, 1)
        grid.setRowStretch(3, 1)

    # ---------------------------------------------------------- helpers
    def _h(self, t):
        l = QLabel(t); l.setObjectName("h2"); return l

    def _set(self, key, value) -> None:
        self.store.set(key, value); self._debounce.start()

    def _ui(self, key, value) -> None:
        self.store.set_ui(key, value)
        if key in ("theme_mode", "glass", "colour"):
            # let the highlight finish gliding before the whole window re-themes
            QTimer.singleShot(SegGroup.DURATION + 40, lambda: self.uiChanged.emit(key))
        else:
            self.uiChanged.emit(key)

    def _switch(self, key) -> Switch:
        s = Switch(bool(self.store.get(key)), self.pal.accent); s.toggled.connect(lambda on: self._set(key, on)); return s

    def _cache_dirs(self) -> list:
        base = os.path.join(self.store.base_dir, ".tikvortex")
        return [os.path.join(base, d) for d in ("thumbs", "covers", "avatars")]

    def _cache_label(self) -> None:
        total = 0
        for d in self._cache_dirs():
            for root, _dirs, files in os.walk(d):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        from ..engine import fmt_bytes
        self.cache_row.sub.setText(f"Thumbnails, covers and profile pictures · {fmt_bytes(total)}")

    def _toggle_sid(self) -> None:
        hidden = self.sid.echoMode() == QLineEdit.Password
        self.sid.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        self.eye.setIcon(QIcon(centred_pixmap("ph.eye-slash-fill" if hidden else "ph.eye-fill", self.pal.icon, 15)))

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is getattr(self, "sid", None) and e.type() in (QEvent.Resize, QEvent.Show):
            self.eye.move(self.sid.width() - self.eye.width() - 6, (self.sid.height() - self.eye.height()) // 2)
        return super().eventFilter(obj, e)

    # ---------------------------------------------------------- session id / cookies
    def _sid_path(self) -> str:
        return os.path.join(self.store.base_dir, "sessionid.txt")

    def _read_sid(self) -> str:
        try:
            with open(self._sid_path(), encoding="utf-8") as f:
                line = f.readline().strip()
        except OSError:
            return ""
        line = line[10:].strip() if line.lower().startswith("sessionid=") else line
        return "" if line.startswith("#") or line.upper().startswith("PASTE_") else line

    def _save_sid(self) -> None:
        v = self.sid.text().strip()
        try:
            if v:
                with open(self._sid_path(), "w", encoding="utf-8") as f:
                    f.write(v + "\n")
            elif os.path.isfile(self._sid_path()):
                os.remove(self._sid_path())
        except OSError:
            pass
        self._debounce.start()

    def _ck_path(self) -> str:
        return os.path.join(self.store.base_dir, "cookies.txt")

    def _cookies_label(self) -> None:
        have = os.path.isfile(self._ck_path())
        n = 0
        if have:
            try:
                with open(self._ck_path(), encoding="utf-8", errors="ignore") as f:
                    n = sum(1 for l in f if l.strip() and not l.startswith("#") and "tiktok" in l.lower())
            except OSError:
                pass
        self.ck_row.sub.setText(f"cookies.txt loaded · {n} TikTok cookies" if have else
                                "A cookies.txt (Netscape format) for private or restricted profiles")
        self.ck_del.setEnabled(have)

    def _pick_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a cookies.txt", "", "Cookies (*.txt);;All files (*)")
        if path and os.path.abspath(path) != os.path.abspath(self._ck_path()):
            import shutil
            try:
                shutil.copyfile(path, self._ck_path())
            except OSError:
                pass
        self._cookies_label(); self._debounce.start()

    def _remove_cookies(self) -> None:
        try:
            os.remove(self._ck_path())
        except OSError:
            pass
        self._cookies_label(); self._debounce.start()

    def _get_pics(self) -> None:
        from ..avatars import STORE
        n = STORE[0].fetch_all() if STORE[0] is not None else 0
        self.pics_btn.setText(f"Fetching {n}…" if n else "All up to date")
        QTimer.singleShot(2600, lambda: (self.pics_btn.setText("Get profile pictures"), self._cache_label()))

    def _clear_cache(self) -> None:
        import shutil
        for d in self._cache_dirs():
            shutil.rmtree(d, ignore_errors=True)
        from ..avatars import STORE
        if STORE[0] is not None:
            STORE[0].clear_cache()
        self._cache_label()

    def _uiswitch(self, key) -> Switch:
        s = Switch(bool(self.store.ui(key)), self.pal.accent); s.toggled.connect(lambda on: self._ui(key, on)); return s

    def _slider(self, lo, hi, val, fmt, key, scale=1) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(14)
        s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(max(lo, min(hi, val)))
        l = QLabel(fmt(s.value())); l.setFixedWidth(74); l.setStyleSheet("font-weight: 800; font-size: 12px;")
        l.setAlignment(Qt.AlignRight | Qt.AlignVCenter)          # values end on the same edge as every other control
        s.valueChanged.connect(lambda v: (l.setText(fmt(v)), self._set(key, v * scale)))
        h.addWidget(s, 1); h.addWidget(l); return w

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Download folder", self.folder.text())
        if d:
            self.folder.setText(d); self._set("output_dir", d)

    def _colour(self, name: str) -> None:
        for n, b in self.swatches.items():
            b.setChecked(n == name)
        self._ui("colour", name)

    def _refresh_add(self) -> None:
        while self.add_row.count():
            w = self.add_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for n in TOKENS:
            if n not in self.lane.names():
                b = button("+  " + n, "chip"); b.clicked.connect(lambda _=False, n=n: self.lane.add(n)); self.add_row.addWidget(b)
        self.add_row.addStretch()

    def _template_changed(self) -> None:
        sep = SEPARATORS[self.sep_seg.value]; self.lane.sep = sep; self.lane._relayout()
        tpl = sep.join([TOKENS[n] for n in self.lane.names()] + ["{video_id}"]) + "{ext}"
        self._custom = False; self.custom_note.setText("")
        self._set("naming_template", tpl); self._refresh_add(); self._preview()

    def restyle(self, pal: Palette) -> None:
        self.pal = pal
        for sw in self.findChildren(Switch):
            sw.accent = pal.accent; sw.update()
        self.lane.pal = pal; self.lane.lock = Chip("Post ID", pal, locked=True)
        self.lane.marker.setStyleSheet(f"background: {pal.accent[1]}; border-radius: 2px;")
        self.lane.set_tokens(self.lane.names(), self.lane.sep)
        self.preview.setStyleSheet(f"background: {pal.field}; border: 1px solid {pal.stroke}; border-radius: 10px; padding: 9px 12px; font-family: Consolas, monospace; font-size: 12px;")
        for n, b in self.swatches.items():
            b.setChecked(n == self.store.ui("colour"))
        self.mode.select(self.store.ui("theme_mode"), False); self.glass_seg.select(self.store.ui("glass"), False)

    def _preview(self) -> None:
        tpl = self.store.get("naming_template") or ""
        fmt = self.store.get("date_format") or "%y-%m-%d"
        sample = dict(SAMPLE); sample["{date}"] = datetime(2026, 11, 17).strftime(fmt)
        out = tpl.replace("{ext}", ".mp4")
        for k, v in sample.items():
            out = out.replace(k, v)
        stem = out[:-4] if out.endswith(".mp4") else out
        # one box, two examples: a video, and the first image of a photo slideshow
        self.preview.setText(f"{out}\n{stem}_img_1.jpg")        # a video, and the first photo of a slideshow
