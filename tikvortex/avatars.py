"""Profile pictures: fetched once per account, cached in .tikvortex/avatars, painted as circles.

Where the picture link comes from, in order:
  1. links saved when posts were downloaded (.tikvortex/meta/avatars.json, written by the engine)
  2. TikWM's user-info API
  3. TikWM's post API, using any downloaded post by that account (the post details carry the picture link)
If neither works the app keeps showing the gradient initials.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


class AvatarStore(QObject):
    ready = Signal(str)            # lower-case username whose picture just arrived
    _got = Signal(str, bytes)

    def __init__(self, base_dir: str, store=None):
        super().__init__()
        self.base = os.path.join(base_dir, ".tikvortex")
        self.dir = os.path.join(self.base, "avatars")
        self.store = store                       # app settings (group picture choices)
        self._img: dict[str, QImage] = {}
        self._circles: dict[tuple, QPixmap] = {}
        self._busy: set[str] = set()
        self._failed: set[str] = set()
        self._pids: dict[str, str] = {}          # username -> one of their downloaded post IDs
        self._got.connect(self._arrived)

    # ---------------------------------------------------------- lookups
    def image(self, username: str) -> Optional[QImage]:
        u = (username or "").lstrip("@").lower()
        if not u:
            return None
        if u in self._img:
            return self._img[u]
        path = os.path.join(self.dir, f"{u}.jpg")
        if os.path.isfile(path):
            img = QImage(path)
            if not img.isNull():
                self._img[u] = img
                return img
        self.request(u)
        return None

    def custom_image(self, path: str) -> Optional[QImage]:
        if path in self._img:
            return self._img[path]
        img = QImage(path) if path and os.path.isfile(path) else QImage()
        if img.isNull():
            return None
        self._img[path] = img
        return img

    def group_choice(self, group: str) -> str:
        pics = (self.store.ui("group_pics") if self.store is not None else None) or {}
        return pics.get(group, "")

    def set_group_choice(self, group: str, choice: str) -> None:
        if self.store is None:
            return
        pics = dict(self.store.ui("group_pics") or {})
        if choice:
            pics[group] = choice
        else:
            pics.pop(group, None)
        self.store.set_ui("group_pics", pics); self._circles.clear(); self.ready.emit("")

    def clear_cache(self) -> None:
        self._img.clear(); self._circles.clear(); self._failed.clear()

    def note_posts(self, pairs) -> None:
        for u, pid in pairs:
            u = (u or "").lstrip("@").lower()
            if u and pid and u not in self._pids:
                self._pids[u] = str(pid)

    def fetch_all(self) -> int:
        """Retry every downloaded creator without a cached picture; returns how many were asked for."""
        self._failed.clear(); n = 0
        for u in list(self._pids):
            if not os.path.isfile(os.path.join(self.dir, f"{u}.jpg")) and u not in self._busy:
                self.request(u); n += 1
        return n

    # ---------------------------------------------------------- fetching
    def request(self, u: str) -> None:
        if u in self._busy or u in self._failed:
            return
        self._busy.add(u)
        threading.Thread(target=self._fetch, args=(u,), daemon=True).start()

    def _link_for(self, u: str) -> str:
        try:
            with open(os.path.join(self.base, "meta", "avatars.json"), encoding="utf-8") as f:
                link = json.load(f).get(u) or ""
            if link:
                return link
        except (OSError, ValueError):
            pass
        try:
            from tikvortex.core.tikwm import http
            r = http().get("https://www.tikwm.com/api/user/info", params={"unique_id": u}, timeout=12)
            user = ((r.json() or {}).get("data") or {}).get("user") or {}
            link = user.get("avatarMedium") or user.get("avatarThumb") or user.get("avatarLarger") or ""
            if link:
                return link
        except Exception:
            pass
        pid = self._pids.get(u)
        if not pid:
            return ""
        try:
            from tikvortex.core.tikwm import submit_tikwm_quick
            entry = submit_tikwm_quick(f"https://www.tiktok.com/@{u}/video/{pid}") or {}
            return ((entry.get("meta") or {}).get("avatar")) or ""
        except Exception:
            return ""

    def _fetch(self, u: str) -> None:
        data = b""
        try:
            link = self._link_for(u)
            if link:
                from tikvortex.core.tikwm import http
                r = http().get(link, timeout=15, headers={"Referer": "https://www.tiktok.com/"})
                if r.ok and r.content and len(r.content) > 200:
                    data = r.content
        except Exception:
            data = b""
        self._got.emit(u, data)

    def _arrived(self, u: str, data: bytes) -> None:
        self._busy.discard(u)
        img = QImage.fromData(data) if data else QImage()
        if img.isNull():
            self._failed.add(u); return
        os.makedirs(self.dir, exist_ok=True)
        img.save(os.path.join(self.dir, f"{u}.jpg"), "JPG", 92)
        self._img[u] = img
        self._circles = {k: v for k, v in self._circles.items() if u not in k[0]}
        self.ready.emit(u)


# ---------------------------------------------------------------- painting
def _initials(name: str) -> str:
    from .widgets import initials
    return initials(name)


def _paint_circle(p: QPainter, r: QRectF, img: Optional[QImage], seed: str, ring: Optional[QColor] = None) -> None:
    path = QPainterPath(); path.addEllipse(r)
    p.save(); p.setClipPath(path)
    if img is not None:
        side = min(img.width(), img.height())
        src = QRectF((img.width() - side) / 2, (img.height() - side) / 2, side, side)
        p.drawImage(r, img, src)
    else:
        from .widgets import tile_colors
        a, b = tile_colors(seed); g = QLinearGradient(r.topLeft(), r.bottomRight()); g.setColorAt(0, QColor(a)); g.setColorAt(1, QColor(b))
        p.fillPath(path, g)
        f = QFont("Geist"); f.setHintingPreference(QFont.PreferNoHinting); f.setPixelSize(max(8, int(r.height() * 0.36))); f.setWeight(QFont.Bold)
        p.setFont(f); p.setPen(QColor(255, 255, 255, 235)); p.drawText(r, Qt.AlignCenter, _initials(seed))
    p.restore()
    if ring is not None:
        p.setPen(QPen(ring, 1.5)); p.setBrush(Qt.NoBrush); p.drawEllipse(r.adjusted(0.75, 0.75, -0.75, -0.75))


def paint_avatar(p: QPainter, r: QRectF, store: Optional[AvatarStore], kind: str, key: str, accounts=None,
                 border: Optional[QColor] = None) -> None:
    """kind 'user': key = @username.  kind 'group': key = group name, accounts = its @accounts
    (uses the chosen picture; with none chosen, two of its accounts overlap)."""
    p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
    if kind == "user" or store is None:
        _paint_circle(p, r, store.image(key) if store else None, key.lstrip("@")); return
    choice = store.group_choice(key)
    accs = [a.lstrip("@") for a in (accounts or [])]
    if choice.startswith("@"):
        _paint_circle(p, r, store.image(choice), key); return
    if choice.startswith("file:"):
        _paint_circle(p, r, store.custom_image(choice[5:]), key); return
    if choice == "initials" or not accs:
        _paint_circle(p, r, None, key); return
    imgs = [store.image(a) for a in accs[:2]]
    if len(accs) >= 2 and all(i is not None for i in imgs):
        s = r.width() * 0.74
        ring = border or QColor(26, 24, 52)
        _paint_circle(p, QRectF(r.left(), r.top(), s, s), imgs[0], accs[0])
        r2 = QRectF(r.right() - s, r.bottom() - s, s, s)
        p.setPen(Qt.NoPen); p.setBrush(ring); p.drawEllipse(r2.adjusted(-1.5, -1.5, 1.5, 1.5))
        _paint_circle(p, r2, imgs[1], accs[1]); return
    _paint_circle(p, r, imgs[0] if imgs and imgs[0] is not None else None, key)


def avatar_pixmap(store: Optional[AvatarStore], kind: str, key: str, px: int, accounts=None, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(px * dpr), int(px * dpr)); pm.setDevicePixelRatio(dpr); pm.fill(Qt.transparent)
    p = QPainter(pm); paint_avatar(p, QRectF(0, 0, px, px), store, kind, key, accounts); p.end()
    return pm


STORE: list = [None]       # the app's AvatarStore (set by the window)


from PySide6.QtWidgets import QWidget  # noqa: E402


class AvatarWidget(QWidget):
    """A round profile picture that fills in by itself once the picture has been fetched."""
    def __init__(self, kind: str, key: str, px: int, accounts=None, ring: Optional[str] = None):
        super().__init__(); self.kind, self.key, self.accounts, self.ring = kind, key, list(accounts or []), ring
        self.setFixedSize(px, px)
        if STORE[0] is not None:
            STORE[0].ready.connect(self._ready)

    def _ready(self, u: str) -> None:
        names = [self.key.lstrip("@").lower()] + [a.lstrip("@").lower() for a in self.accounts]
        if not u or u in names:
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        inset = 3.0 if self.ring else 0.0
        r = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        paint_avatar(p, r, STORE[0], self.kind, self.key, self.accounts)
        if self.ring:
            p.setRenderHint(QPainter.Antialiasing); p.setPen(QPen(QColor(self.ring), 2)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
