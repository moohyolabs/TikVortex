"""Highlights page: look up a creator's highlights, browse them by collection (or all at once),
pick stories and add them to the queue.

Story videos (the viewer's low-res copies) are fetched into a small local cache in the background,
so lengths show straight away and hover previews start instantly. Downloads are always the original."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QFontMetricsF, QGuiApplication, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from tikvortex.core.download import find_all_downloads
from tikvortex.core.highlights import HighlightError, creator_folder_for, fetch_highlights, post_url

from ..avatars import STORE, paint_avatar
from ..theme import OK, Palette
from ..widgets import Card, add_action, button, label, styled_menu, tile_colors
from .library import _FitLabel, _rgba, mp4_duration

ROLE = Qt.UserRole + 1
MIN_W = 170
CACHE_DAYS = 3


def _font(px: int, w) -> QFont:
    f = QFont("Geist"); f.setHintingPreference(QFont.PreferNoHinting); f.setPixelSize(px); f.setWeight(w); return f


def _mmss(sec: float) -> str:
    sec = int(sec); return f"{sec // 60}:{sec % 60:02d}"


def _pill(p: QPainter, r: QRectF, text: str, bg: QColor) -> None:
    p.setFont(_font(10, QFont.ExtraBold)); path = QPainterPath(); path.addRoundedRect(r, r.height() / 2, r.height() / 2)
    p.fillPath(path, bg); p.setPen(QColor("white")); p.drawText(r, Qt.AlignCenter, text)


def _pill_w(text: str) -> float:
    return QFontMetricsF(_font(10, QFont.ExtraBold)).horizontalAdvance(text) + 14


def _when(created: str) -> str:
    """'2025-03-07 18:02' -> '7 Mar 2025'."""
    try:
        return time.strftime("%d %b %Y", time.strptime(created[:10], "%Y-%m-%d")).lstrip("0")
    except (ValueError, TypeError):
        return created[:10] or "?"


class HighlightDelegate(QStyledItemDelegate):
    """Cards in the Library's format: thumbnail, @creator, then date (left) and type/length (right)."""
    def __init__(self, page):
        super().__init__(page); self.page = page

    def sizeHint(self, o, i):
        w = self.page.tile; return QSize(w, int(w * 1.33) + 46)

    def paint(self, p: QPainter, opt, idx):
        it = idx.data(ROLE); pal: Palette = self.page.pal
        p.save(); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(opt.rect).adjusted(5, 5, -5, -5)
        coll = it.get("_kind") == "collection"
        sel = bool(opt.state & QStyle.State_Selected) and not coll
        hover = bool(opt.state & QStyle.State_MouseOver)
        path = QPainterPath(); path.addRoundedRect(rect, 14, 14); p.fillPath(path, _rgba(pal.glass2))
        if sel:
            p.fillPath(path, QColor(139, 92, 246, 26))
            g = QLinearGradient(rect.topLeft(), rect.bottomRight()); g.setColorAt(0, QColor(pal.accent[0])); g.setColorAt(1, QColor(pal.accent[1]))
            p.setPen(QPen(g, 2))
        else:
            p.setPen(QPen(QColor(pal.accent[0]) if hover and coll else _rgba(pal.stroke), 1))
        p.drawPath(path)
        th = QRectF(rect.left() + 8, rect.top() + 8, rect.width() - 16, rect.width() * 1.33 - 16)
        tp = QPainterPath(); tp.addRoundedRect(th, 10, 10); p.setClipPath(tp)
        live = self.page.preview_frame if (not coll and self.page.preview_pid == it["post_id"]) else None
        pm = self.page.covers_mem.get(it["post_id"])
        if live is not None:
            s = live.size().scaled(th.size().toSize(), Qt.KeepAspectRatioByExpanding)
            p.drawImage(QRectF(th.center().x() - s.width() / 2, th.center().y() - s.height() / 2, s.width(), s.height()), live)
        elif pm:
            s = pm.size().scaled(th.size().toSize(), Qt.KeepAspectRatioByExpanding)
            p.drawPixmap(QRectF(th.center().x() - s.width() / 2, th.center().y() - s.height() / 2, s.width(), s.height()), pm, QRectF(pm.rect()))
        else:
            a, b = tile_colors(it["post_id"]); g = QLinearGradient(th.topLeft(), th.bottomRight()); g.setColorAt(0, QColor(a)); g.setColorAt(1, QColor(b)); p.fillRect(th, g)
        playing = self.page.preview_pid == it.get("post_id") and not coll
        if playing:
            dur = self.page.preview_dur or 1
            p.fillRect(QRectF(th.left(), th.bottom() - 3, th.width(), 3), QColor(255, 255, 255, 70))
            p.fillRect(QRectF(th.left(), th.bottom() - 3, th.width() * min(1.0, self.page.preview_pos / dur), 3), QColor("white"))
        p.setClipping(False)
        dark = QColor(0, 0, 0, 150)
        if coll:
            t = f"{it['count']}"
            _pill(p, QRectF(th.right() - _pill_w(t) - 6, th.top() + 6, _pill_w(t), 18), t, dark)
        else:
            if it.get("saved"):
                _pill(p, QRectF(th.left() + 6, th.top() + 6, _pill_w("✓ Saved"), 18), "✓ Saved", QColor(OK))
            if playing:
                _pill(p, QRectF(th.right() - _pill_w("MUTED") - 6, th.top() + 6, _pill_w("MUTED"), 18), "MUTED", dark)
            if it["is_photo"]:
                t = f"{it['images']} photo{'s' if it['images'] != 1 else ''}"
            else:
                total = self.page.durations.get(it["post_id"]) or 0
                if playing:
                    t = f"{_mmss(self.page.preview_pos / 1000)} / {_mmss(total)}" if total else _mmss(self.page.preview_pos / 1000)
                else:
                    t = _mmss(total) if total else ""
            if t:
                _pill(p, QRectF(th.right() - _pill_w(t) - 6, th.bottom() - 24, _pill_w(t), 18), t, dark)
        # text under the thumbnail, spaced like the Library's cards
        f1, f2 = _font(12, QFont.ExtraBold), _font(11, QFont.Medium)
        m1, m2 = QFontMetricsF(f1), QFontMetricsF(f2)
        left, width = th.left() + 2, th.width() - 4
        gap = max(2.0, (rect.bottom() - th.bottom() - m1.height() - m2.height()) / 3)
        y1 = th.bottom() + gap; y2 = y1 + m1.height() + gap
        p.setFont(f1); p.setPen(QColor(pal.text))
        if coll:
            p.drawText(QRectF(left, y1, width, m1.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       m1.elidedText(it["collection"], Qt.ElideRight, int(width)))
            sub = f"{it['count']} stor{'ies' if it['count'] != 1 else 'y'}"
            right = (f"{it['picked']} picked" if it["picked"] else (f"{it['saved_count']} saved" if it["saved_count"] else ""))
        else:
            av = round(m1.height()); u = self.page.username
            paint_avatar(p, QRectF(left, y1 + (m1.height() - av) / 2, av, av), STORE[0], "user", u)
            p.drawText(QRectF(left + av + 5, y1, width - av - 5, m1.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       m1.elidedText("@" + u, Qt.ElideRight, int(width - av - 5)))
            sub, right = _when(it["created"]), ("photos" if it["is_photo"] else "video")
            if self.page.where == "*":
                right = it["collection"]
        p.setFont(f2); p.setPen(QColor(pal.mute))
        rw = min(m2.horizontalAdvance(right), width * 0.5) if right else 0
        p.drawText(QRectF(left, y2, width - rw - 8, m2.height()), Qt.AlignLeft | Qt.AlignVCenter, m2.elidedText(sub, Qt.ElideRight, int(width - rw - 8)))
        if right:
            p.drawText(QRectF(left, y2, width, m2.height()), Qt.AlignRight | Qt.AlignVCenter, m2.elidedText(right, Qt.ElideRight, int(rw + 1)))
        p.restore()


class _Media(QWidget):
    """The story itself, edge to edge, with its controls drawn over it (they fade away while it plays)."""
    def __init__(self, viewer):
        super().__init__(); self.v = viewer; self.setMouseTracking(True); self.setCursor(Qt.PointingHandCursor)
        self.frame = None; self.hover = None; self.ui = 1.0; self._seeking = False
        self._idle = QTimer(self); self._idle.setSingleShot(True); self._idle.setInterval(2200); self._idle.timeout.connect(self._fade)
        self._anim = None

    # -- geometry of the drawn controls
    def _r(self):
        r = QRectF(self.rect()); w, h = r.width(), r.height()
        bar_y = h - 46
        return {
            "close": QRectF(w - 46, 14, 32, 32),
            "play": QRectF(14, bar_y, 32, 32),
            "mute": QRectF(w - 46, bar_y, 32, 32),
            "track": QRectF(100, bar_y + 14, w - 200, 4),
            "prev": QRectF(12, h / 2 - 18, 36, 36),
            "next": QRectF(w - 48, h / 2 - 18, 36, 36),
        }

    def _hit(self, pos):
        R = self._r()
        for k in ("close", "play", "mute", "prev", "next"):
            if k in ("play", "mute") and self.v.photo:
                continue
            if k in ("prev", "next") and not (self.v.photo and self.v.count() > 1):
                continue
            if R[k].adjusted(-4, -4, 4, 4).contains(pos):
                return k
        if not self.v.photo and R["track"].adjusted(-6, -12, 6, 12).contains(pos):
            return "track"
        return None

    def show_ui(self):
        if self._anim:
            self._anim.stop()
        self.ui = 1.0; self.update()
        if self.v.playing():
            self._idle.start()

    def _fade(self):
        if not self.v.playing() or self._seeking or self.hover:
            return
        from PySide6.QtCore import QVariantAnimation
        self._anim = QVariantAnimation(self); self._anim.setStartValue(1.0); self._anim.setEndValue(0.0); self._anim.setDuration(380)
        self._anim.valueChanged.connect(lambda x: (setattr(self, "ui", x), self.update())); self._anim.start()

    # -- painting
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()); path = QPainterPath(); path.addRoundedRect(r, 12, 12)          # concentric with the window's corners
        p.fillPath(path, QColor(0, 0, 0)); p.setClipPath(path)
        img = self.frame
        if img is not None and not img.isNull():
            s = img.size().scaled(self.size(), Qt.KeepAspectRatio)
            if abs(s.width() - r.width()) < 24 and abs(s.height() - r.height()) < 24:      # nearly the same shape: fill
                s = img.size().scaled(self.size(), Qt.KeepAspectRatioByExpanding)
            p.drawImage(QRectF((r.width() - s.width()) / 2, (r.height() - s.height()) / 2, s.width(), s.height()), img)
        else:
            p.setPen(QColor(255, 255, 255, 140)); p.setFont(_font(12, QFont.DemiBold))
            p.drawText(r, Qt.AlignCenter, self.v.message or "Loading…")
        a = self.ui
        # top: who and where, on a soft shade
        g = QLinearGradient(0, 0, 0, 96); g.setColorAt(0, QColor(0, 0, 0, int(150 * max(a, .55)))); g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, r.width(), 96), g)
        top = 14
        if self.v.photo and self.v.count() > 1:          # story-style segments for photo sets
            n = self.v.count(); gap = 4; sw = (r.width() - 28 - gap * (n - 1)) / n
            for i in range(n):
                seg = QRectF(14 + i * (sw + gap), 10, sw, 3)
                p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 235 if i <= self.v.idx else 80)); p.drawRoundedRect(seg, 1.5, 1.5)
            top = 22
        u = self.v.page.username
        paint_avatar(p, QRectF(14, top, 32, 32), STORE[0], "user", u)
        p.setPen(QColor("white")); p.setFont(_font(13, QFont.ExtraBold))
        p.drawText(QRectF(54, top - 1, r.width() - 110, 18), Qt.AlignLeft | Qt.AlignVCenter, "@" + u)
        p.setPen(QColor(255, 255, 255, 190)); p.setFont(_font(11, QFont.Medium))
        sub = "  ·  ".join(x for x in (self.v.it.get("collection"), _when(self.v.it.get("created", ""))) if x)
        p.drawText(QRectF(54, top + 17, r.width() - 110, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(_font(11, QFont.Medium)).elidedText(sub, Qt.ElideRight, r.width() - 110))
        R = self._r(); R["close"].moveTop(top)
        self._circle(p, R["close"], "ph.x-bold", 1.0, self.hover == "close", 14)
        if self.v.photo:
            if self.v.count() > 1 and a > 0.01:
                self._circle(p, R["prev"], "ph.caret-left-bold", a, self.hover == "prev", 16)
                self._circle(p, R["next"], "ph.caret-right-bold", a, self.hover == "next", 16)
            if self.v.count() > 1:
                t = f"{self.v.idx + 1} / {self.v.count()}"; w = _pill_w(t)
                _pill(p, QRectF(r.width() - w - 14, r.height() - 32, w, 18), t, QColor(0, 0, 0, 150))
            return
        if a <= 0.01:
            prog = self.v.position() / max(1, self.v.duration())          # a hairline of progress stays visible
            p.fillRect(QRectF(0, r.height() - 3, r.width(), 3), QColor(255, 255, 255, 60))
            p.fillRect(QRectF(0, r.height() - 3, r.width() * prog, 3), QColor(255, 255, 255, 230))
            return
        g = QLinearGradient(0, r.height() - 110, 0, r.height()); g.setColorAt(0, QColor(0, 0, 0, 0)); g.setColorAt(1, QColor(0, 0, 0, int(170 * a)))
        p.fillRect(QRectF(0, r.height() - 110, r.width(), 110), g)
        if not self.v.playing() and self.frame is not None:                 # big play button while paused
            c = QRectF(r.center().x() - 34, r.center().y() - 34, 68, 68)
            self._circle(p, c, "ph.play-fill", 1.0, False, 26, fill=QColor(0, 0, 0, 110), dx=2)
        self._circle(p, R["play"], "ph.pause-fill" if self.v.playing() else "ph.play-fill", a, self.hover == "play", 14)
        self._circle(p, R["mute"], "ph.speaker-slash-fill" if self.v.muted() else "ph.speaker-high-fill", a, self.hover == "mute", 14)
        tr = R["track"]; f = _font(11, QFont.DemiBold); p.setFont(f)
        p.setPen(QColor(255, 255, 255, int(235 * a)))
        p.drawText(QRectF(54, tr.center().y() - 9, tr.left() - 62, 18), Qt.AlignRight | Qt.AlignVCenter, _mmss(self.v.position() / 1000))
        p.drawText(QRectF(tr.right() + 8, tr.center().y() - 9, 40, 18), Qt.AlignLeft | Qt.AlignVCenter, _mmss(self.v.duration() / 1000))
        prog = min(1.0, self.v.position() / max(1, self.v.duration()))
        p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, int(70 * a))); p.drawRoundedRect(tr, 2, 2)
        fill = QRectF(tr.left(), tr.top(), tr.width() * prog, tr.height())
        gg = QLinearGradient(tr.topLeft(), tr.topRight()); c0 = QColor(self.v.page.pal.accent[0]); c1 = QColor(self.v.page.pal.accent[1])
        c0.setAlphaF(a); c1.setAlphaF(a); gg.setColorAt(0, c0); gg.setColorAt(1, c1); p.setBrush(gg); p.drawRoundedRect(fill, 2, 2)
        k = 14 if self.hover == "track" or self._seeking else 10
        p.setBrush(QColor(255, 255, 255, int(255 * a))); p.drawEllipse(QPointF(fill.right(), tr.center().y()), k / 2, k / 2)

    def _circle(self, p, rc, icon, a, hot, px, fill=None, dx=0):
        from ..widgets import centred_pixmap
        p.setPen(Qt.NoPen); p.setBrush(fill or QColor(255, 255, 255, int((58 if hot else 34) * a))); p.drawEllipse(rc)
        pm = centred_pixmap(icon, "#ffffff", px, self.devicePixelRatioF())
        p.setOpacity(a); p.drawPixmap(QPointF(rc.center().x() - px / 2 + dx, rc.center().y() - px / 2), pm); p.setOpacity(1.0)

    # -- mouse
    def mouseMoveEvent(self, e):
        pos = e.position(); h = self._hit(pos)
        if self._seeking:
            self._seek_to(pos.x())
        elif h != self.hover:
            self.hover = h; self.update()
        self.show_ui()
        if e.buttons() & Qt.LeftButton and self.v._drag is not None and not self._seeking:
            self.v.move(e.globalPosition().toPoint() - self.v._drag)

    def leaveEvent(self, e):
        self.hover = None; self._idle.start(600) if self.v.playing() else None; self.update()

    def _seek_to(self, x):
        tr = self._r()["track"]; frac = min(1.0, max(0.0, (x - tr.left()) / tr.width()))
        self.v.set_position(int(frac * self.v.duration())); self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position(); h = self._hit(pos); self.v._drag = None; self._moved = False
        if h == "track":
            self._seeking = True; self._seek_to(pos.x()); return
        if h is None:                                    # empty area: drag the window, or click to play/pause
            self.v._drag = e.globalPosition().toPoint() - self.v.frameGeometry().topLeft(); self._press = e.globalPosition().toPoint()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._seeking:
            self._seeking = False; self.show_ui(); return
        h = self._hit(e.position())
        moved = self.v._drag is not None and (e.globalPosition().toPoint() - self._press).manhattanLength() > 4
        self.v._drag = None
        if moved:
            return
        if h == "close":
            self.v.fade_close()
        elif h == "play" or (h is None and not self.v.photo):
            self.v.toggle()
        elif h == "mute":
            self.v.toggle_mute()
        elif h == "prev" or (h is None and self.v.photo and e.position().x() < self.width() / 3):
            self.v.go(-1)
        elif h == "next" or (h is None and self.v.photo):
            self.v.go(1)
        self.show_ui()


