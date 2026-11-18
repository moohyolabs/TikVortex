"""Creator chips (with a count circle) and the creator gallery used by the Library."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
                               QWidget)

from .avatars import STORE, AvatarWidget, paint_avatar
from .widgets import SegGroup, button, label


def qc(c) -> QColor:
    """Theme colours can be rgba(...) strings, which QColor doesn't parse."""
    c = str(c).strip()
    if c.startswith("rgba"):
        r, g, b, a = [float(x) for x in c[c.index("(") + 1:-1].split(",")]
        q = QColor(int(r), int(g), int(b)); q.setAlphaF(a if a <= 1 else a / 255); return q
    return QColor(c)


def font(px: int, weight) -> QFont:
    f = QFont("Geist"); f.setPixelSize(px); f.setWeight(weight); f.setHintingPreference(QFont.PreferNoHinting); return f


def paint_badge(p: QPainter, cx: float, cy: float, text: str, bg: QColor, fg: QColor, h: float = 18) -> float:
    """A circle (a pill once the number is wide) with the number centred. Returns its width."""
    f = font(10, QFont.ExtraBold); w = max(h, QFontMetricsF(f).horizontalAdvance(text) + 10)
    r = QRectF(cx - w / 2, cy - h / 2, w, h)
    p.setPen(Qt.NoPen); p.setBrush(bg); p.drawRoundedRect(r, h / 2, h / 2)
    p.setFont(f); p.setPen(fg); p.drawText(r, Qt.AlignCenter, text); return w


def ago(ts: float) -> str:
    d = max(0, time.time() - ts)
    for n, unit in ((86400 * 365, "year"), (86400 * 30, "month"), (86400 * 7, "week"), (86400, "day"), (3600, "h"), (60, "min")):
        if d >= n:
            k = int(d // n)
            if unit in ("h", "min"):
                return f"{k} {unit} ago"
            return "yesterday" if unit == "day" and k == 1 else f"{k} {unit}{'s' if k != 1 else ''} ago"
    return "just now"


class CreatorChip(QAbstractButton):
    """Pill: [picture] name (count in a circle). kind: 'user', 'group', 'all' or 'gallery'."""
    H = 30

    def __init__(self, pal, name: str, count: int = 0, kind: str = "user", accounts=None):
        super().__init__(); self.pal, self.name, self.count, self.kind, self.accounts = pal, name, count, kind, accounts
        self.setCheckable(kind != "gallery"); self.setCursor(Qt.PointingHandCursor); self.setFixedHeight(self.H)
        self.setAttribute(Qt.WA_Hover); self.setToolTip({"gallery": "See every creator"}.get(kind, ""))
        self.setFixedWidth(int(self._layout()[-1]))

    def _text(self) -> str:
        return {"all": "All creators", "gallery": "Gallery"}.get(self.kind, self.name)

    def _layout(self):
        tw = QFontMetricsF(font(12, QFont.Bold)).horizontalAdvance(self._text())
        bw = max(18.0, QFontMetricsF(font(10, QFont.ExtraBold)).horizontalAdvance(str(self.count)) + 10)
        x = 31.0 if self.kind in ("user", "group", "gallery") else 12.0
        end = x + tw + (8 + bw + 6 if self.kind != "gallery" else 12)
        return x, tw, bw, end

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5); on = self.isChecked()
        a, b = QColor(self.pal.accent[0]), QColor(self.pal.accent[1])
        if on:
            g = QLinearGradient(r.topLeft(), r.topRight()); g.setColorAt(0, a); g.setColorAt(1, b)
            p.setPen(Qt.NoPen); p.setBrush(g)
        else:
            p.setPen(QPen(a if self.underMouse() else qc(self.pal.stroke), 1)); p.setBrush(qc(self.pal.glass2))
        if self.isDown():
            c = QColor(a); c.setAlphaF(0.30 if self.pal.dark else 0.20); p.setBrush(c)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x, tw, bw, _end = self._layout()
        if self.kind in ("user", "group"):
            paint_avatar(p, QRectF(5, 5, 20, 20), STORE[0], self.kind, self.name, self.accounts)
        elif self.kind == "gallery":
            import qtawesome as qta
            p.drawPixmap(11, 8, qta.icon("ph.squares-four-fill", color=self.pal.text).pixmap(QSize(14, 14)))
        p.setFont(font(12, QFont.Bold)); p.setPen(QColor("white") if on else QColor(self.pal.text))
        p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self._text())
        if self.kind != "gallery":
            if on:
                bg, fg = QColor(255, 255, 255, 60), QColor("white")
            else:
                bg = QColor(a); bg.setAlpha(70 if self.pal.dark else 40)
                fg = QColor(a).lighter(150) if self.pal.dark else QColor(a).darker(115)
            paint_badge(p, x + tw + 8 + bw / 2, self.height() / 2, str(self.count), bg, fg)


class _CountBadge(QWidget):
    """The gradient count badge on a gallery tile's picture."""
    def __init__(self, pal, text: str):
        super().__init__(); self.pal, self.text = pal, text
        self.setFixedSize(int(max(26, QFontMetricsF(font(10, QFont.ExtraBold)).horizontalAdvance(text) + 14)), 26)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QLinearGradient(r.topLeft(), r.bottomRight()); g.setColorAt(0, QColor(self.pal.accent[0])); g.setColorAt(1, QColor(self.pal.accent[1]))
        p.setPen(QPen(QColor("#1a1833" if self.pal.dark else "#ffffff"), 2.5)); p.setBrush(g); p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(font(11, QFont.Black)); p.setPen(QColor("white")); p.drawText(r, Qt.AlignCenter, self.text)


