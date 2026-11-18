"""Main window: frameless glass window with sidebar, header, pages, tray icon and mini mode."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSpacerItem,
                               QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from tikvortex.core.clipboard import extract_tiktok_url
from tikvortex.core.tikwm import api_call_count

from . import ASSETS, APP_NAME
from .engine import QueueManager, StatusProbe, fmt_bytes
from .loaders import CoverLoader, ThumbLoader
from .pages.download import DownloadPage
from .pages.highlights import HighlightsPage
from .pages.library import LibraryPage
from .pages.settings import SettingsPage
from .pages.tools import ToolsPage
from .sidebar import Sidebar
from .theme import BAD, OK, WARN, make_palette, stylesheet
from . import widgets as widgets_mod
from .widgets import (Backdrop, ConfirmDialog, EmptyBox, HeaderPill, IconLabel, SegGroup, StatusCard, Toast,
                      Wordmark, app_icon_pixmap, button, centred_pixmap, install_apple_tooltips, label,
                      refresh_icons, styled_menu)

EDGE = 7


class CommandBar(QFrame):
    clicked = Signal()

    def __init__(self, pal):
        super().__init__(); self.setObjectName("cmd"); self.setFixedHeight(34); self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self); h.setContentsMargins(14, 0, 8, 0); h.setSpacing(9)
        self.ic = IconLabel("ph.magnifying-glass-fill", pal.icon_dim, 16); h.addWidget(self.ic, 0, Qt.AlignVCenter)
        self.txt = label("Search or run a command…", "mute", 13); h.addWidget(self.txt, 0, Qt.AlignVCenter); h.addStretch()
        self.k = QLabel("Ctrl K"); self.k.setFixedHeight(20); self.k.setAlignment(Qt.AlignCenter); h.addWidget(self.k, 0, Qt.AlignVCenter)
        for w in (self.ic, self.txt, self.k):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_Hover)
        self.restyle(pal)

    def restyle(self, pal) -> None:
        self.ic.set_icon("ph.magnifying-glass-fill", pal.icon_dim, 16)
        self.k.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {pal.mute}; background: {pal.glass}; border: 1px solid {pal.stroke}; border-radius: 10px; padding: 0 8px;")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class Header(QWidget):
    """Page title + subtitle, centred command bar, and the Mini Mode / folder / Online pills."""
    def __init__(self, pal, win):
        super().__init__(); self.win = win
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.h = QHBoxLayout(self); self.h.setContentsMargins(0, 0, 0, 0); self.h.setSpacing(8)
        # page icon in a small glass tile, so the title doesn't sit hard against the edge
        self.pg_icon = QLabel(); self.pg_icon.setFixedSize(46, 46); self.pg_icon.setAlignment(Qt.AlignCenter)
        self.pg_icon.setObjectName("pgicon"); self.h.addWidget(self.pg_icon, 0, Qt.AlignVCenter); self.h.addSpacing(6)
        tb = QWidget(); tv = QVBoxLayout(tb); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(0)
        self.title = Wordmark("DOWNLOAD", 25, pal.text); tv.addWidget(self.title)
        self.sub = label("", "subtitle"); self.sub.setContentsMargins(2, 0, 0, 0); tv.addWidget(self.sub)
        self.tb = tb
        self.h.addWidget(tb, 0, Qt.AlignVCenter); self.h.addStretch(1)
        self.cmd = CommandBar(pal); self.h.addWidget(self.cmd, 0, Qt.AlignVCenter); self.h.addStretch(1)
        self.mini = HeaderPill("ph.picture-in-picture-fill", "Mini Mode", pal); self.h.addWidget(self.mini, 0, Qt.AlignVCenter)
        self.folder = HeaderPill("ph.folder-simple-fill", "Downloads", pal); self.h.addWidget(self.folder, 0, Qt.AlignVCenter)
        self.online = HeaderPill("", "Checking…", pal, dot="#94a3b8"); self.h.addWidget(self.online, 0, Qt.AlignVCenter)
        # one width for every status, so the pills and command bar never shift when it changes
        self.online.ensurePolished(); widest = 0
        for t in ("Checking…", "Online", "Offline", "Busy"):
            self.online.lbl.setText(t); self.online.adjustSize(); widest = max(widest, self.online.sizeHint().width())
        self.online.lbl.setText("Checking…"); self._online_max = widest; self.online.setFixedWidth(widest)
        self.folder.ensurePolished()

    def set_page(self, page, pal) -> None:
        self.title.set_colors(pal.text); self.title.set_text(page.title); self.sub.setText(page.subtitle)
        icons = {"DOWNLOAD": "mdi6.download", "LIBRARY": "ph.squares-four-fill", "HIGHLIGHTS": "ph.sparkle-fill",
                 "TOOLS": "ph.wrench-fill", "SETTINGS": "ph.gear-six-fill", "WATCHLIST": "ph.eye-fill"}
        from .widgets import centred_pixmap
        self.pg_icon.setPixmap(centred_pixmap(icons.get(page.title, "ph.circle-fill"), "white", 22))
        a, b = pal.accent
        self.pg_icon.setStyleSheet(f"QLabel#pgicon {{ border-radius: 13px; border: 1px solid rgba(255,255,255,0.28);"
                                   f" background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {a}, stop:1 {b}); }}")

    def resizeEvent(self, e):
        super().resizeEvent(e); self.fit()

    def set_title_widths(self, pages) -> None:
        widest = 0
        for pg in pages:
            self.title.set_text(pg.title); self.sub.setText(pg.subtitle)
            self.tb.adjustSize(); widest = max(widest, self.tb.sizeHint().width())
        self.max_left = widest
        self.tb.setFixedWidth(widest)

    def set_status(self, text: str) -> None:
        """Status pill hugs its text; the folder pill beside it grows by the same amount, so the
        Mini Mode pill and the command bar never move."""
        self.online.lbl.setText(text); self.online.setMinimumWidth(0); self.online.setMaximumWidth(16777215)
        self.online.adjustSize(); w = self.online.sizeHint().width()
        self.online.setFixedWidth(w)
        if not hasattr(self, "_folder_base"):
            self._folder_base = self.folder.sizeHint().width()
        self.folder.setFixedWidth(self._folder_base + (self._online_max - w))
        self.fit()

    def fit(self) -> None:
        # everything else in the row at its real width (the page icon too, and pills given fixed widths),
        # plus the layout's own gaps; the command bar gets what's left, keeping at least 28 px clear on each side
        def wid(w):
            return max(w.sizeHint().width(), w.minimumWidth())
        left = wid(self.pg_icon) + 6 + max(self.tb.sizeHint().width(), getattr(self, "max_left", 0))
        right = sum(wid(w) for w in (self.mini, self.folder, self.online) if w.isVisible())
        gaps = self.h.spacing() * 7
        avail = self.width() - left - right - gaps
        self.cmd.setFixedWidth(max(220, min(640, avail - 56)))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.win.windowHandle():
            self.win.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.win.toggle_max()


class CommandPalette(QDialog):
    def __init__(self, win):
        super().__init__(win, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint); self.setObjectName("palette"); self.win = win
        # Rounded and see-through (no square corners), which also gives the list smooth, evenly spaced text.
        self.setAttribute(Qt.WA_TranslucentBackground); self.setFixedWidth(560)
        from .widgets import paint_popup_background
        paint_popup_background(self, 16)
        v = QVBoxLayout(self); v.setContentsMargins(12, 12, 12, 12); v.setSpacing(12)    # same 12 px everywhere
        self.edit = QLineEdit(); self.edit.setPlaceholderText("Type a command, or paste links to download"); v.addWidget(self.edit)
        self.list = QListWidget(); v.addWidget(self.list)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel); self.list.setSpacing(0)
        self.list.setFrameShape(QFrame.NoFrame); self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        f = QFont(self.font()); f.setPixelSize(13); f.setWeight(QFont.DemiBold); f.setHintingPreference(QFont.PreferNoHinting)
        f.setStyleStrategy(QFont.StyleStrategy(QFont.PreferAntialias | QFont.NoSubpixelAntialias | QFont.PreferQuality))
        self.list.setFont(f)
        a = widgets_mod.ACCENT[0]
        self.list.setStyleSheet("QListWidget::item { padding: 8px 10px; border-radius: 8px; }"
                                f"QListWidget::item:selected {{ background: {a}; color: white; }}")
        self.list.setMouseTracking(True); self.list.setCursor(Qt.PointingHandCursor)
        self.list.itemEntered.connect(self.list.setCurrentItem)
        self.edit.textChanged.connect(self._fill); self.edit.returnPressed.connect(self._run)
        self.list.itemClicked.connect(lambda it: (self.list.setCurrentItem(it), self._run()))
        self.commands = [
            ("Go to Watchlist", lambda: win.open_watchlist("")),
            ("Watch a creator for new posts", lambda: (win.open_watchlist(""), win.watch.input.setFocus())),
            ("Find a creator's highlights", lambda: (win.show_page("Highlights"), win.highlights.user.setFocus())),
            ("Show the creator gallery", lambda: (win.show_page("Library"), win.library.show_gallery())),
            ("Show compressed posts in the library", lambda: (win.show_page("Library"), win.library.kind.select("Compressed"))),
            ("Show duplicate posts in the library", lambda: (win.show_page("Library"), win.library.kind.select("Duplicates"))),
            ("Turn clipboard monitor on / off", lambda: win.download.clip.toggle()),
            ("Get missing profile pictures", lambda: win.settings._get_pics()),
            ("Open the log", lambda: win.tools._log()),
            ("Check watchlist now", lambda: win.wl.check_all()), ("Paste clipboard and download", win.paste_and_download),
            ("Pause / resume downloads", lambda: win.qm.pause(not win.qm.paused)), ("Stop downloads", win.qm.stop),
            ("Retry all compressed in original quality", win.qm.retry_all_compressed), ("Clear finished from queue", win.qm.clear_finished),
            ("Open download folder", win.open_folder), ("Mini Mode", win.toggle_mini), ("Rescan library", lambda: win.library.rescan()),
            ("Re-order dates", lambda: win.show_page("Tools")), ("Fix dates on older downloads", lambda: win.show_page("Tools")),
            ("Check TikWM certificate", lambda: (win.show_page("Tools"), win.tools._cert())),
            ("Switch to light theme", lambda: win.set_mode("Light")), ("Switch to dark theme", lambda: win.set_mode("Dark")),
            ("Follow Windows theme", lambda: win.set_mode("Auto")),
            ("moohyo labs on GitHub", open_github),
        ]
        self._fill("")

    def _fill(self, text: str) -> None:
        self.list.clear(); t = text.strip().lower()
        if t:
            posts, profiles = self.win.qm.parse(text)
            if posts or profiles:
                it = QListWidgetItem(f"Download {len(posts) + len(profiles)} item(s)"); it.setData(Qt.UserRole, lambda: self.win.qm.submit(text))
                self.list.addItem(it)
        for name, fn in self.commands:
            if not t or all(w in name.lower() for w in t.split()):
                it = QListWidgetItem(name); it.setData(Qt.UserRole, fn); self.list.addItem(it)
        if t and self.list.count() == 0:
            it = QListWidgetItem(f"Search the library for “{text.strip()}”"); it.setData(Qt.UserRole, lambda: self.win.search_library(text.strip()))
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self._fit_list()

    ROWS = 6                     # a compact list; type to narrow it down

    def _fit_list(self) -> None:
        """Exactly whole rows tall (at most ROWS), so the last item ends 12 px above the bottom like the
        first starts 12 px below the search box - no leftover strip when scrolled to the end."""
        n = max(1, min(self.ROWS, self.list.count()))
        rh = self.list.sizeHintForRow(0) if self.list.count() else 36
        self.list.setFixedHeight(n * max(rh, 1) + 2 * self.list.frameWidth())
        self.adjustSize()

    def showEvent(self, e):
        super().showEvent(e)
        from .widgets import _strip_win_popup_frame
        _strip_win_popup_frame(self)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Down, Qt.Key_Up):
            self.list.keyPressEvent(e)
        else:
            super().keyPressEvent(e)

    def _run(self) -> None:
        it = self.list.currentItem()
        self.close()
        if it:
            fn = it.data(Qt.UserRole)
            if fn:
                fn()


class MiniWindow(QWidget):
    """Small always-on-top drop box."""
    def __init__(self, win):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.win = win; self.setAcceptDrops(True); self.setFixedSize(340, 190); self.setWindowTitle(f"{APP_NAME} mini")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.bg = Backdrop(win.pal, radius=18); self.bg.set_motion(False)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.bg)
        v = QVBoxLayout(self.bg); v.setContentsMargins(14, 12, 14, 14); v.setSpacing(10)
        top = QHBoxLayout(); ic = QLabel(); ic.setPixmap(app_icon_pixmap(24)); top.addWidget(ic)
        self.name = Wordmark("TikVortex", 15, win.pal.wordmark); top.addWidget(self.name); top.addStretch()
        exp = button("", "ghost", "ph.arrows-out-simple-bold", win.pal); exp.setFixedSize(30, 28); exp.setToolTip("Back to the full window"); exp.clicked.connect(win.toggle_mini)
        top.addWidget(exp); v.addLayout(top)
        self.drop = EmptyBox("Drop or paste TikTok links", win.pal, accent=True, size=13); v.addWidget(self.drop, 1)
        r = QHBoxLayout(); self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setFixedHeight(7); r.addWidget(self.bar, 1)
        self.pause = button("", "ghost", "ph.pause-fill", win.pal); self.pause.setFixedSize(30, 28); self.pause.clicked.connect(lambda: win.qm.pause(not win.qm.paused))
        r.addWidget(self.pause); v.addLayout(r)
        self.info = label("Idle", "mute", 11); v.addWidget(self.info)
        QShortcut(QKeySequence.Paste, self, activated=self._paste)
        self.restyle(win.pal)

    def restyle(self, pal) -> None:
        self.bg.set_palette(pal); self.name.set_colors(pal.wordmark)
        self.drop.pal = pal; self.drop.update()

    def update_progress(self, label_: str, done: int, total: int) -> None:
        self.bar.setMaximum(max(1, total)); self.bar.setValue(done); self.info.setText(label_)

    def _paste(self) -> None:
        self.win.qm.submit(QGuiApplication.clipboard().text())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.win.handle_drop(e.mimeData())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()


HEADER_GAP = 16      # subtitle -> first card


class _Intro(QWidget):
    """Startup: the logo spins and settles on the backdrop, then the window comes into focus from a blur."""
    LOGO_MS, REVEAL_MS = 620, 520

    def __init__(self, win):
        super().__init__(win.bg); self.win = win
        self.setAttribute(Qt.WA_TransparentForMouseEvents); self.setGeometry(win.bg.rect())
        sharp = win.bg.grab()
        w, h = max(1, sharp.width() // 14), max(1, sharp.height() // 14)
        small = sharp.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.blur = small.scaled(sharp.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.blur.setDevicePixelRatio(sharp.devicePixelRatio())
        self.icon = app_icon_pixmap(96, 2.0)
        self.word = Wordmark("TikVortex", 40, win.pal.wordmark)
        self.word.setParent(self); self.word.adjustSize(); self.word.hide()
        self.t = 0.0
        from PySide6.QtCore import QVariantAnimation
        self.anim = QVariantAnimation(self); self.anim.setStartValue(0.0); self.anim.setEndValue(1.0)
        self.anim.setDuration(self.LOGO_MS + self.REVEAL_MS); self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self.deleteLater)

    def start(self) -> None:
        self.show(); self.raise_(); self.anim.start()

    def _tick(self, v) -> None:
        self.t = float(v); self.update()

    def paintEvent(self, e):
        from PySide6.QtGui import QPainter
        from .widgets import apple_curve
        total = self.LOGO_MS + self.REVEAL_MS; ms = self.t * total
        ease = apple_curve()
        a = ease.valueForProgress(min(1.0, ms / self.LOGO_MS))                       # logo entrance
        u = ease.valueForProgress(max(0.0, (ms - self.LOGO_MS) / self.REVEAL_MS))   # reveal
        p = QPainter(self); p.setRenderHint(QPainter.SmoothPixmapTransform); p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 - u); p.drawPixmap(0, 0, self.blur)                         # blur clears to sharp
        cover = max(0.0, 1.0 - 2.2 * u)
        if cover > 0:
            p.setOpacity(cover); bgpix = self.win.bg._img
            if bgpix is not None:
                p.drawPixmap(0, 0, bgpix)
            cx, cy = self.width() / 2, self.height() / 2 - 24
            p.save(); p.translate(cx, cy)
            scale = 0.72 + 0.28 * a + 0.06 * u
            p.rotate(-140 * (1 - a)); p.scale(scale, scale); p.setOpacity(cover * min(1.0, a * 1.4))
            p.drawPixmap(-48, -48, self.icon); p.restore()
            wm = self.word.pixmap()
            if wm is not None and not wm.isNull():
                k = ease.valueForProgress(max(0.0, min(1.0, (ms - 180) / (self.LOGO_MS - 180))))
                p.setOpacity(cover * k)
                ww = wm.width() / wm.devicePixelRatio()
                p.drawPixmap(int(cx - ww / 2), int(cy + 64 + 10 * (1 - k)), wm)


class _PageTransition(QWidget):
    """Lightweight cross-dissolve overlay for the outgoing page pixmap."""
    def __init__(self, parent: QWidget, rect: QRect, old_pix):
        super().__init__(parent)
        self.setGeometry(rect)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.old_pix = old_pix
        self.alpha = 1.0

    def set_progress(self, val) -> None:
        self.alpha = max(0.0, 1.0 - float(val))
        self.update()

    def paintEvent(self, e):
        if self.alpha <= 0.004 or self.old_pix is None:
            return
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.setOpacity(self.alpha)
        p.drawPixmap(0, 0, self.old_pix)


class MainWindow(QWidget):
    _update_found = Signal(str, str)

    def __init__(self, store, cfg, cache, base_dir: str):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.store, self.base_dir = store, base_dir
        self._update_found.connect(self._show_update)
        self.setWindowTitle(APP_NAME); self.setWindowIcon(QIcon(os.path.join(ASSETS, "icon.ico" if sys.platform == "win32" else "icon.png")))
        self.setAcceptDrops(True); self.setMinimumSize(1180, 720)
        self.pal = make_palette(store.ui("colour"), store.ui("theme_mode"), store.ui("glass"))
        widgets_mod.ACCENT[:] = list(self.pal.accent); widgets_mod.TEXT[0] = self.pal.text; widgets_mod.set_popup_colors(self.pal); widgets_mod.MOTION[0] = not store.ui("reduce_motion")
        app = QApplication.instance()
        app.setStyleSheet(stylesheet(self.pal))
        install_apple_tooltips(app)
        data = os.path.join(base_dir, ".tikvortex"); os.makedirs(data, exist_ok=True)
        self.qm = QueueManager(cfg, cache, os.path.join(data, "queue.json"))
        self._unfinished = self.qm.load_unfinished()
        self.covers = CoverLoader(os.path.join(data, "covers")); self.thumbs = ThumbLoader(os.path.join(data, "thumbs"))
        from . import avatars as avatars_mod
        self.avatars = avatars_mod.AvatarStore(base_dir, store); avatars_mod.STORE[0] = self.avatars

        self.bg = Backdrop(self.pal); self.bg.set_motion(self._bg_moves()); self.bg.installEventFilter(self)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.addWidget(self.bg)
        root = QHBoxLayout(self.bg); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(18)
        self.side = Sidebar(self.pal); root.addWidget(self.side)
        col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(0); root.addLayout(col, 1)
        self.header_top = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Fixed); col.addItem(self.header_top)
        self.header = Header(self.pal, self); col.addWidget(self.header)
        self.header_gap = QSpacerItem(0, 18, QSizePolicy.Minimum, QSizePolicy.Fixed); col.addItem(self.header_gap)
        self._col = col
        self.stack = QStackedWidget(); col.addWidget(self.stack, 1)
        self.download = DownloadPage(self.qm, self.pal, self.covers, store)
        self.library = LibraryPage(self.qm, self.pal, self.thumbs, store)
        self.highlights = HighlightsPage(self.qm, self.pal, self.covers)
        self.tools = ToolsPage(self.qm, self.pal, store, base_dir)
        self.settings = SettingsPage(store, self.pal)
        from .watchlist import Watchlist, WatchlistPage
        self.wl = Watchlist(store, self.qm); self.watch = WatchlistPage(self.wl, self.pal)
        self.pages = {"Download": self.download, "Library": self.library, "Highlights": self.highlights, "Tools": self.tools,
                      "Settings": self.settings, "Watchlist": self.watch}        # (Watchlist isn't in the sidebar's page list)
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.header.set_title_widths(self.pages.values())

        # window controls, toast
        self.ctrls = QWidget(self.bg); ch = QHBoxLayout(self.ctrls); ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(0)
        self.btn_min = self._winbtn("ph.minus-bold", lambda: self.animated_minimise()); self.btn_max = self._winbtn("ph.square-bold", self.toggle_max)
        self.btn_close = self._winbtn("ph.x-bold", self.close, "winclose")
        self.maker = QPushButton("moohyo labs"); self.maker.setObjectName("maker"); self.maker.setCursor(Qt.PointingHandCursor)
        self.maker.setToolTip("moohyo labs on GitHub"); self.maker.clicked.connect(open_github)
        ch.addWidget(self.maker, 0, Qt.AlignVCenter); ch.addSpacing(24)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            ch.addWidget(b)
        self.toast = Toast(self.bg, self.pal); self.toast.anchor = self.download.qcard
        self.status_card = StatusCard(self.bg); self.header.online.hovered.connect(self._show_status)

        # wiring
        self.side.logoMoved.connect(self._align_header)
        self.side.pageRequested.connect(self.show_page)
        self.side.watchRequested.connect(self.open_watchlist)
        self.wl.changed.connect(self._side_watch); self.wl.found.connect(self._watch_found)
        self.library.watchRequested.connect(lambda key: self.open_watchlist(self.wl.add(key) or ""))
        self.side.recentRequested.connect(self._open_recent)
        self.side.seeAllGroups.connect(lambda: self.open_watchlist(""))
        self.header.cmd.clicked.connect(self.open_palette); self.header.mini.clicked.connect(self.toggle_mini)
        self.header.folder.clicked.connect(self.open_folder)
        self.qm.batchChanged.connect(self._batch_progress); self.qm.activeChanged.connect(self._active)
        self.qm.batchFinished.connect(self._batch_finished); self.qm.message.connect(self._message)
        self.library.redownload.connect(lambda links: (self.qm.submit(" ".join(links), force=True, label="re-download"), self.show_page("Download")))
        self.library.statsChanged.connect(lambda n, b: self.side.set_storage(self.qm.output_dir, b))
        self.library.notify.connect(lambda title, body: self.toast.show_message(title, body, ms=3000))
        self.tools.notify.connect(lambda title, body: self.toast.show_message(title, body, ms=3200))
        self.download.clipboardToggled.connect(self._clipboard)
        self.settings.engineChanged.connect(self._engine_changed); self.settings.uiChanged.connect(self._ui_changed)
        self.settings.previewSound.connect(self.play_chime)
        self.tools.groupsChanged.connect(self._engine_changed)
        self.probe = StatusProbe(); self.probe.changed.connect(self._online)

        self._shortcuts()
        self._tray()
        self.mini_win = None
        self.sound = None
        self._clip_last = ("", 0.0)
        self._clip_timer = QTimer(self); self._clip_timer.timeout.connect(self._poll_clipboard)
        QGuiApplication.clipboard().dataChanged.connect(self._clip_changed)
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.store.ui("theme_mode") == "Auto" and self.apply_theme())
        except Exception:
            pass

        self._side_watch(); self.side.set_recent(store.ui("recent") or [])
        # creator suggestions (with pictures) under the Download, Library and Highlights boxes
        from .suggest import CreatorMemory, attach
        self.memory = CreatorMemory(store, self.qm); self._note_groups(); self.memory.tidy(self.wl)
        self.tools.groupsChanged.connect(self._note_groups)
        attach(self.download.edit, self.memory, token=True)
        attach(self.library.search, self.memory,
               lambda n, e: (self.library.search.clear(), self.library.pick_creator(n)))
        attach(self.highlights.user, self.memory, lambda n, e: (self.highlights.user.setText("@" + n), self.highlights.find()))
        self.library.creatorsScanned.connect(lambda c, l: (self.memory.note(list(c), "user", c, l),
                                                           self.side.set_recent(self.store.ui("recent") or [], c)))
        self.wl.changed.connect(lambda: self.memory.note([e["key"] for e in self.wl.entries() if e["kind"] == "user"]))
        self._refresh_folder_pill()
        self.side.set_storage(self.qm.output_dir, 0)
        self.show_page(store.ui("page") if store.ui("page") in self.pages else "Download")
        self._restore_geometry()
        if store.ui("clipboard_on_launch"):
            self.download.clip.setChecked(True)
        QTimer.singleShot(400, self.library.rescan)
        QTimer.singleShot(900, self._offer_resume)
        if store.ui("check_updates"):
            QTimer.singleShot(4000, self._check_updates)
        self._win_effects_done = False

    # ---------------------------------------------------------- chrome
    def _winbtn(self, icon_name, fn, obj="win") -> QPushButton:
        b = QPushButton(); b.setObjectName(obj); b.setFixedSize(46, 34); b.setProperty("win_icon", icon_name)
        b.setIcon(QIcon(centred_pixmap(icon_name, self.pal.mute, 12))); b.clicked.connect(fn); return b

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ctrls.adjustSize(); self.ctrls.move(self.bg.width() - self.ctrls.width() - 2, 0); self.ctrls.raise_()
        if self.toast.isVisible():
            self.toast.place()

    def showEvent(self, e):
        if not self._win_effects_done:
            # settle every layout before the first frame is drawn, so nothing jumps into place at startup
            self.bg.layout().activate(); self.side._fit_now(); self.header.fit(); self.bg.layout().activate()
        super().showEvent(e)
        if not self._win_effects_done and widgets_mod.MOTION[0] and not getattr(self, "_intro_done", False):
            self._intro_done = True
            try:
                self._intro = _Intro(self); self._intro.start()
            except Exception:
                pass
        if not self._win_effects_done:
            self._win_effects_done = True
            try:
                from .winfx import apply_window_effects
                apply_window_effects(self)
            except Exception:
                pass

    def nativeEvent(self, event_type, message):
        """Taskbar click on the open window: fade out first, then minimise (Windows asks with SC_MINIMIZE)."""
        if sys.platform == "win32" and event_type == b"windows_generic_MSG":
            import ctypes.wintypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0112 and (msg.wParam & 0xFFF0) == 0xF020 and not getattr(self, "_minimising", False):
                self.animated_minimise(); return True, 0
        return super().nativeEvent(event_type, message)

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            self.bg.pause(self.isMinimized())
            was_min = bool(getattr(e, "oldState", lambda: 0)() & Qt.WindowMinimized)
            if not self.isMinimized() and was_min and not self.store.ui("reduce_motion"):
                start = min(self.windowOpacity(), 0.0 if self.windowOpacity() < 1.0 else 1.0)
                if start < 1.0:                       # restored from the taskbar: fade back in
                    QTimer.singleShot(0, lambda: self._fade(self, start, 1.0, 190, lambda: self.setWindowOpacity(1.0)))
            elif not self.isMinimized() and self.windowOpacity() < 1.0:
                self.setWindowOpacity(1.0)
            self.btn_max.setProperty("win_icon", "ph.copy-simple-fill" if self.isMaximized() else "ph.square-bold")
            self.btn_max.setIcon(QIcon(centred_pixmap(self.btn_max.property("win_icon"), self.pal.mute, 12)))
            self.layout().setContentsMargins(0, 0, 0, 0)
        super().changeEvent(e)

    def _align_header(self, logo_centre: int) -> None:
        """Keep the page title row on the logo's centre line; the cards start level with the first page button."""
        hh = self.header.sizeHint().height()
        top = max(0, logo_centre - hh // 2)
        self.header_top.changeSize(0, top, QSizePolicy.Minimum, QSizePolicy.Fixed)
        # pages start a short, fixed distance under the subtitle (more room for the queue and library)
        self.header_gap.changeSize(0, HEADER_GAP, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self._col.invalidate()
        if self.bg.layout() is not None:
            self.bg.layout().activate()

    def toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _edges(self, pos: QPoint):
        r = self.rect(); ed = Qt.Edges()
        if self.isMaximized():
            return ed
        if pos.x() <= EDGE: ed |= Qt.LeftEdge
        if pos.x() >= r.width() - EDGE: ed |= Qt.RightEdge
        if pos.y() <= EDGE: ed |= Qt.TopEdge
        if pos.y() >= r.height() - EDGE: ed |= Qt.BottomEdge
        return ed

    def eventFilter(self, obj, e):
        if e.type() == QEvent.MouseMove and obj is self.bg and getattr(self, "_press_at", None) is not None:
            if e.buttons() & Qt.LeftButton and (e.globalPosition().toPoint() - self._press_at).manhattanLength() > 4:
                self._press_at = None
                if self.windowHandle():
                    self.windowHandle().startSystemMove()
            return True
        if e.type() in (QEvent.MouseMove, QEvent.HoverMove) and obj is self.bg:
            ed = self._edges(e.position().toPoint())
            cur = Qt.ArrowCursor
            if ed in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge): cur = Qt.SizeFDiagCursor
            elif ed in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge): cur = Qt.SizeBDiagCursor
            elif ed & (Qt.LeftEdge | Qt.RightEdge): cur = Qt.SizeHorCursor
            elif ed & (Qt.TopEdge | Qt.BottomEdge): cur = Qt.SizeVerCursor
            # Only change it when it actually changes: setting the window's cursor on every move
            # (hover moves arrive even over buttons) overrode the buttons' hand pointer on Windows.
            if cur != getattr(self, "_edge_cur", Qt.ArrowCursor):
                self._edge_cur = cur
                self.bg.setCursor(cur) if cur != Qt.ArrowCursor else self.bg.unsetCursor()
        elif e.type() == QEvent.MouseButtonPress and obj is self.bg and e.button() == Qt.LeftButton:
            ed = self._edges(e.position().toPoint())
            if ed and self.windowHandle():
                self.windowHandle().startSystemResize(ed); return True
            # Only start a window drag once the mouse actually moves: a plain click on empty space used to
            # enter Windows' move loop, which left hover and the hand pointer dead until the next alt-tab.
            self._press_at = e.globalPosition().toPoint(); return True
        elif e.type() == QEvent.MouseButtonRelease and obj is self.bg:
            self._press_at = None
        elif e.type() == QEvent.MouseButtonDblClick and obj is self.bg:
            self.toggle_max(); return True
        return super().eventFilter(obj, e)

    def _restore_geometry(self) -> None:
        g = self.store.ui("geometry")
        scr = QGuiApplication.primaryScreen().availableGeometry()
        if isinstance(g, list) and len(g) == 5:
            x, y, w, h, mx = g
            rect = QRect(x, y, w, h)
            if scr.intersects(rect) or any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
                self.setGeometry(rect)
                if mx:
                    QTimer.singleShot(0, self.showMaximized)
                return
        w, h = min(1600, int(scr.width() * 0.86)), min(900, int(scr.height() * 0.88))
        self.resize(max(w, 1180), max(h, 720)); self.move(scr.center() - self.rect().center())

    def save_geometry(self) -> None:
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        self.store.set_ui("geometry", [g.x(), g.y(), g.width(), g.height(), self.isMaximized()])

    # ---------------------------------------------------------- pages / theme
    def show_page(self, name: str, rescan_library: bool = True) -> None:
        if name not in self.pages:
            return
        page = self.pages[name]
        changed = self.stack.currentWidget() is not page
        animate = changed and self.isVisible() and not self.store.ui("reduce_motion")

        if getattr(self, "_page_anim", None) is not None:
            self._page_anim.stop()
            self._page_done()

        snap = self._snapshot_stack() if animate else None
        if changed:
            from PySide6.QtWidgets import QAbstractItemView, QScrollArea
            for area in page.findChildren(QScrollArea) + page.findChildren(QAbstractItemView):
                area.verticalScrollBar().setValue(0)

        self.stack.setCurrentWidget(page)
        self.side.set_page(name)
        self.header.set_page(page, self.pal)
        self.header.fit()

        if animate and snap is not None:
            old_pix, rect = snap
            self._animate_page(old_pix, rect)

        delay = 230 if animate else 0
        QTimer.singleShot(delay, lambda n=name, rs=rescan_library: self._after_page_shown(n, rs))
        if self.isVisible():
            self.raise_()

    def _after_page_shown(self, name: str, rescan_library: bool = True) -> None:
        if self.stack.currentWidget() is not self.pages.get(name):
            return
        self.store.set_ui("page", name)
        if name == "Library" and rescan_library:
            self.library.rescan()
        elif name == "Tools":
            self.tools.refresh()

    def _open_recent(self, entry: dict) -> None:
        self.show_page("Library", rescan_library=False)
        self.library.select_recent_batch(entry)

    def _snapshot_stack(self):
        r = self.stack.geometry()
        top = self.stack.mapTo(self.bg, QPoint(0, 0))
        rect = QRect(top, r.size())
        return self.bg.grab(rect), rect

    def _animate_page(self, old_pix, rect: QRect) -> None:
        from PySide6.QtCore import QVariantAnimation
        from .widgets import apple_curve
        overlay = _PageTransition(self.bg, rect, old_pix)
        overlay.show()
        overlay.raise_()
        self.toast.raise_()
        self.ctrls.raise_()

        anim = QVariantAnimation(self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(apple_curve())
        anim.valueChanged.connect(overlay.set_progress)
        anim.finished.connect(self._page_done)
        self._page_anim = anim
        self._cover = overlay
        anim.start()

    def _page_done(self) -> None:
        cover = getattr(self, "_cover", None)
        if cover is not None:
            cover.deleteLater()
            self._cover = None
        self._page_anim = None

    def set_mode(self, mode: str) -> None:
        self.store.set_ui("theme_mode", mode); self.settings.mode.select(mode, False); self.apply_theme()

    def apply_theme(self) -> None:
        """Re-theme behind a still snapshot of the window, then dissolve to the new look. Re-styling every
        widget makes them re-measure for a frame or two; doing it hidden means nothing visibly shakes."""
        cover = None
        if self.isVisible() and getattr(self, "pal", None) is not None:
            cover = _PageTransition(self.bg, self.bg.rect(), self.bg.grab()); cover.show(); cover.raise_()
        self.setUpdatesEnabled(False)
        try:
            self._apply_theme()
        finally:
            self.setUpdatesEnabled(True)
        if cover is None:
            return
        if not widgets_mod.MOTION[0]:
            QTimer.singleShot(30, cover.deleteLater); return
        from PySide6.QtCore import QVariantAnimation
        from .widgets import apple_curve
        anim = QVariantAnimation(cover); anim.setDuration(260); anim.setStartValue(0.0); anim.setEndValue(1.0)
        anim.setEasingCurve(apple_curve()); anim.valueChanged.connect(cover.set_progress); anim.finished.connect(cover.deleteLater)
        QTimer.singleShot(30, anim.start)          # let layouts settle under the snapshot first

    def _apply_theme(self) -> None:
        self.pal = make_palette(self.store.ui("colour"), self.store.ui("theme_mode"), self.store.ui("glass"))
        QApplication.instance().setStyleSheet(stylesheet(self.pal))
        widgets_mod.ACCENT[:] = list(self.pal.accent); widgets_mod.TEXT[0] = self.pal.text; widgets_mod.set_popup_colors(self.pal)
        for sg in self.findChildren(SegGroup):
            sg.update()
        self.bg.set_palette(self.pal); self.side.restyle(self.pal)
        self.header.set_page(self.stack.currentWidget(), self.pal); self.header.cmd.restyle(self.pal)
        for p in (self.header.mini, self.header.folder, self.header.online):
            p.restyle(self.pal)
        for page in self.pages.values():
            page.restyle(self.pal) if hasattr(page, "restyle") else None
        refresh_icons(self, self.pal)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            b.setIcon(QIcon(centred_pixmap(b.property("win_icon"), self.pal.mute, 12)))
        if self.mini_win:
            self.mini_win.restyle(self.pal); refresh_icons(self.mini_win, self.pal)
        self.toast.set_pal(self.pal)

    RELEASES_API = "https://api.github.com/repos/moohyolabs/TikVortex/releases/latest"

    def _check_updates(self) -> None:
        """Quietly ask GitHub for the latest release; a toast offers it if it's newer."""
        import threading
        from . import __version__

        def work():
            try:
                from tikvortex.core.tikwm import http
                j = http().get(self.RELEASES_API, timeout=10, headers={"Accept": "application/vnd.github+json"}).json()
                tag = str(j.get("tag_name") or "").lstrip("vV"); url = j.get("html_url") or GITHUB_URL
                newer = tuple(int(x) for x in tag.split(".") if x.isdigit()) > tuple(int(x) for x in __version__.split(".") if x.isdigit())
                if tag and newer:
                    self._update_found.emit(tag, url)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _show_update(self, tag: str, url: str) -> None:
        self.toast.show_message(f"TikVortex {tag} is available", "A newer version is out on GitHub.", "Open",
                                lambda: QDesktopServices.openUrl(QUrl(url)), ms=12000)

    def _bg_moves(self) -> bool:
        return bool(self.store.ui("moving_background")) and not self.store.ui("reduce_motion")

    def _ui_changed(self, key: str) -> None:
        if key in ("theme_mode", "colour", "glass"):
            self.apply_theme()
        elif key in ("reduce_motion", "moving_background"):
            self.bg.set_motion(self._bg_moves()); widgets_mod.MOTION[0] = not self.store.ui("reduce_motion")
        elif key in ("start_with_windows", "start_mode"):
            set_autostart(bool(self.store.ui("start_with_windows")), self.store.ui("start_mode") or "open")
        elif key == "tray" and self.tray:
            self.tray.setVisible(True)

    def _engine_changed(self) -> None:
        cfg = self.store.engine_config(); self.qm.set_config(cfg)
        self._side_watch(); self.tools.refresh(); self._refresh_folder_pill()

    def _note_groups(self) -> None:
        """Groups, and every account in them, count as creators you've done something with."""
        cr = self.qm.cfg.creators
        self.memory.note(list(cr), "group")
        self.memory.note([a.lstrip("@") for accs in cr.values() for a in accs])

    def _refresh_folder_pill(self) -> None:
        out = os.path.normpath(os.path.abspath(self.qm.output_dir))
        drive, rest = os.path.splitdrive(out)
        parts = [p for p in rest.split(os.sep) if p]
        short = out if len(out) <= 24 or len(parts) <= 1 else f"{drive}{os.sep}…{os.sep}{parts[-1]}"
        self.header.folder.lbl.setText(short)
        self.header.folder.setToolTip(f"Open {out}")
        self.header.fit()

    # ---------------------------------------------------------- actions
    def open_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices
        os.makedirs(self.qm.output_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.qm.output_dir))

    def open_palette(self) -> None:
        from .widgets import popup_recently_closed, track_popup
        if popup_recently_closed(self.header.cmd):     # clicking the bar while it's open closes it
            return
        p = CommandPalette(self); track_popup(self.header.cmd, p)
        g = self.header.cmd.mapToGlobal(QPoint(0, 0))
        p.move(g.x() + (self.header.cmd.width() - p.width()) // 2, g.y() + self.header.cmd.height() + 6)
        p.show(); p.edit.setFocus()

    def search_library(self, text: str) -> None:
        self.show_page("Library"); self.library.search.setText(text)

    def paste_and_download(self) -> None:
        t = QGuiApplication.clipboard().text()
        if t and self.qm.submit(t):
            self.show_page("Download")

    def handle_drop(self, md) -> None:
        parts = []
        if md.hasUrls():
            for u in md.urls():
                parts.append(f'"{u.toLocalFile()}"' if u.isLocalFile() else u.toString())
        if md.hasText():
            parts.append(md.text())
        if parts and self.qm.submit(" ".join(parts)):
            self.show_page("Download")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.handle_drop(e.mimeData())

    # ---------------------------------------------------------- window fades (mini mode, minimise, close)
    def _fade(self, w, start: float, end: float, ms: int, done=None) -> None:
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        if not widgets_mod.MOTION[0]:
            w.setWindowOpacity(end)
            if done:
                done()
            return
        a = QPropertyAnimation(w, b"windowOpacity", w); a.setDuration(ms); a.setStartValue(start); a.setEndValue(end)
        a.setEasingCurve(QEasingCurve.OutCubic if end > start else QEasingCurve.InCubic)
        if done:
            a.finished.connect(done)
        w._fade_anim = a; a.start()

    def _swap(self, out_w, in_w, show_in) -> None:
        """Fade one window out, then fade the other in."""
        def step2():
            out_w.hide(); out_w.setWindowOpacity(1.0)
            in_w.setWindowOpacity(0.0); show_in(); self._fade(in_w, 0.0, 1.0, 220)
        self._fade(out_w, 1.0, 0.0, 150, step2)

    def toggle_mini(self) -> None:
        if self.mini_win and self.mini_win.isVisible():
            self._swap(self.mini_win, self, lambda: (self.showNormal(), self.raise_(), self.activateWindow())); return
        if self.mini_win is None:
            self.mini_win = MiniWindow(self)
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.mini_win.move(scr.right() - self.mini_win.width() - 24, scr.bottom() - self.mini_win.height() - 24)
        self._swap(self, self.mini_win, self.mini_win.show)

    def animated_minimise(self) -> None:
        if getattr(self, "_minimising", False):
            return
        self._minimising = True

        def done():
            self.showMinimized(); self._minimising = False      # stays see-through until it's restored (then fades in)
        self._fade(self, 1.0, 0.0, 170, done)

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_palette)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.toggle_mini)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.download.submit)
        for i, name in enumerate(self.pages, 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda n=name: self.show_page(n))
        self._paste_sc = QShortcut(QKeySequence.Paste, self, activated=self._paste_anywhere)
        self._paste_sc.setContext(Qt.WindowShortcut)

    def _paste_anywhere(self) -> None:
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit,)) or w.__class__.__name__ == "QPlainTextEdit":
            w.paste(); return
        self.paste_and_download()

    # ---------------------------------------------------------- clipboard watch
    def _clipboard(self, on: bool) -> None:
        if on:
            self._clip_last = (extract_tiktok_url(QGuiApplication.clipboard().text()) or "", time.time())
            self._clip_timer.start(1500)
        else:
            self._clip_timer.stop()

    def _clip_changed(self) -> None:
        if self.download.clip.isChecked():
            self._poll_clipboard()

    def _poll_clipboard(self) -> None:
        url = extract_tiktok_url(QGuiApplication.clipboard().text() or "")
        if not url:
            return
        last, t = self._clip_last
        if url == last and time.time() - t < self.qm.cfg.clipboard_dedupe_sec + 3600:
            return
        self._clip_last = (url, time.time())
        self.qm.submit(url, label="clipboard")
        self.toast.show_message("Added from clipboard", url[:60] + ("…" if len(url) > 60 else ""), ms=3500)

    # ---------------------------------------------------------- queue feedback
    def _active(self, _n: int) -> None:
        # Count now rather than trusting the number in the signal: counts sent from download threads can
        # arrive after newer ones (that left a stale number on the badge after clearing the queue).
        n = self.qm.active_count()
        self.side.set_badge(n)
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} - {n} downloading" if n else APP_NAME)

    def _batch_progress(self, b) -> None:
        cur = self.qm.current
        if cur is None:
            return
        c = cur.counts(self.qm.items); done = c["total"] - c["left"]
        if cur.finished:
            sub = f"finished · {c['Original'] + c['Already saved']} original"
        elif cur.listing and not c["total"]:
            sub = "listing posts…"
        else:
            sub = f"{done} of {c['total']}" + (" · paused" if self.qm.paused else "")
        self.side.set_now(cur.label, done, c["total"], sub)
        if self.mini_win:
            self.mini_win.update_progress(f"{cur.label} · {sub}", done, c["total"])

    def _batch_finished(self, b, counts: dict) -> None:
        if not any(counts.values()):
            return
        saved_items = [
            self.qm.items[i] for i in b.items
            if i in self.qm.items and self.qm.items[i].state in ("Original", "Compressed", "Already saved")
        ]
        pids = [it.vid for it in saved_items if it.vid]
        creators = list(dict.fromkeys(it.username for it in saved_items if it.username))
        recent = [dict(
            label=b.label,
            count=counts["Original"] + counts["Compressed"] + counts["Already saved"],
            time=time.time(),
            pids=pids,
            creators=creators,
        )]
        recent += [r for r in (self.store.ui("recent") or []) if r.get("label") != b.label][:11]
        self.store.set_ui("recent", recent); self.side.set_recent(recent)
        got = counts["Original"] + counts["Compressed"]
        body = f"{counts['Original']} original · {counts['Compressed']} compressed · {fmt_bytes(b.bytes_done)}"
        if counts["Failed"]:
            body += f" · {counts['Failed']} failed"
        self.toast.show_message(f"{b.label} finished", body, "Open folder", self.open_folder)
        if self.store.ui("notify") and self.tray and not self.isActiveWindow():
            self.tray.showMessage(f"{b.label} finished", body, QIcon(os.path.join(ASSETS, "icon.png")), 6000)
        if self.store.ui("finish_sound") and got:
            self.play_chime()
        if counts["Failed"]:
            self._failed_links(b)
        QTimer.singleShot(800, self.library.rescan)

    def _failed_links(self, b) -> None:
        links = self.qm.failed_links(b)
        mode = self.store.ui("failed_links")
        if not links or mode == "Never":
            return
        if mode == "Ask":
            box = QMessageBox(self); box.setWindowTitle(APP_NAME); box.setIcon(QMessageBox.Question)
            box.setText(f"{len(links)} post(s) in “{b.label}” failed.\nSave the links to a .txt so you can retry them later?")
            cb = QCheckBox("Don't ask again - always save"); box.setCheckBox(cb)
            box.setStandardButtons(QMessageBox.Save | QMessageBox.No)
            if box.exec() != QMessageBox.Save:
                return
            if cb.isChecked():
                self.store.set_ui("failed_links", "Always"); self.settings.failed.select("Always", False)
        os.makedirs(self.qm.output_dir, exist_ok=True)
        path = os.path.join(self.qm.output_dir, f"failed - {time.strftime('%Y-%m-%d %H%M')}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(links) + "\n")
        self.toast.show_message("Failed links saved", os.path.basename(path) + " - drop it back in to retry", "Show", lambda: _reveal(path))

    def _message(self, level: str, text: str) -> None:
        self.toast.show_message("Heads up" if level == "warn" else "Working on it", text, ms=8000 if level == "warn" else 4000)

    def _online(self, info: dict) -> None:
        state = info.get("state")
        col = {"online": OK, "busy": WARN}.get(state, BAD)
        self.header.online.set_dot(col); self.header.set_status({"online": "Online", "busy": "Busy"}.get(state, "Offline"))
        lat = info.get("latency")
        head = {"online": "TikWM is responding normally", "busy": "TikWM is slow or busy right now",
                "offline": "Can't reach TikWM"}.get(state, "Can't reach TikWM")
        lines = [head]
        if lat is not None:
            lines.append(f"Replies in about {lat:.1f} s")
        lines.append(f"{api_call_count()} requests this session · checked {time.strftime('%H:%M', time.localtime(info.get('checked', time.time())))}")
        lines.append("Original files are usually ready within a minute" if state == "online" else
                     "Downloads may fall back to compressed - they're retried at the end")
        self.status_card.set_lines(lines[0], lines[1:], col)

    def _show_status(self, on: bool) -> None:
        if not on:
            self.status_card.conceal()
            return
        pill = self.header.online
        br = pill.mapTo(self.bg, pill.rect().bottomRight())
        self.status_card.adjustSize()
        self.status_card.move(br.x() - self.status_card.width(), br.y() + 8)
        self.status_card.reveal()

    def play_chime(self) -> None:
        path = os.path.join(ASSETS, "chime.wav")
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["afplay", path])
            else:
                QApplication.beep()
        except Exception:
            QApplication.beep()

    def _offer_resume(self) -> None:
        pending = self._unfinished; self._unfinished = []
        if pending:
            self.toast.show_message("Unfinished downloads", f"{len(pending)} post(s) didn't finish last time.", "Resume",
                                    lambda: self.qm.resume(pending), ms=0)

    # ---------------------------------------------------------- watchlist
    def open_watchlist(self, key: str = "") -> None:
        self.show_page("Watchlist")
        if key:
            self.watch.focus(key)

    def _side_watch(self) -> None:
        self.side.set_watchlist(self.wl.entries(), self.wl)

    def _watch_found(self, key: str, n: int, auto: bool) -> None:
        e = self.wl.entry(key) or {"kind": "user"}
        who = ("@" if e["kind"] == "user" else "") + key
        title = f"{n} new post{'s' if n != 1 else ''} from {who}"
        body = "Added to the queue." if auto else "Open the watchlist to download them."
        self.toast.show_message(title, body, "View", lambda: (self.bring_to_front(), self.open_watchlist(key)), ms=8000)
        if self.tray and not self.isActiveWindow() and self.store.ui("notify"):
            self.tray.showMessage(title, body, QIcon(os.path.join(ASSETS, "icon.png")), 6000)

    # ---------------------------------------------------------- tray / close
    def _tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(os.path.join(ASSETS, "icon.png")), self); self.tray.setToolTip(APP_NAME)
        m = styled_menu()
        m.addAction("Open TikVortex", self.bring_to_front); m.addAction("Paste and download", self.paste_and_download)
        self.tray_pause = m.addAction("Pause downloads", lambda: self.qm.pause(not self.qm.paused))
        m.addAction("Mini Mode", self.toggle_mini); m.addAction("Watchlist", lambda: (self.bring_to_front(), self.open_watchlist("")))
        m.addSeparator()
        theme = styled_menu(title="Theme"); m.addMenu(theme)
        for mode in ("Light", "Dark", "Auto"):
            theme.addAction(mode, lambda mo=mode: self.set_mode(mo))
        m.addSeparator(); m.addAction("Quit", self.quit_app)
        m.aboutToShow.connect(lambda: self.tray_pause.setText("Resume downloads" if self.qm.paused else "Pause downloads"))
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.bring_to_front() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def bring_to_front(self) -> None:
        if self.mini_win and self.mini_win.isVisible():
            self.mini_win.hide()
        self.showNormal() if self.isMinimized() or not self.isVisible() else None
        self.show(); self.raise_(); self.activateWindow()

    def closeEvent(self, e):
        # fade out first (whether to the tray or quitting), then close for real
        if self.isVisible() and widgets_mod.MOTION[0] and not getattr(self, "_faded_out", False):
            e.ignore(); self._faded_out = True
            self._fade(self, 1.0, 0.0, 200, self.close); return
        self._faded_out = False
        if self.windowOpacity() < 1.0:
            QTimer.singleShot(0, lambda: self.setWindowOpacity(1.0))
        self.save_geometry()
        if self.store.ui("tray") and self.tray and not getattr(self, "_quitting", False):
            e.ignore(); self.hide()
            if not getattr(self, "_told_tray", False):
                self._told_tray = True
                self.tray.showMessage(APP_NAME, "Still running in the tray - downloads carry on.", QIcon(os.path.join(ASSETS, "icon.png")), 3000)
            return
        if self.qm.busy() and not getattr(self, "_quitting", False):
            if not ConfirmDialog.ask(self, APP_NAME, "Downloads are still running. Quit anyway?\nUnfinished posts can be resumed next time.",
                                     self.pal, confirm_text="Quit", cancel_text="Stay", danger=True):
                e.ignore(); return
        self._shutdown(); e.accept(); QApplication.instance().quit()

    def quit_app(self) -> None:
        if self.qm.busy():
            self.bring_to_front()
            if not ConfirmDialog.ask(self, APP_NAME, "Downloads are still running. Quit anyway?\nUnfinished posts can be resumed next time.",
                                     self.pal, confirm_text="Quit", cancel_text="Stay", danger=True):
                return
        self._quitting = True; self.save_geometry(); self._shutdown(); QApplication.instance().quit()

    def _shutdown(self) -> None:
        self.qm.save_state(); self.probe.stop(); self.qm.shutdown()
        if self.tray:
            self.tray.hide()


GITHUB_URL = "https://github.com/moohyolabs"


def open_github() -> None:
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(GITHUB_URL))


def _reveal(path: str) -> None:
    from .pages.download import reveal
    reveal(path)


def set_autostart(on: bool, mode: str = "open") -> None:
    """mode: "open", "minimised" (to the taskbar) or "tray"."""
    flag = {"minimised": " --minimized", "tray": " --tray"}.get(mode, "")
    if sys.platform != "win32":
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    try:
        if on:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"{flag}'
            else:
                pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                from . import app_dir
                cmd = f'"{pyw if os.path.isfile(pyw) else sys.executable}" "{os.path.join(app_dir(), "TikVortex.pyw")}"{flag}'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)