class StoryPlayer(QDialog):
    """A story viewer: the low-res copy plays edge to edge with its controls over it; photo stories page through
    their images. Downloads are always the original."""
    W = 380
    FOOT = 66

    def __init__(self, page, it: dict, src):
        super().__init__(page.window(), Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_DeleteOnClose); self.page, self.it = page, it
        self.photo = src is None; self.images: list = []; self.idx = 0; self.message = ""; self._drag = None
        from ..widgets import Backdrop
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        bg = Backdrop(page.pal, radius=22); bg.set_motion(False); outer.addWidget(bg)
        v = QVBoxLayout(bg); v.setContentsMargins(10, 10, 10, 10); v.setSpacing(0)
        self.media = _Media(self); v.addWidget(self.media, 1)
        foot = QWidget(); foot.setFixedHeight(self.FOOT); fh = QHBoxLayout(foot); fh.setContentsMargins(14, 12, 14, 2)        # lines up with the avatar and close button above; fh.setSpacing(12)
        col = QVBoxLayout(); col.setSpacing(1)
        a = QLabel("Low-quality preview"); a.setStyleSheet("font-size: 13px; font-weight: 800;")
        b = label("Downloads are full quality", "mute", 11)
        col.addStretch(); col.addWidget(a); col.addWidget(b); col.addStretch(); fh.addLayout(col, 1)
        if it.get("saved"):
            ok = label("✓  Saved", "", 12, 800); ok.setStyleSheet(f"color: {OK}; font-size: 12px; font-weight: 800;"); fh.addWidget(ok)
        else:
            dl = button("Download original", "primary", "mdi6.download", page.pal); dl.setFixedHeight(40)
            dl.setStyleSheet("padding: 0 18px;")          # the app's usual 12px corners
            dl.clicked.connect(lambda: (page.download([it]), self.fade_close())); fh.addWidget(dl)
        v.addWidget(foot)
        self._fit(9 / 16)
        if self.photo:
            page.load_photos(it)
        else:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
            self.player = QMediaPlayer(self); self.audio = QAudioOutput(self); self.player.setAudioOutput(self.audio)
            self.sink = QVideoSink(self); self.player.setVideoSink(self.sink); self.sink.videoFrameChanged.connect(self._frame)
            self.player.positionChanged.connect(lambda _ms: self.media.update())
            self.player.playbackStateChanged.connect(lambda _s: self.media.show_ui())
            self.player.setLoops(QMediaPlayer.Infinite)
            self.player.setSource(src); self.player.play()
        self.media.show_ui()
        self._closing = False
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)       # a click anywhere outside the viewer closes it

    def showEvent(self, e):
        super().showEvent(e)
        from ..widgets import animate_popup_in
        animate_popup_in(self, 200)                            # the app's usual dissolve, never a hard cut

    def eventFilter(self, obj, e):
        if e.type() == QEvent.MouseButtonPress and not self._closing:
            try:
                inside = self.frameGeometry().contains(e.globalPosition().toPoint())
            except AttributeError:
                return False
            if not inside:
                self.fade_close(); return True                  # the click only closes; it doesn't also act on the app
        return False

    def fade_close(self):
        """Fade out, then close."""
        from ..widgets import MOTION
        if self._closing:
            return True
        self._closing = True
        from PySide6.QtWidgets import QApplication
        QApplication.instance().removeEventFilter(self)
        if not MOTION[0]:
            return QDialog.close(self)
        from PySide6.QtCore import QVariantAnimation
        a = QVariantAnimation(self); a.setDuration(150); a.setStartValue(float(self.windowOpacity())); a.setEndValue(0.0)
        a.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        a.finished.connect(lambda: QDialog.close(self)); a.start()
        return True

    def _fit(self, aspect: float) -> None:
        """Size the window to the story's shape (between 9:16 and 4:5), so there are no black bars."""
        aspect = min(0.8, max(0.5625, aspect)); w = self.W; h = round(w / aspect)
        self.media.setFixedSize(w, h); self.setFixedSize(w + 20, h + 20 + self.FOOT)

    # ---- state for the controls
    def playing(self) -> bool:
        if self.photo:
            return False
        from PySide6.QtMultimedia import QMediaPlayer
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def position(self) -> int:
        return 0 if self.photo else self.player.position()

    def duration(self) -> int:
        return 0 if self.photo else self.player.duration()

    def muted(self) -> bool:
        return False if self.photo else self.audio.isMuted()

    def count(self) -> int:
        return max(len(self.images), self.it.get("images") or 1)

    def toggle(self) -> None:
        if not self.photo:
            self.player.pause() if self.playing() else self.player.play()

    def toggle_mute(self) -> None:
        self.audio.setMuted(not self.audio.isMuted()); self.media.update()

    def set_position(self, ms: int) -> None:
        self.player.setPosition(ms)

    def _frame(self, frame) -> None:
        img = frame.toImage()
        if img.isNull():
            return
        first = self.media.frame is None
        self.media.frame = img; self.media.update()
        if first:
            self._fit(img.width() / max(1, img.height()))

    # ---- photos
    def photo_arrived(self, i: int, img) -> None:
        if img.isNull():
            if not self.images:
                self.message = "Couldn't load this photo"; self.media.update()
            return
        first = not self.images
        while len(self.images) <= i:
            self.images.append(img)
        self.images[i] = img
        if first:
            self._fit(img.width() / max(1, img.height()))
        self._show_photo()

    def go(self, d: int) -> None:
        if len(self.images) > 1:
            self.idx = (self.idx + d) % len(self.images); self._show_photo()

    def _show_photo(self) -> None:
        self.media.frame = self.images[self.idx] if self.idx < len(self.images) else None; self.media.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.fade_close(); return
        if self.photo and k in (Qt.Key_Left, Qt.Key_Right):
            self.go(-1 if k == Qt.Key_Left else 1); return
        if not self.photo:
            if k == Qt.Key_Space:
                self.toggle(); self.media.show_ui(); return
            if k in (Qt.Key_Left, Qt.Key_Right):
                self.set_position(max(0, self.position() + (-5000 if k == Qt.Key_Left else 5000))); self.media.show_ui(); return
            if k == Qt.Key_M:
                self.toggle_mute(); return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):                     # the footer drags the window too
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def closeEvent(self, e):
        from PySide6.QtWidgets import QApplication
        QApplication.instance().removeEventFilter(self)
        if not self.photo:
            self.player.stop(); self.player.setSource(QUrl())
        super().closeEvent(e)




