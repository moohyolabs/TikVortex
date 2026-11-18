"""Sidebar: logo, divider, pages, now downloading, creator groups, recent batches and storage."""
from __future__ import annotations

import os
import shutil
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QSpacerItem,
                               QVBoxLayout, QWidget)

from .theme import Palette
from .widgets import Badge, EmptyBox, centred_pixmap, Thumb, Wordmark, app_icon_pixmap, glass, initials, label, wordmark

PAGES = (("Download", "mdi6.download"), ("Library", "ph.squares-four-fill"), ("Highlights", "ph.sparkle-fill"),
         ("Tools", "ph.wrench-fill"), ("Settings", "ph.gear-six-fill"))
SIDE_W = 275
NAV_H = 48
LOGO_PAD = 24        # top & bottom symmetry
LOGO_DIV_GAP = 18    # space between logo bottom and divider line
DIV_NAV_GAP = 13     # crisp gap between divider line and DOWNLOAD button (matches older build)
MIN_GAP = 10
MIN_GAP_RULE = 18   # the five section gaps never go below this (16:9 fits 3 rows in a list); more room = more rows
IDEAL_GAP = 16
MAX_GAP = 26
HEADING_GAP = 11
ROW_GAP = 11         # one spacing inside every section: heading letters -> first item, item -> item, bar -> text
SECTION_GAPS = 6     # divider->pages, pages->divider 2, divider 2->now, now->groups, groups->recent, recent->storage
ROW_PAD = 5          # a list row's own padding above/below its picture (rows sit flush; pictures end up 2*ROW_PAD apart)
SUB = 2 * ROW_PAD    # (fallback) inside a section: heading -> first picture = picture -> next picture = bar -> text
AV_PX = 30           # sidebar row pictures


def _ago(ts: float) -> str:
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    if d < 172800:
        return "yesterday"
    return time.strftime("%a %d %b", time.localtime(ts))


class NavButton(QPushButton):
    """Page button using the same stretched Inter Display Black font as the page titles."""
    ICON, PAD, SPACE, FONT = 26, 14, 12, 16.5

    def __init__(self, name: str, icon_name: str, pal: Palette):
        super().__init__()
        self.name, self.icon_name = name, icon_name
        self.setFixedHeight(NAV_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("nav")
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 12, 0)
        h.addStretch()
        self.badge = Badge("", pal.accent)
        self.badge.hide()
        h.addWidget(self.badge, 0, Qt.AlignVCenter)
        self.selected = False
        self.pal = pal
        self._pix = None
        self._lbl_sel = None
        self._lbl_norm = None
        self.restyle(pal)

    def set_selected(self, on: bool, pal: Palette) -> None:
        self.selected = on
        self.setObjectName("navsel" if on else "nav")
        self.style().unpolish(self)
        self.style().polish(self)
        self.restyle(pal)

    def restyle(self, pal: Palette) -> None:
        self.pal = pal
        self._pix = centred_pixmap(self.icon_name, pal.nav_icon, self.ICON, dpr=3.0)
        title = self.name.upper()
        self._lbl_sel = wordmark(title, self.FONT, pal.text, weight=QFont.Black, stretch=1.30, spacing=0.5, dpr=3.0)
        self._lbl_norm = wordmark(title, self.FONT, pal.nav_text, weight=QFont.Black, stretch=1.30, spacing=0.5, dpr=3.0)
        self.badge.accent = pal.accent
        self.badge.update()
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cy = self.height() / 2.0
        if self._pix is not None:
            p.drawPixmap(QRectF(self.PAD, cy - self.ICON / 2.0, self.ICON, self.ICON), self._pix, QRectF(self._pix.rect()))
        lbl = self._lbl_sel if self.selected else self._lbl_norm
        if lbl is not None:
            dpr = lbl.devicePixelRatio()
            lw, lh = lbl.width() / dpr, lbl.height() / dpr
            p.drawPixmap(QRectF(self.PAD + self.ICON + self.SPACE - 2.0, cy - lh / 2.0, lw, lh), lbl, QRectF(lbl.rect()))