class CreatorTile(QFrame):
    clicked = Signal(str)
    menu = Signal(str, object)
    W, H = 176, 196

    def __init__(self, pal, c: dict):
        super().__init__(); self.setObjectName("grow"); self.setCursor(Qt.PointingHandCursor); self.key = c["key"]
        self.setFixedSize(self.W, self.H)
        v = QVBoxLayout(self); v.setContentsMargins(12, 16, 12, 12); v.setSpacing(4); v.setAlignment(Qt.AlignHCenter)
        holder = QWidget(); holder.setFixedSize(88, 88)
        av = AvatarWidget(c["kind"], c["key"], 84, c.get("accounts"), ring=pal.accent[0]); av.setParent(holder); av.move(2, 2)
        bad = _CountBadge(pal, str(c["count"])); bad.setParent(holder); bad.move(88 - bad.width(), 88 - bad.height())
        v.addWidget(holder, 0, Qt.AlignHCenter); v.addSpacing(6)
        t = QLabel(c["title"]); t.setAlignment(Qt.AlignCenter); t.setStyleSheet("font-weight: 800; font-size: 13px;")
        t.setText(t.fontMetrics().elidedText(c["title"], Qt.ElideRight, self.W - 24)); t.setToolTip(c["title"]); v.addWidget(t)
        from .engine import fmt_bytes
        v.addWidget(label(f"{c['count']} post{'s' if c['count'] != 1 else ''} · {fmt_bytes(c['size'])}", "mute", 11), 0, Qt.AlignHCenter)
        v.addWidget(label(f"Last saved {ago(c['last'])}", "mute", 10), 0, Qt.AlignHCenter)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.key)

    def contextMenuEvent(self, e):
        self.menu.emit(self.key, e.globalPos())


class CreatorGallery(QWidget):
    """Every creator as a tile (sits where the post grid is). Click one to see their posts."""
    picked = Signal(str)
    back = Signal()
    menu = Signal(str, object)

    def __init__(self, pal, store):
        super().__init__(); self.pal, self.store = pal, store; self.creators: list[dict] = []
        v = QVBoxLayout(self); v.setContentsMargins(8, 2, 14, 0); v.setSpacing(13)   # header 14 px from the top, like the queue's
        h = QHBoxLayout(); h.setSpacing(10)
        t = QLabel("CREATORS"); t.setObjectName("h2"); h.addWidget(t, 0, Qt.AlignVCenter)
        self.sub = label("", "mute", 12); h.addWidget(self.sub, 0, Qt.AlignVCenter); h.addStretch()
        self.sort = SegGroup(["Recent", "Most posts", "A–Z"], store.ui("gallery_sort") or "Recent")
        self.sort.changed.connect(lambda s: (store.set_ui("gallery_sort", s), self._fill())); h.addWidget(self.sort, 0, Qt.AlignVCenter)
        b = button("Back to posts", "ghost", "ph.arrow-left-bold", pal); b.setFixedHeight(34); b.clicked.connect(self.back.emit)
        h.addWidget(b, 0, Qt.AlignVCenter); v.addLayout(h)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        self.body = QWidget(); self.grid = QGridLayout(self.body); self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14); self.grid.setVerticalSpacing(14); self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.body); v.addWidget(self.scroll, 1)
        self._cols = 0

    def set_creators(self, creators: list[dict], animate: bool = False) -> None:
        self.creators = creators; self._animate = animate
        self.sub.setText(f"{len(creators)} creator{'s' if len(creators) != 1 else ''} · {sum(c['count'] for c in creators)} posts")
        self._fill()

    def _sorted(self):
        k = self.sort.value or self.store.ui("gallery_sort") or "Recent"
        if k == "Most posts":
            return sorted(self.creators, key=lambda c: (-c["count"], c["title"].lower()))
        if k == "A–Z":
            return sorted(self.creators, key=lambda c: c["title"].lower().lstrip("@"))
        return sorted(self.creators, key=lambda c: -c["last"])

    def _fill(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        vw = self.scroll.viewport().width()
        if vw < CreatorTile.W:                       # not laid out yet: use the gallery's own width
            vw = max(CreatorTile.W, self.width() - 22)
        cols = max(1, (vw + 14) // (CreatorTile.W + 14)); self._cols = cols
        tiles = []
        for i, c in enumerate(self._sorted()):
            t = CreatorTile(self.pal, c); t.clicked.connect(self.picked.emit); t.menu.connect(self.menu.emit); self.grid.addWidget(t, i // cols, i % cols)
            tiles.append(t)
        if getattr(self, "_animate", False):
            self._animate = False; self._cascade(tiles)

    def _cascade(self, tiles) -> None:
        """Opening the gallery: tiles fade up one after another (a quick cascade, row by row)."""
        from .widgets import MOTION
        if not MOTION[0]:
            return
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        for i, t in enumerate(tiles[:40]):
            fx = QGraphicsOpacityEffect(t); fx.setOpacity(0.0); t.setGraphicsEffect(fx)
            a = QPropertyAnimation(fx, b"opacity", t); a.setDuration(320); a.setStartValue(0.0); a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.OutCubic); a.finished.connect(lambda t=t: t.setGraphicsEffect(None))
            QTimer.singleShot(18 * i, a.start)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cols = max(1, (self.scroll.viewport().width() + 14) // (CreatorTile.W + 14))
        if cols != self._cols and self.creators:
            self._fill()