class StoryInfo(Card):
    """Details of the selected story, or a summary of several."""
    def __init__(self, page):
        super().__init__(margins=(18, 16, 18, 16), spacing=10); self.page = page; self.setFixedWidth(320)
        h = QHBoxLayout(); self.title = QLabel("STORY INFO"); self.title.setObjectName("h2"); h.addWidget(self.title); h.addStretch()
        x = button("", "ghost", "ph.x-bold", page.pal); x.setFixedSize(30, 30); x.clicked.connect(lambda: page.show_info(False)); h.addWidget(x)
        self.v.addLayout(h)
        self.cover = QLabel(); self.cover.setFixedHeight(220); self.cover.setAlignment(Qt.AlignCenter); self.v.addWidget(self.cover)
        self.desc = label("", "sub", 12); self.desc.setWordWrap(True); self.v.addWidget(self.desc)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(12); self.grid.setVerticalSpacing(8); self.v.addLayout(self.grid)
        self.v.addStretch(1)
        bh = QHBoxLayout(); bh.setSpacing(8)
        self.cp = button("Copy link", "ghost", "ph.link-simple-bold", page.pal); self.cp.clicked.connect(page.copy_links); bh.addWidget(self.cp)
        self.op = button("Open on TikTok", "ghost", "ph.arrow-square-out-bold", page.pal); self.op.clicked.connect(page.open_web); bh.addWidget(self.op)
        self.v.addLayout(bh)

    def show_items(self, items: list) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        pg = self.page
        if not items:
            self.title.setText("STORY INFO"); self.cover.clear(); self.desc.setText("Select a story to see its details.")
            self.cp.setEnabled(False); self.op.setEnabled(False); return
        self.cp.setEnabled(True); self.op.setEnabled(len(items) == 1)
        it = items[0]
        pm = pg.covers_mem.get(it["post_id"])
        if pm:
            self.cover.setPixmap(pm.scaled(QSize(160, 220) * self.devicePixelRatioF(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.cover.pixmap().setDevicePixelRatio(self.devicePixelRatioF())
        else:
            self.cover.clear()
        if len(items) == 1:
            self.title.setText("STORY INFO"); self.cp.setText(" Copy link")
            self.desc.setText(it["desc"] or "No caption")
            length = "-" if it["is_photo"] else (_mmss(pg.durations[it["post_id"]]) if pg.durations.get(it["post_id"]) else "-")
            rows = [("Creator", "@" + pg.username), ("Collection", it["collection"]), ("Posted", it["created"] or "-"),
                    ("Type", f"{it['images']} photos" if it["is_photo"] else "Video"), ("Length", length),
                    ("Status", "Saved" if it.get("saved") else "Not saved yet"), ("Post ID", it["post_id"])]
        else:
            self.title.setText("SELECTION"); self.cp.setText(" Copy links")
            cols = sorted({i["collection"] for i in items})
            self.desc.setText(f"{len(items)} stories from " + ", ".join(cols[:3]) + (f" +{len(cols) - 3}" if len(cols) > 3 else ""))
            vids = [i for i in items if not i["is_photo"]]; tot = sum(pg.durations.get(i["post_id"]) or 0 for i in vids)
            saved = sum(1 for i in items if i.get("saved"))
            dates = sorted(i["created"][:10] for i in items if i["created"])
            rows = [("Stories", str(len(items))), ("Mix", f"{len(vids)} video{'s' if len(vids) != 1 else ''} · {len(items) - len(vids)} photo post{'s' if len(items) - len(vids) != 1 else ''}"),
                    ("Length", _mmss(tot) + " of video" if tot else "-"), ("Saved", f"{saved} of {len(items)}"),
                    ("Posted", f"{dates[0]} - {dates[-1]}" if dates else "-")]
        for r, (k, val) in enumerate(rows):
            self.grid.addWidget(label(k, "mute", 11), r, 0, Qt.AlignTop)
            lv = label(val, "", 12); lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lv.setText(lv.fontMetrics().elidedText(val, Qt.ElideRight, 200))
            if k == "Status":
                lv.setStyleSheet(f"color: {OK if it.get('saved') else pg.pal.mute}; font-weight: 800;")
            self.grid.addWidget(lv, r, 1)
        self.grid.setColumnStretch(1, 1)


class HighlightsPage(QWidget):
    title = "HIGHLIGHTS"
    subtitle = "Pick stories from a creator's highlights"
    _loaded = Signal(str, list, str)
    _fetched = Signal(str)
    _photo_got = Signal(str, int, object)           # post ID, index, QImage

    def __init__(self, qm, pal: Palette, covers):
        super().__init__(); self.qm, self.pal, self.covers = qm, pal, covers
        self.username = ""; self.covers_mem: dict[str, QPixmap] = {}; self.items: list = []
        self.picked: set[str] = set()                 # chosen story IDs (kept while you move between collections)
        self.where = ""                                 # "" = collections, "*" = every story, else a collection name
        self.tile = 190
        self.cache_dir = os.path.join(os.path.dirname(os.path.abspath(qm.cfg.cache_path)), ".tikvortex", "story_cache")
        self.durations = self._load_durations()
        self.preview_pid = None; self.preview_frame = None; self.preview_pos = 0; self.preview_dur = 0
        self._player = None; self._hover_item = None; self._syncing = False; self._preview_rect = QRectF()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tv-story"); self._queued: set[str] = set()
        self._fetched.connect(self._story_cached)
        from ..loaders import ThumbLoader                 # when a story's cover won't load, its video's first frame is used
        self.frames = ThumbLoader(os.path.join(os.path.dirname(self.cache_dir), "story_thumbs"))
        self.frames.ready.connect(self._frame_cover)
        self._photo_got.connect(self._photo_loaded); self._photos: dict = {}
        self._hover_timer = QTimer(self); self._hover_timer.setSingleShot(True); self._hover_timer.setInterval(60)
        self._hover_timer.timeout.connect(self._start_preview)
        threading.Thread(target=self._prune_cache, daemon=True).start()

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
        top = Card(margins=(16, 12, 16, 12)); h = QHBoxLayout(); h.setSpacing(10)     # same box as the Library's search bar
        self.user = QLineEdit(); self.user.setPlaceholderText("@username"); self.user.returnPressed.connect(self.find); h.addWidget(self.user, 2)
        self.find_btn = button("Find highlights", "primary", "ph.sparkle-fill", pal); self.find_btn.clicked.connect(self.find); h.addWidget(self.find_btn)
        self.find_btn.setFixedHeight(38); self.find_btn.setStyleSheet("padding: 0 18px;")
        h.addSpacing(6)
        self.pid = QLineEdit(); self.pid.setPlaceholderText("…or paste a highlight post ID")
        self.pid.returnPressed.connect(self.add_ids); h.addWidget(self.pid, 2)
        add = button("Add", "ghost", "mdi6.download", pal); add.setFixedHeight(38); add.clicked.connect(self.add_ids); h.addWidget(add)
        top.v.addLayout(h); v.addWidget(top)

        # selection bar, the same as the Library's
        self.selbar = Card("row", margins=(14, 8, 10, 8)); sh = QHBoxLayout(); sh.setSpacing(8)
        self.sel_lbl = QLabel("None selected"); self.sel_lbl.setFixedWidth(118); sh.addWidget(self.sel_lbl)
        hint = _FitLabel(("Ctrl+click to add · Shift+click for a range · drag a box · Ctrl+A for all",
                          "Ctrl+click · Shift+click · drag · Ctrl+A", ""))
        hint.setObjectName("mute"); hint.setStyleSheet("font-size: 11px;"); hint.setAlignment(Qt.AlignCenter); hint.setMinimumWidth(1); hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        sh.addWidget(hint, 1)
        self.info_btn = button("Info", "ghost", "ph.info-fill", pal, checkable=True); self.info_btn.clicked.connect(lambda: self.show_info(not self.info.isVisible()))
        self.play_btn = button("Play", "ghost", "ph.play-fill", pal); self.play_btn.clicked.connect(lambda: self.play())
        self.copy_btn = button("Copy links", "ghost", "ph.link-simple-bold", pal); self.copy_btn.clicked.connect(self.copy_links)
        self.dl = button("Download", "primary", "mdi6.download", pal); self.dl.clicked.connect(lambda: self.download())
        self.clear_btn = button("", "ghost", "ph.x-bold", pal); self.clear_btn.setFixedWidth(34); self.clear_btn.setToolTip("Clear selection")
        self.clear_btn.clicked.connect(self._clear_selection)
        for b in (self.info_btn, self.play_btn, self.copy_btn, self.dl, self.clear_btn):
            sh.addWidget(b)
        # same height as the Library's bar (its buttons are all ghost-sized), and Download is wide enough
        # for "Download (999)", so the number appearing never pushes the other buttons along
        bh_ = self.info_btn.sizeHint().height(); self.dl.setFixedHeight(bh_); self.dl.setStyleSheet("padding: 0 12px;")
        self.dl.setText(" Download (999)"); self.dl.setFixedWidth(self.dl.sizeHint().width() + 6); self.dl.setText(" Download")
        self.selbar.v.addLayout(sh); v.addWidget(self.selbar)

        body = QHBoxLayout(); body.setSpacing(14)
        grid = Card(margins=(12, 14, 6, 12), spacing=8)
        gh = QHBoxLayout(); gh.setContentsMargins(8, 0, 8, 0); gh.setSpacing(8)
        self.back = button("Collections", "ghost", "ph.arrow-left-bold", pal); self.back.setFixedHeight(34)
        self.back.clicked.connect(lambda: self.open_collection("")); self.back.hide(); gh.addWidget(self.back, 0, Qt.AlignVCenter)
        self.head = QLabel("STORIES"); self.head.setObjectName("h2"); gh.addWidget(self.head, 0, Qt.AlignVCenter)
        gh.addSpacing(10)
        self.status = label("", "mute"); self.status.setMinimumWidth(1); self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        gh.addWidget(self.status, 1, Qt.AlignVCenter)
        self.all_btn = button("Show all stories", "ghost", "ph.squares-four-fill", pal); self.all_btn.setFixedHeight(34)
        self.all_btn.clicked.connect(lambda: self.open_collection("*")); gh.addWidget(self.all_btn, 0, Qt.AlignVCenter)
        self.sel_all = button("Select all", "ghost"); self.sel_all.setFixedHeight(34); self.sel_all.clicked.connect(lambda: self.list.selectAll())
        gh.addWidget(self.sel_all, 0, Qt.AlignVCenter)
        self.head_row = QWidget(); self.head_row.setLayout(gh); self.head_row.setFixedHeight(34); grid.v.addWidget(self.head_row)
        self.list = QListWidget(); self.list.setFrameShape(QListWidget.NoFrame)
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; padding: 0; }")
        self.list.setViewMode(QListWidget.IconMode); self.list.setResizeMode(QListWidget.Adjust); self.list.setSpacing(0)
        self.list.setMovement(QListWidget.Static); self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setSelectionRectVisible(True); self.list.setUniformItemSizes(True); self.list.setItemDelegate(HighlightDelegate(self))
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel); self.list.setMouseTracking(True)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.list.viewport().setAttribute(Qt.WA_Hover, True); self.list.viewport().installEventFilter(self)
        self.list.itemSelectionChanged.connect(self._sel); self.list.itemClicked.connect(self._clicked)
        self.list.itemDoubleClicked.connect(lambda li: self.play([li.data(ROLE)]) if li.data(ROLE).get("_kind") != "collection" else None)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu); self.list.customContextMenuRequested.connect(self._menu)
        from PySide6.QtGui import QKeySequence, QShortcut
        for k in (Qt.Key_Delete, Qt.Key_Escape):          # nothing to delete here: Delete clears the selection
            sc = QShortcut(QKeySequence(k), self); sc.setContext(Qt.WidgetWithChildrenShortcut); sc.activated.connect(self._clear_selection)
        grid.v.addWidget(self.list, 1)
        self.empty = label("Enter a username to see their highlights.", "empty"); self.empty.setAlignment(Qt.AlignCenter)
        grid.v.addWidget(self.empty, 1)                  # takes the spare room, so the header stays at the top
        body.addWidget(grid, 1)
        self.info = StoryInfo(self); self.info.hide(); body.addWidget(self.info)
        v.addLayout(body, 1); self.list.hide(); self._header(); self._sel()
        self._loaded.connect(self._show)

    # ---------------------------------------------------------- lookup
    def find(self) -> None:
        u = self.user.text().strip().lstrip("@")
        if not u:
            return
        self.username = u; self.find_btn.setEnabled(False)
        self.status.setText(f"Looking up @{u}'s highlights… (big profiles can take a minute)")
        cfg, out = self.qm.cfg, self.qm.output_dir

        def work():
            try:
                items = fetch_highlights(u, cfg)
                for it in items:
                    it["saved"] = bool(find_all_downloads(out, it["post_id"]))
                self._loaded.emit(u, items, "")
            except HighlightError as e:
                self._loaded.emit(u, [], str(e))
            except Exception as e:
                self._loaded.emit(u, [], f"{e.__class__.__name__}: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _show(self, user: str, items: list, err: str) -> None:
        self.find_btn.setEnabled(True); self.stop_preview()
        self.items = items; self.picked = set()
        if err or not items:
            self.status.setText(f"Highlights unavailable: {err}. You can still paste post IDs." if err else f"@{user} has no public highlights.")
            self.list.clear(); self.list.hide(); self.empty.show()
            self.empty.setText("Couldn't load highlights right now." if err else "No highlights to show."); self._header(); self._sel(); return
        for it in items:
            if it["cover"] and it["post_id"] not in self.covers_mem:
                self.covers.request(it["cover"], lambda pm, pid=it["post_id"]: self._cover(pid, pm))
        self.empty.hide(); self.list.show()
        self.open_collection("" if len(self._collections()) > 1 else "*")
        self._prefetch(items)
        QTimer.singleShot(2500, self._missing_covers)

    def _missing_covers(self) -> None:
        """Stories whose cover image didn't load get a frame from their (already cached) video instead."""
        for it in self.items:
            p = self._cached(it["post_id"])
            if not it["is_photo"] and it["post_id"] not in self.covers_mem and os.path.isfile(p):
                self.frames.request(it["post_id"], p)

    def _frame_cover(self, pid: str, pm: QPixmap) -> None:
        if pid not in self.covers_mem:
            self._cover(pid, pm)
        if self.info.isVisible():
            self.info.show_items(self.selected())

    def _collections(self) -> list[dict]:
        out: dict = {}
        for it in self.items:
            c = out.setdefault(it["collection"], {"_kind": "collection", "collection": it["collection"], "post_id": it["post_id"],
                                                  "count": 0, "saved_count": 0, "picked": 0})
            c["count"] += 1; c["saved_count"] += bool(it.get("saved")); c["picked"] += it["post_id"] in self.picked
        return list(out.values())

    # ---------------------------------------------------------- browsing
    def open_collection(self, name: str) -> None:
        """"" shows the collections, "*" every story, anything else one collection's stories."""
        from ..widgets import fade_in
        self.stop_preview(); self.where = name; self._syncing = True
        self.list.clear()
        if name == "":
            for c in self._collections():
                li = QListWidgetItem(); li.setData(ROLE, c); self.list.addItem(li)
            self.list.setSelectionMode(QAbstractItemView.NoSelection)
        else:
            self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
            shown = [it for it in self.items if name == "*" or it["collection"] == name]
            for it in shown:
                li = QListWidgetItem(); li.setData(ROLE, it); self.list.addItem(li)
                li.setSelected(it["post_id"] in self.picked)
            self._prefetch(shown, first=True)
        self._syncing = False; self.list.scrollToTop(); self._header(); self._sel(); QTimer.singleShot(0, self._fit)
        QTimer.singleShot(0, lambda: fade_in(self.list, 220))

    def _header(self) -> None:
        stories = self.where != ""; have = bool(self.items)
        self.back.setVisible(have and stories and len(self._collections()) > 1)
        self.all_btn.setVisible(have and not stories)
        self.sel_all.setVisible(have and stories)
        if not have:
            self.head.setText("STORIES"); return
        cols = self._collections()
        if not stories:
            self.head.setText(f"COLLECTIONS  ·  {len(cols)}")
            self.status.setText(f"@{self.username} · {len(self.items)} stories in {len(cols)} collection" + ("s" if len(cols) != 1 else ""))
        else:
            title = "ALL STORIES" if self.where == "*" else self.where.upper()
            self.head.setText(f"{title}  ·  {self.list.count()}"); self.status.setText(f"@{self.username}")

    def _clicked(self, li) -> None:
        it = li.data(ROLE)
        if it.get("_kind") == "collection":
            self.open_collection(it["collection"])

    def _clear_selection(self) -> None:
        self.picked.clear(); self.list.clearSelection(); self._sel()

    def selected(self) -> list:
        return [it for it in self.items if it["post_id"] in self.picked]

    def _sel(self) -> None:
        if self.where and not self._syncing:            # keep the picked set in step with what's selected on screen
            for i in range(self.list.count()):
                li = self.list.item(i); pid = li.data(ROLE)["post_id"]
                (self.picked.add if li.isSelected() else self.picked.discard)(pid)
        sel = self.selected(); n = len(sel); todo = sum(1 for s in sel if not s.get("saved"))
        if n:
            self.sel_lbl.setText(f"{n} selected"); self.sel_lbl.setStyleSheet(f"font-weight: 900; font-size: 13px; color: {self.pal.nav_icon};")
        else:
            self.sel_lbl.setText("None selected"); self.sel_lbl.setStyleSheet(f"font-weight: 800; font-size: 13px; color: {self.pal.mute};")
        self.dl.setText(f" Download ({todo})" if n else " Download"); self.dl.setEnabled(todo > 0)
        self.play_btn.setEnabled(any(s["is_photo"] or s.get("video") for s in sel))
        self.copy_btn.setEnabled(n > 0); self.clear_btn.setEnabled(n > 0); self.info_btn.setEnabled(bool(self.items))
        if self.info.isVisible():
            self.info.show_items(sel)

    def _cover(self, pid: str, pm: QPixmap) -> None:
        self.covers_mem[pid] = pm; self.list.viewport().update()

    # ---------------------------------------------------------- actions
    def show_info(self, on: bool) -> None:
        from ..widgets import fade_in
        was = self.info.isVisible(); self.info.setVisible(on); self.info_btn.setChecked(on)
        if on:
            self.info.show_items(self.selected())
            if not was:
                QTimer.singleShot(0, lambda: fade_in(self.info, 220))
        QTimer.singleShot(0, self._fit)

    def copy_links(self) -> None:
        sel = self.selected()
        if sel:
            QGuiApplication.clipboard().setText("\n".join(post_url(self.username, it) for it in sel))

    def open_web(self) -> None:
        sel = self.selected()
        if sel:
            QDesktopServices.openUrl(QUrl(post_url(self.username, sel[0])))

    def play(self, items=None) -> None:
        items = [i for i in (items or self.selected()) if i["is_photo"] or i.get("video")]
        if not items:
            return
        it = items[0]; self.stop_preview()
        StoryPlayer(self, it, None if it["is_photo"] else self._source(it)).show()

    # ---------------------------------------------------------- photos (full size, for the viewer)
    def load_photos(self, it: dict) -> None:
        """Fetch a photo story's images in the background; each arrives through _photo_got."""
        pid = it["post_id"]
        if pid in self._photos:
            for i, img in enumerate(self._photos[pid]):
                self._photo_got.emit(pid, i, img)
            return

        def work():
            import requests
            from PySide6.QtGui import QImage
            urls = [it["cover"]] if it.get("cover") else []
            if it.get("images", 1) > 1:                   # the viewer only gives the first one: ask TikWM for them all
                try:
                    from tikvortex.core.tikwm import submit_tikwm_quick
                    e = submit_tikwm_quick(post_url(self.username, it), self.qm.cfg) or {}
                    urls = [u for u in (e.get("images") or []) if isinstance(u, str)] or urls
                except Exception:
                    pass
            for i, u in enumerate(urls):
                img = QImage()
                try:
                    r = requests.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"})
                    if r.ok:
                        img.loadFromData(r.content)
                except Exception:
                    pass
                self._photo_got.emit(pid, i, img)
            if not urls:
                self._photo_got.emit(pid, 0, QImage())
        threading.Thread(target=work, daemon=True).start()

    def _photo_loaded(self, pid: str, i: int, img) -> None:
        got = self._photos.setdefault(pid, [])
        if not img.isNull():
            while len(got) <= i:
                got.append(img)
            got[i] = img
        for d in self.window().findChildren(QDialog):
            if isinstance(d, StoryPlayer) and d.it["post_id"] == pid:
                d.photo_arrived(i, img)

    def _menu(self, pos) -> None:
        li = self.list.itemAt(pos)
        if li is None:
            return
        it = li.data(ROLE)
        if it.get("_kind") == "collection":
            m = styled_menu(self)
            add_action(m, "Open collection", "ph.folder-open-fill", lambda: self.open_collection(it["collection"]))
            add_action(m, "Show all stories", "ph.squares-four-fill", lambda: self.open_collection("*"))
            m.exec(self.list.viewport().mapToGlobal(pos)); return
        if not li.isSelected():
            self.list.clearSelection(); li.setSelected(True)
        sel = self.selected(); one = len(sel) == 1
        m = styled_menu(self)
        if sel:
            add_action(m, "View photos" if sel[0]["is_photo"] else "Play preview",
                       "ph.image-fill" if sel[0]["is_photo"] else "ph.play-fill", lambda: self.play())
        add_action(m, "Info", "ph.info-fill", lambda: self.show_info(True))
        m.addSeparator()
        todo = [s for s in sel if not s.get("saved")]
        a = add_action(m, f"Download original ({len(todo)})" if not one else "Download original", "mdi6.download", lambda: self.download())
        a.setEnabled(bool(todo))
        add_action(m, "Copy link" if one else "Copy links", "ph.link-simple-bold", self.copy_links)
        add_action(m, "Copy post ID" if one else "Copy post IDs", "ph.hash-bold",
                   lambda: QGuiApplication.clipboard().setText("\n".join(s["post_id"] for s in sel)))
        if one:
            add_action(m, "Open on TikTok", "ph.arrow-square-out-bold", self.open_web)
        m.addSeparator()
        add_action(m, "Select all", "ph.checks-bold", self.list.selectAll)
        add_action(m, "Clear selection", "ph.x-bold", self._clear_selection)
        m.exec(self.list.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------- layout
    def resizeEvent(self, e):                       # cards stretch so each row fills the width exactly
        super().resizeEvent(e); QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        """One fixed card size for the page width. The Info panel takes exactly the room of whole columns
        (its width is set from the card size), so the cards never resize or shift when it opens or closes."""
        vw = self.list.viewport().width()
        if vw <= 50:
            return
        gap = self.layout().itemAt(2).layout().spacing() if self.layout().count() > 2 else 14
        full = vw + (self.info.width() + gap if self.info.isVisible() else 0)      # the grid's width without the panel
        cols = max(1, full // MIN_W); cell = max(MIN_W, (full - 1) // cols)
        n = 1
        while n * cell - gap < 300 and n < cols - 1:
            n += 1
        want = n * cell - gap
        if self.info.width() != want:
            self.info.setFixedWidth(want)
            if self.info.isVisible():
                QTimer.singleShot(0, self._fit); return
        size = QSize(cell, int(cell * 1.33) + 46)
        if size != self.list.gridSize():
            self.tile = cell; self.list.setGridSize(size); self.list.doItemsLayout()

    # ---------------------------------------------------------- story cache (instant lengths and previews)
    def _cached(self, pid: str) -> str:
        return os.path.join(self.cache_dir, f"{pid}.mp4")

    def _source(self, it: dict) -> QUrl:
        path = self._cached(it["post_id"])
        return QUrl.fromLocalFile(path) if os.path.isfile(path) else QUrl(it["video"])

    def _prefetch(self, items: list, first: bool = False) -> None:
        """Fetch story videos in the background; the collection you open goes to the front of the line."""
        todo = [it for it in items if not it["is_photo"] and it.get("video") and it["post_id"] not in self._queued
                and not os.path.isfile(self._cached(it["post_id"]))]
        for it in todo:
            self._queued.add(it["post_id"])
            self._pool.submit(self._fetch_story, it["post_id"], it["video"])
        for it in items:                                  # lengths of stories already in the cache
            p = self._cached(it["post_id"])
            if not it["is_photo"] and it["post_id"] not in self.durations and os.path.isfile(p):
                d = mp4_duration(p, os.path.getsize(p))
                if d:
                    self.durations[it["post_id"]] = round(d, 1)

    def _fetch_story(self, pid: str, url: str) -> None:
        import requests
        dst = self._cached(pid); tmp = dst + ".part"
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with requests.get(url, stream=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status_code != 200:
                    return
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        f.write(chunk)
            os.replace(tmp, dst); self._fetched.emit(pid)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _story_cached(self, pid: str) -> None:
        p = self._cached(pid)
        d = mp4_duration(p, os.path.getsize(p)) if os.path.isfile(p) else 0
        if d:
            self.durations[pid] = round(d, 1); self._save_durations_soon()
        if pid not in self.covers_mem and os.path.isfile(p):
            QTimer.singleShot(1500, lambda: self.frames.request(pid, p) if pid not in self.covers_mem else None)
        self.list.viewport().update()
        if self.info.isVisible():
            self.info.show_items(self.selected())

    def _prune_cache(self) -> None:
        try:
            cut = time.time() - CACHE_DAYS * 86400
            for f in os.listdir(self.cache_dir):
                p = os.path.join(self.cache_dir, f)
                if os.path.getmtime(p) < cut:
                    os.remove(p)
        except OSError:
            pass

    def _dur_path(self) -> str:
        return os.path.join(os.path.dirname(self.cache_dir), "highlight_lengths.json")

    def _load_durations(self) -> dict:
        try:
            with open(self._dur_path(), encoding="utf-8") as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def _save_durations_soon(self) -> None:
        if not hasattr(self, "_dur_timer"):
            self._dur_timer = QTimer(self); self._dur_timer.setSingleShot(True); self._dur_timer.setInterval(1500)
            self._dur_timer.timeout.connect(self._save_durations)
        self._dur_timer.start()

    def _save_durations(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._dur_path()), exist_ok=True)
            with open(self._dur_path(), "w", encoding="utf-8") as f:
                json.dump(self.durations, f)
        except OSError:
            pass

    # ---------------------------------------------------------- hover preview (muted)
    def eventFilter(self, obj, e):
        try:
            vp = self.list.viewport()
        except RuntimeError:
            return False
        if obj is vp:
            t = e.type()
            if t == QEvent.MouseMove:
                li = self.list.itemAt(e.position().toPoint())
                it = li.data(ROLE) if li is not None else None
                ok = it is not None and it.get("_kind") != "collection" and it.get("video") and not it["is_photo"]
                key = it["post_id"] if ok else None
                cur = self._hover_item["post_id"] if self._hover_item else self.preview_pid
                if key != cur:
                    self.stop_preview()
                    if ok:
                        self._hover_item = it; self._preview_rect = QRectF(self.list.visualItemRect(li))
                        self.preview_pid = key; self.preview_pos = 0                 # pills switch over straight away
                        self.preview_dur = (self.durations.get(key) or 0) * 1000
                        vp.update(self._preview_rect.toRect()); self._hover_timer.start()
            elif t in (QEvent.Leave, QEvent.Hide, QEvent.Wheel):
                self.stop_preview()
        return False

    def _start_preview(self) -> None:
        it = self._hover_item
        if it is None or not self.list.viewport().underMouse():
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        except ImportError:
            return
        if self._player is None:
            self._player = QMediaPlayer(self); self._sink = QVideoSink(self)
            self._player.setVideoSink(self._sink)            # no audio output at all: always muted
            self._player.setLoops(QMediaPlayer.Infinite)
            self._sink.videoFrameChanged.connect(self._frame)
            self._player.durationChanged.connect(self._got_duration)
        self._player.setSource(self._source(it)); self._player.play()

    def _got_duration(self, ms: int) -> None:
        if ms > 0 and self.preview_pid:
            self.preview_dur = ms
            if not self.durations.get(self.preview_pid):
                self.durations[self.preview_pid] = round(ms / 1000, 1); self._save_durations_soon()

    def _frame(self, frame) -> None:
        if self.preview_pid is None:
            return
        vp = self.list.viewport()
        if not (self.isVisible() and self.window().isActiveWindow() and
                self._preview_rect.contains(QPointF(vp.mapFromGlobal(QCursor.pos())))):
            self.stop_preview(); return
        img = frame.toImage()
        if img.isNull():
            return
        self.preview_frame = img; self.preview_pos = self._player.position()
        vp.update(self._preview_rect.toRect())

    def stop_preview(self) -> None:
        self._hover_timer.stop(); self._hover_item = None
        if self._player is not None:
            self._player.stop(); self._player.setSource(QUrl())        # release the cached file
        if self.preview_pid is not None:
            self.preview_pid = None; self.preview_frame = None; self.preview_pos = 0; self.list.viewport().update()

    # ---------------------------------------------------------- queue
    def download(self, items=None) -> None:
        sel = [it for it in (items or self.selected()) if not it.get("saved")]
        if not sel:
            return
        sel.sort(key=lambda it: int(it["post_id"]))
        folder = creator_folder_for(self.username, self.qm.cfg) if self.qm.cfg.folder_layout == "group" else None
        links = " ".join(post_url(self.username, it) for it in sel)
        self.qm.submit(links, folder=folder, label=f"@{self.username} highlights")
        mem = getattr(self.window(), "memory", None)
        if mem is not None:                               # downloading from someone is what puts them in the suggestions
            mem.note([self.username])
        self.status.setText(f"Added {len(sel)} to the queue")

    def add_ids(self) -> None:
        t = self.pid.text().strip()
        if t and self.qm.submit(t, label="highlight posts"):
            self.pid.clear()

    def restyle(self, pal: Palette) -> None:
        self.pal = pal; self.list.viewport().update(); self._sel()