class _Row(QFrame):
    """Sidebar group/recent row with the same rounded hover pill style as Creator Groups in Tools."""
    clicked = Signal()

    def __init__(self, seed: str, title: str, sub: str, right: QWidget | None = None, avatar: QWidget | None = None):
        super().__init__()
        self.setObjectName("grow")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 5, 8, 5)
        h.setSpacing(9)
        h.addWidget(avatar if avatar is not None else Thumb(seed, initials(seed), 26, 30, 7), 0, Qt.AlignVCenter)
        v = QVBoxLayout()
        v.setSpacing(1)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 700; font-size: 12px;")
        v.addWidget(t)
        v.addWidget(label(sub, "sub", 10))
        h.addLayout(v, 1)
        if right is not None:
            h.addWidget(right, 0, Qt.AlignVCenter)
        self.setToolTip(title)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class Sidebar(QFrame):
    pageRequested = Signal(str)
    watchRequested = Signal(str)
    recentRequested = Signal(dict)
    seeAllGroups = Signal()
    logoMoved = Signal(int)          # y of the logo's centre, in sidebar coordinates

    def __init__(self, pal: Palette):
        super().__init__()
        self.setObjectName("side")
        self.setFixedWidth(SIDE_W)
        self.pal = pal

        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(14, 0, 13, 0)
        self.v.setSpacing(0)

        # 4 equidistant inter-section gaps below the page buttons:
        # [0] pages -> now downloading, [1] now downloading -> groups,
        # [2] groups -> recent, [3] recent -> storage.
        self.gaps = [QSpacerItem(0, IDEAL_GAP, QSizePolicy.Minimum, QSizePolicy.Fixed) for _ in range(4)]
        self.bottom_pad = QSpacerItem(0, LOGO_PAD, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.div_space = QSpacerItem(0, LOGO_DIV_GAP + 1 + DIV_NAV_GAP, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.under = [QSpacerItem(0, 8, QSizePolicy.Minimum, QSizePolicy.Fixed) for _ in range(3)]
        self.bar_text = QSpacerItem(0, 7, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.heads: list[QWidget] = []
        self._line_y = 0.0

        # ---- Header: 24 px above logo, 18 px to divider, 13 px to DOWNLOAD button
        self.v.addSpacing(LOGO_PAD)
        lg = QHBoxLayout()
        lg.setSpacing(10)
        self.logo_row = lg
        self.logo_icon = QLabel()
        self.logo_icon.setPixmap(app_icon_pixmap(46))
        self.logo_icon.setFixedSize(46, 46)
        lg.addWidget(self.logo_icon)
        self.logo_name = Wordmark("TikVortex", 30, pal.wordmark)
        lg.addWidget(self.logo_name)
        lg.addStretch()
        self.v.addLayout(lg)
        self.v.addItem(self.div_space)

        # ---- Content below divider
        inner = QVBoxLayout()
        inner.setContentsMargins(3, 0, 4, 0)
        inner.setSpacing(0)
        self.v.addLayout(inner, 1)

        # pages
        self.nav: dict[str, NavButton] = {}
        for i, (name, ic) in enumerate(PAGES):
            b = NavButton(name, ic, pal)
            b.clicked.connect(lambda _=False, n=name: self.pageRequested.emit(n))
            inner.addWidget(b)
            self.nav[name] = b
            if i < len(PAGES) - 1:
                inner.addSpacing(4)
        inner.addItem(self.gaps[0])

        # now downloading
        hw = self._heading("NOW DOWNLOADING")[0]; inner.addWidget(hw); self.heads.append(hw)
        inner.addItem(self.under[0])
        self.now = glass("nowcard")
        self.now.setAttribute(Qt.WA_Hover)
        self.now.setCursor(Qt.PointingHandCursor)
        nv = QVBoxLayout(self.now)
        nv.setContentsMargins(12, 10, 12, 12)
        nv.setSpacing(6)
        self.now_title = QLabel("Nothing yet")
        self.now_title.setStyleSheet("font-weight: 800; font-size: 12px;")
        nv.addWidget(self.now_title)
        self.now_bar = QProgressBar()
        self.now_bar.setTextVisible(False)
        self.now_bar.setFixedHeight(6)
        nv.addWidget(self.now_bar)
        self.now_sub = label("Paste a link to start", "sub", 11)
        nv.addWidget(self.now_sub)
        self.now.mousePressEvent = lambda e: self.pageRequested.emit("Download")
        inner.addWidget(self.now)
        inner.addItem(self.gaps[1])

        # watchlist
        gh, self.see_all = self._heading("WATCHLIST", "View all")
        self.see_all.mousePressEvent = lambda e: self.seeAllGroups.emit()
        inner.addWidget(gh); self.heads.append(gh)
        inner.addItem(self.under[1])
        self.groups_box = QWidget()
        self.groups_v = QVBoxLayout(self.groups_box)
        self.groups_v.setContentsMargins(0, 0, 0, 0)
        self.groups_v.setSpacing(ROW_GAP)
        self.groups_v.setAlignment(Qt.AlignTop)
        inner.addWidget(self.groups_box)
        self.no_groups = EmptyBox("Nothing on your watchlist\nClick to watch a creator", pal)
        self.no_groups.setCursor(Qt.PointingHandCursor)
        self.no_groups.full_text = self.no_groups.text
        self.no_groups.clicked.connect(self.seeAllGroups.emit)
        self.groups_v.addWidget(self.no_groups)
        inner.addItem(self.gaps[2])

        # recent
        hw = self._heading("RECENT")[0]; inner.addWidget(hw); self.heads.append(hw)
        inner.addItem(self.under[2])
        self.recent_box = QWidget()
        self.recent_v = QVBoxLayout(self.recent_box)
        self.recent_v.setContentsMargins(0, 0, 0, 0)
        self.recent_v.setSpacing(ROW_GAP)
        self.recent_v.setAlignment(Qt.AlignTop)
        inner.addWidget(self.recent_box)
        self.no_recent = EmptyBox("Finished batches show up here", pal)
        self.no_recent.full_text = self.no_recent.text
        self.recent_v.addWidget(self.no_recent)
        inner.addItem(self.gaps[3])

        # storage
        self.store_bar = QProgressBar()
        self.store_bar.setTextVisible(False)
        self.store_bar.setFixedHeight(6)
        inner.addWidget(self.store_bar)
        inner.addItem(self.bar_text)
        self.store_lbl = label("", "sub", 11)
        inner.addWidget(self.store_lbl)
        inner.addItem(self.bottom_pad)
        inner.addStretch(1)              # nothing is spread between items; the spacing below is exact

        self.group_rows: list[_Row] = []
        self.recent_rows: list[_Row] = []
        self._groups: dict = {}
        self.set_page("Download")

    def _under_heading(self) -> int:
        """Space under a section heading so its letters sit HEADING_GAP above the first item."""
        l = label("GROUPS", "heading")
        l.ensurePolished()
        fm = QFontMetricsF(l.font())
        below = (l.sizeHint().height() - fm.height()) / 2 + fm.descent()
        return max(0, round(HEADING_GAP - below))

    def _heading(self, text: str, link: str = ""):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(label(text, "heading"))
        h.addStretch()
        a = label(link, "link")
        a.setCursor(Qt.PointingHandCursor)
        a.setVisible(bool(link))
        h.addWidget(a)
        return w, a

    # ---------------------------------------------------------- content
    def set_page(self, name: str) -> None:
        for n, b in self.nav.items():
            b.set_selected(n == name, self.pal)

    def set_badge(self, n: int) -> None:
        self.nav["Download"].badge.set_text(str(n) if n else "")

    def set_now(self, title: str, done: int, total: int, sub: str) -> None:
        self.now_title.setText(title)
        self.now_bar.setMaximum(max(1, total))
        self.now_bar.setValue(done)
        self.now_sub.setText(sub)

    def set_watchlist(self, entries: list, wl) -> None:
        """Watched creators: picture, name, when last checked, and a count circle for new posts waiting."""
        from .avatars import AvatarWidget
        from .creators import ago
        from .watchlist import _NewBadge
        self._groups = {k: list(v) for k, v in wl.qm.cfg.creators.items()}     # (recent rows use group pictures)
        for r in self.group_rows:
            r.setParent(None)
            r.deleteLater()
        self.group_rows = []
        for e in entries:
            key, group = e["key"], e["kind"] == "group"
            if key in wl.busy:
                sub = "Checking…"
            else:
                sub = ("Couldn't check" if e.get("error") else f"Checked {ago(e.get('checked') or e['last'])}") if e.get("last") else "Not checked yet"
            n = len(e.get("pending") or [])
            r = _Row(key, key if group else "@" + key, sub, right=_NewBadge(self.pal, str(n)) if n else None,
                     avatar=AvatarWidget("group" if group else "user", key, 30, wl.accounts(e) if group else None))
            r.setToolTip(("Group: " if group else "@") + key + "\nClick to see it on the watchlist")
            r.clicked.connect(lambda k=key: self.watchRequested.emit(k))
            self.groups_v.insertWidget(self.groups_v.indexOf(self.no_groups), r)
            self.group_rows.append(r)
        self._fit()

    def set_recent(self, recent: list, counts=None) -> None:
        """counts: {creator: posts in the library now}. A batch for a creator then shows how many of their posts
        you still have (0 if they've all been deleted); the record of what was downloaded is kept."""
        if counts is not None:
            self._lib_counts = counts
        counts = getattr(self, "_lib_counts", None)
        for r in self.recent_rows:
            r.setParent(None)
            r.deleteLater()
        self.recent_rows = []
        for e in recent[:8]:
            n = e.get("count", 0)
            entry = dict(e)
            lab = entry.get("label", "?")
            if counts is not None and not lab.split()[-1].isdigit():
                words = lab.split()
                if lab in self._groups:                   # a creator group: its accounts' posts
                    n = sum(counts.get(a.lstrip("@").lower(), 0) for a in self._groups[lab])
                elif words and words[0].startswith("@") and len(words) == 1:
                    n = counts.get(words[0].lstrip("@").lower(), 0)
            from .avatars import AvatarWidget
            if lab in self._groups:
                av = AvatarWidget("group", lab, 30, self._groups[lab])
            else:
                av = AvatarWidget("user", lab.split()[0].lstrip("@") if lab.startswith("@") else lab, 30)
            r = _Row(lab, f"{lab} · {n} post" + ("s" if n != 1 else ""), _ago(entry.get("time", time.time())), avatar=av)
            r.clicked.connect(lambda ent=entry: self.recentRequested.emit(ent))
            self.recent_v.insertWidget(self.recent_v.indexOf(self.no_recent), r)
            self.recent_rows.append(r)
        self._fit()

    def set_storage(self, folder: str, library_bytes: int) -> None:
        from .engine import fmt_bytes
        try:
            probe = folder if os.path.isdir(folder) else os.path.dirname(folder) or "."
            du = shutil.disk_usage(probe)
            drive = os.path.splitdrive(os.path.abspath(probe))[0] or "/"
            self.store_bar.setMaximum(1000)
            self.store_bar.setValue(int(1000 * du.used / du.total))
            self.store_lbl.setText(f"{fmt_bytes(library_bytes)} saved  ·  {fmt_bytes(du.free)} free on {drive}")
        except OSError:
            self.store_lbl.setText(f"{fmt_bytes(library_bytes)} saved")

    # ---------------------------------------------------------- layout: equal gaps below pages
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        if not getattr(self, "_fit_pending", False):
            self._fit_pending = True
            QTimer.singleShot(0, self._fit_now)

    # Spacing rules (all measured to what you can see, not to widget boxes):
    #  * T = sidebar top -> top of the logo icon = bottom of the storage text -> sidebar bottom
    #  * the divider sits T below the logo icon's bottom
    #  * divider -> pages -> NOW DOWNLOADING -> GROUPS -> RECENT -> storage bar: five equal gaps G
    #  * inside every section: ROW_GAP (heading letters -> first item, row -> row, bar -> text)
    #  * a taller window adds list rows rather than growing G past MAX_GAP
    def _metrics(self):
        if not hasattr(self, "_m"):
            img = self.logo_icon.pixmap().toImage(); dpr = img.devicePixelRatio() or 1.0
            rows = [y for y in range(img.height()) if any(img.pixelColor(x, y).alpha() > 40 for x in range(0, img.width(), 2))]
            icon_m = (rows[0] / dpr) if rows else 0.0
            h = label("GROUPS", "heading"); h.ensurePolished(); fh = QFontMetricsF(h.font())
            hh = h.sizeHint().height(); htop = (hh - fh.height()) / 2
            head_cap_top = htop + fh.ascent() - fh.capHeight(); head_base = htop + fh.ascent()
            self.store_lbl.ensurePolished(); fs = QFontMetricsF(self.store_lbl.font())
            sh = self.store_lbl.sizeHint().height(); stop = (sh - fs.height()) / 2
            self._m = dict(icon_m=icon_m, head_cap_top=head_cap_top, head_below=hh - head_base,
                           store_cap_top=stop + fs.ascent() - fs.capHeight(), store_below=sh - (stop + fs.ascent()))
        return self._m

    def _fit_now(self) -> None:
        self._fit_pending = False
        if self.height() < 100:
            return
        m = self._metrics()
        ng, nr = len(self.group_rows), len(self.recent_rows)
        if not hasattr(self, "_probe"):
            self._probe = _Row("x", "x", "x"); self._probe.hide()
        step = self._probe.sizeHint().height()
        # Measured from the rows themselves: pictures in neighbouring rows are `pic_gap` apart, and every
        # heading sits the same `pic_gap` above its first picture / card (the "sub-section" spacing).
        pic_gap = step - AV_PX; pad = pic_gap / 2
        y = lambda w: w.mapTo(self, w.rect().topLeft()).y()
        Z = QSizePolicy.Minimum, QSizePolicy.Fixed

        # 1) fixed spacing inside sections, zero section gaps, empty lists - then measure
        for i, sp in enumerate(self.under):              # [now card, groups list, recent list]
            sp.changeSize(0, max(0, round(pic_gap - (0 if i == 0 else pad) - m["head_below"])), *Z)
        self.groups_v.setSpacing(0); self.recent_v.setSpacing(0)
        self.bar_text.changeSize(0, max(0, round(pic_gap - m["store_cap_top"])), *Z)
        self.bottom_pad.changeSize(0, 0, *Z)
        for sp in self.gaps + [self.div_space]:
            sp.changeSize(0, 0, *Z)
        for row in self.group_rows + self.recent_rows:
            row.hide()
        for box, empty in ((self.groups_box, self.no_groups), (self.recent_box, self.no_recent)):
            empty.hide(); box.setFixedHeight(0); box.layout().invalidate()
        self.v.invalidate(); self.v.activate()
        icon_top = y(self.logo_icon) + m["icon_m"]
        T = icon_top                                         # sidebar top -> visible top of the icon
        icon_bot = y(self.logo_icon) + self.logo_icon.height() - m["icon_m"]
        logo_bottom = y(self.nav["Download"])               # where the pages start with no spacing
        line_y = icon_bot + T                                # divider: T below the icon
        lbl = self.store_lbl
        base0 = y(lbl) + lbl.height() - m["store_below"]     # storage text bottom with no gaps and empty lists
        cap = m["head_cap_top"]
        # free space for the five gaps G (three of them end at a heading's capitals)
        fixed = (self.height() - T) - base0 - (line_y - logo_bottom) + 3 * cap

        # lists are k*step tall; their last row's padding counts toward the following gap
        fixed += 2 * pad

        def G_for(k: int) -> float:
            return (fixed - k * step) / SECTION_GAPS

        k = 2
        while k < 40 and G_for(k + 1) >= MIN_GAP_RULE:
            k += 1
        G = G_for(k)

        # 2) rows per list
        g = (k + 1) // 2; r = k - g
        if ng < g and nr > r:
            give = min(g - max(ng, 1), nr - r); g -= give; r += give
        elif nr < r and ng > g:
            give = min(r - max(nr, 1), ng - g); r -= give; g += give
        g, r = max(1, g), max(1, r)
        for box, empty, rows, n, part in (
            (self.groups_box, self.no_groups, self.group_rows, g, "+  Watch another creator"),
            (self.recent_box, self.no_recent, self.recent_rows, r, ""),
        ):
            for i, row in enumerate(rows):
                row.setVisible(i < n)
            spare = n - min(len(rows), n)
            if spare and (not rows or part):            # one row's slot, drawn where that row's picture would be
                empty.text = empty.full_text if not rows else part
                # a little shorter than a row's slot (three quarters of the space between pictures, top and bottom), so the
                # outline - which is always drawn, unlike a hover box - doesn't butt against the rows around it
                empty.inset = round(pad * 0.75)
                empty.setFixedHeight(step); empty.show(); empty.update()
            box.setFixedHeight(n * step)
            box.layout().invalidate(); box.layout().activate()

        # 3) integer spacers from exact target positions (cumulative rounding keeps the total exact)
        sizes, acc, prev = [], 0.0, 0
        for i in range(SECTION_GAPS):
            acc += G; sizes.append(round(acc) - prev); prev = round(acc)
        self.div_space.changeSize(0, max(0, round(line_y - logo_bottom) + sizes[0]), *Z)
        # gaps[0] holds two gaps with the second divider between them
        g0 = sizes[1] + sizes[2] - round(cap)
        rest = [g0, sizes[3] - round(cap), round(sizes[4] - cap - pad), round(sizes[5] - pad)]
        for sp, h in zip(self.gaps, rest):
            sp.changeSize(0, max(0, h), *Z)
        self._div2_after = sizes[1]
        self.v.invalidate(); self.v.activate()
        # whatever rounding left over goes under the storage text, keeping it T from the bottom edge
        end = y(lbl) + lbl.height() - m["store_below"]
        self.bottom_pad.changeSize(0, max(0, round(self.height() - T - end)), *Z)
        self.v.invalidate(); self.v.activate()
        self._line_y = line_y

        self.see_all.setVisible(True)                 # "View all" opens the watchlist page
        self._fit_logo()
        self.update()
        self.logoMoved.emit(round(y(self.logo_icon) + self.logo_icon.height() / 2))

    def _fit_logo(self) -> None:
        """Size the TikVortex name so the logo spans the page buttons' width."""
        avail = self.width() - self.v.contentsMargins().right() - (self.logo_icon.width() + self.logo_row.spacing()
                                                                    + self.v.contentsMargins().left())
        lo, hi = 18.0, 40.0
        for _ in range(14):
            mid = (lo + hi) / 2
            self.logo_name._args = ("TikVortex", mid, self.pal.wordmark, self.logo_name._args[3])
            self.logo_name.render_now()
            if self.logo_name.width() <= avail:
                lo = mid
            else:
                hi = mid
        self.logo_name._args = ("TikVortex", lo, self.pal.wordmark, self.logo_name._args[3])
        self.logo_name.render_now()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.nav:
            first = self.nav["Download"]
            y = round(self._line_y) + 0.5
            st = self.nav["Settings"]; y2 = st.mapTo(self, st.rect().bottomLeft()).y() + 1 + getattr(self, "_div2_after", 0) + 0.5
            x0 = first.mapTo(self, first.rect().topLeft()).x() + 4.0
            x1 = x0 + first.width() - 8.0
            c = QColor(self.pal.text)
            c.setAlphaF(0.20 if self.pal.dark else 0.14)
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(c, 1.0))
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
            p.drawLine(QPointF(x0, y2), QPointF(x1, y2))

    def restyle(self, pal: Palette) -> None:
        self.pal = pal
        self.logo_name.set_colors(pal.wordmark)
        self.no_groups.pal = pal
        self.no_recent.pal = pal
        self.no_groups.update()
        self.no_recent.update()
        for b in self.nav.values():
            b.restyle(pal)
        self.update()