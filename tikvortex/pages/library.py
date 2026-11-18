"""Library page: everything you've downloaded, with search, filters and multi-select."""
from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import (QAbstractListModel, QEvent, QFile, QPoint, QItemSelection, QItemSelectionModel, QModelIndex, QRectF, QSize,
                            QSortFilterProxyModel, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QGridLayout, QSizePolicy, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QSlider, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from tikvortex.core.media_dates import split_media_name

from ..engine import fmt_bytes
from ..loaders import IMAGE_EXTS, VIDEO_EXTS
from ..theme import WARN, Palette
from ..avatars import STORE, AvatarWidget, paint_avatar
from ..widgets import Card, ConfirmDialog, SegGroup, add_action, button, glass, label, styled_menu, tile_colors
from .download import reveal_many

MEDIA_EXTS = VIDEO_EXTS + IMAGE_EXTS
ROLE = Qt.UserRole + 1


_DUR: dict = {}      # (path, size) -> seconds, so rescans don't re-read files


def mp4_duration(path: str, size: int = 0) -> float:
    """Length of an .mp4 from its 'mvhd' header (a few small reads, no decoding). 0 if unknown."""
    key = (path, size)
    if key in _DUR:
        return _DUR[key]
    import struct
    secs = 0.0
    try:
        with open(path, "rb") as f:
            end = os.fstat(f.fileno()).st_size

            def boxes(start, stop):
                pos = start
                while pos + 8 <= stop:
                    f.seek(pos); hdr = f.read(8)
                    if len(hdr) < 8:
                        return
                    n, kind = struct.unpack(">I4s", hdr); head = 8
                    if n == 1:
                        n = struct.unpack(">Q", f.read(8))[0]; head = 16
                    elif n == 0:
                        n = stop - pos
                    if n < head:
                        return
                    yield kind, pos + head, pos + n
                    pos += n
            for kind, a, b in boxes(0, end):
                if kind == b"moov":
                    for k2, a2, _b2 in boxes(a, b):
                        if k2 == b"mvhd":
                            f.seek(a2); ver = f.read(1)[0]; f.read(3)
                            if ver == 1:
                                f.read(16); scale, dur = struct.unpack(">IQ", f.read(12))
                            else:
                                f.read(8); scale, dur = struct.unpack(">II", f.read(8))
                            secs = dur / scale if scale else 0.0
                            break
                    break
    except (OSError, IndexError, struct.error):
        secs = 0.0
    _DUR[key] = secs
    return secs


def scan_library(output_dir: str, cache: dict) -> list[dict]:
    posts: dict[str, dict] = {}
    if not os.path.isdir(output_dir):
        return []
    for root, _dirs, names in os.walk(output_dir):
        rel = os.path.relpath(root, output_dir)
        top = "" if rel == "." else rel.split(os.sep)[0]
        for n in names:
            if not n.lower().endswith(MEDIA_EXTS) or n.endswith((".part", ".old")):
                continue
            pid, _idx = split_media_name(n, cache)
            if not pid:
                continue
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            e = posts.setdefault(pid, {"pid": pid, "files": [], "dirs": set(), "size": 0, "mtime": 0, "folder": top})
            e["files"].append(p); e["dirs"].add(root); e["size"] += st.st_size; e["mtime"] = max(e["mtime"], st.st_mtime)
    out = []
    for pid, e in posts.items():
        ent = cache.get(pid) or {}
        files = sorted(e["files"])
        photo = all(f.lower().endswith(IMAGE_EXTS) for f in files)
        vids = [f for f in files if f.lower().endswith(VIDEO_EXTS)]
        out.append({
            "pid": pid, "files": files, "main": (vids or files)[0], "size": e["size"], "mtime": e["mtime"],
            "dur": mp4_duration(vids[0], e["size"]) if vids and not photo else 0.0,
            "posted": int(ent.get("create_time") or 0) or (int(pid) >> 32),
            "creator": ent.get("username") or e["folder"] or "unknown", "folder": e["folder"],
            "caption": ent.get("title") or "", "kind": "photo" if photo else "video", "count": len(files),
            "compressed": (ent.get("quality") not in (None, "original")) and not photo and bool(ent),
            "dup": len(e["dirs"]) > 1 and not photo or (not photo and len(vids) > 1),
        })
    return out


class LibraryModel(QAbstractListModel):
    def __init__(self):
        super().__init__(); self.rows: list[dict] = []; self.thumbs: dict = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        r = self.rows[idx.row()]
        if role == ROLE:
            return r
        if role == Qt.DisplayRole:
            return f"{r['creator']} {r['caption']} {r['pid']}"
        return None

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel(); self.rows = rows; self.endResetModel()

    def thumb_ready(self, key: str, pm) -> None:
        self.thumbs[key] = pm
        for i, r in enumerate(self.rows):
            if r["pid"] == key:
                ix = self.index(i); self.dataChanged.emit(ix, ix); break


SORTS = {   # key: (menu text, proxy sort key, order)
    "posted_new": ("Date posted · newest", "Posted", Qt.DescendingOrder),
    "posted_old": ("Date posted · oldest", "Posted", Qt.AscendingOrder),
    "dl_new": ("Date downloaded · newest", "Downloaded", Qt.DescendingOrder),
    "dl_old": ("Date downloaded · oldest", "Downloaded", Qt.AscendingOrder),
    "size": ("Largest first", "Size", Qt.DescendingOrder),
    "creator": ("Creator A–Z", "Creator", Qt.DescendingOrder),
}
SORT_LABEL = {"posted_new": "Newest posted", "posted_old": "Oldest posted", "dl_new": "Newest downloaded",
              "dl_old": "Oldest downloaded", "size": "Largest first", "creator": "Creator A–Z"}


class LibraryFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__(); self.kind = "All"; self.creator = ""; self.text = ""; self.sort_key = "Posted"

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        if self.creator and r["folder"] != self.creator and r["creator"] != self.creator:
            return False
        if self.kind == "Videos" and r["kind"] != "video": return False
        if self.kind == "Photos" and r["kind"] != "photo": return False
        if self.kind.startswith("Compressed") and not r["compressed"]: return False
        if self.kind.startswith("Duplicates") and not r["dup"]: return False
        if self.text:
            hay = f"{r['creator']} {r['caption']} {r['pid']} {r['folder']}".lower()
            if not all(w in hay for w in self.text.lower().split()):
                return False
        return True

    def lessThan(self, a, b):
        ra, rb = self.sourceModel().rows[a.row()], self.sourceModel().rows[b.row()]
        k = {"Posted": "posted", "Downloaded": "mtime", "Size": "size", "Creator": "creator"}.get(self.sort_key, "posted")
        if k == "creator":                        # A-Z reads top to bottom, so invert for the descending sort
            return (ra["creator"].lower(), -ra["posted"]) > (rb["creator"].lower(), -rb["posted"])
        return (ra[k] or 0) < (rb[k] or 0)


class TileDelegate(QStyledItemDelegate):
    def __init__(self, page):
        super().__init__(page); self.page = page

    def sizeHint(self, option, idx):
        w = self.page.tile; return QSize(w, int(w * 1.33) + 46)

    def paint(self, p: QPainter, opt, idx):
        r = idx.data(ROLE); pal: Palette = self.page.pal
        p.save(); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(opt.rect).adjusted(5, 5, -5, -5)
        sel = bool(opt.state & QStyle.State_Selected)
        path = QPainterPath(); path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, QColor(pal.glass2) if pal.glass2.startswith("#") else _rgba(pal.glass2))
        if sel:
            p.fillPath(path, QColor(139, 92, 246, 26))
            g = QLinearGradient(rect.topLeft(), rect.bottomRight()); g.setColorAt(0, QColor(pal.accent[0])); g.setColorAt(1, QColor(pal.accent[1]))
            p.setPen(QPen(g, 2)); p.drawPath(path)
        else:
            p.setPen(QPen(_rgba(pal.stroke), 1)); p.drawPath(path)
        th = QRectF(rect.left() + 8, rect.top() + 8, rect.width() - 16, rect.width() * 1.33 - 16)
        tp = QPainterPath(); tp.addRoundedRect(th, 10, 10); p.setClipPath(tp)
        pm = self.page.model.thumbs.get(r["pid"])
        live = self.page.preview_frame if self.page.preview_pid == r["pid"] else None
        if live is not None:                     # muted hover preview playing
            s = live.size().scaled(th.size().toSize(), Qt.KeepAspectRatioByExpanding)
            p.drawImage(QRectF(th.center().x() - s.width() / 2, th.center().y() - s.height() / 2, s.width(), s.height()), live)
            p.fillRect(QRectF(th.left(), th.bottom() - 3, th.width(), 3), QColor(255, 255, 255, 70))
            p.fillRect(QRectF(th.left(), th.bottom() - 3, th.width() * self.page.preview_progress, 3), QColor("white"))
        elif pm is not None:
            s = pm.size().scaled(th.size().toSize(), Qt.KeepAspectRatioByExpanding)
            p.drawPixmap(QRectF(th.center().x() - s.width() / 2, th.center().y() - s.height() / 2, s.width(), s.height()), pm, QRectF(pm.rect()))
        else:
            a, b = tile_colors(r["creator"]); g = QLinearGradient(th.topLeft(), th.bottomRight()); g.setColorAt(0, QColor(a)); g.setColorAt(1, QColor(b))
            p.fillRect(th, g); p.setPen(QColor(255, 255, 255, 230)); f = _font(22, QFont.Bold); f.setPixelSize(22); f.setWeight(QFont.Bold); p.setFont(f)
            p.drawText(th, Qt.AlignCenter, "▦" if r["kind"] == "photo" else "▶")
            self.page.request_thumb(r)
        p.setClipping(False)
        x = th.right() - 6
        bad = []
        if live is not None:
            bad.append(("MUTED", QColor(0, 0, 0, 150)))                # top-right while the preview plays
        if r["compressed"]:
            bad.append(("SD", WARN))
        if r["dup"]:
            bad.append(("2 copies", "#38bdf8"))
        for txt, col in bad:
            f = _font(10, QFont.ExtraBold); p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(txt) + 14
            br = QRectF(x - w, th.top() + 6, w, 18); x -= w + 4
            bp = QPainterPath(); bp.addRoundedRect(br, 9, 9); p.fillPath(bp, QColor(col)); p.setPen(QColor("white")); p.drawText(br, Qt.AlignCenter, txt)
        # length, bottom-right, always shown for videos; counts up while the preview plays ("0:02 / 0:15")
        total = self.page.duration_of(r) if r["kind"] == "video" else 0
        if total:
            def mmss(sec): sec = int(sec); return f"{sec // 60}:{sec % 60:02d}"
            txt = mmss(total) if live is None else f"{mmss(self.page.preview_pos / 1000)} / {mmss(total)}"
            f = _font(10, QFont.ExtraBold); p.setFont(f)
            bw = p.fontMetrics().horizontalAdvance(txt) + 14; br = QRectF(th.right() - bw - 6, th.bottom() - 24, bw, 18)
            bp = QPainterPath(); bp.addRoundedRect(br, 9, 9); p.fillPath(bp, QColor(0, 0, 0, 150)); p.setPen(QColor("white"))
            p.drawText(br, Qt.AlignCenter, txt)
        if sel:
            c = QRectF(th.left() + 6, th.top() + 6, 20, 20)
            g = QLinearGradient(c.topLeft(), c.bottomRight()); g.setColorAt(0, QColor(pal.accent[0])); g.setColorAt(1, QColor(pal.accent[1]))
            p.setPen(QPen(QColor("white"), 1.5)); p.setBrush(g); p.drawEllipse(c)
            f = _font(12, QFont.Black); p.setFont(f); p.drawText(c, Qt.AlignCenter, "✓")
        # Name and details: evenly spaced in the space under the thumbnail, and shortened (never cut) to fit.
        f1, f2 = _font(12, QFont.ExtraBold), _font(11, QFont.Medium)
        from PySide6.QtGui import QFontMetricsF
        m1, m2 = QFontMetricsF(f1), QFontMetricsF(f2)
        left, width = th.left() + 2, th.width() - 4
        space = rect.bottom() - th.bottom()
        gap = max(2.0, (space - m1.height() - m2.height()) / 3)
        y1 = th.bottom() + gap; y2 = y1 + m1.height() + gap
        av = round(m1.height())
        paint_avatar(p, QRectF(left, y1 + (m1.height() - av) / 2, av, av), STORE[0], "user", r["creator"])
        p.setFont(f1); p.setPen(QColor(pal.text))
        p.drawText(QRectF(left + av + 5, y1, width - av - 5, m1.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   m1.elidedText("@" + r["creator"], Qt.ElideRight, int(width - av - 5)))
        p.setFont(f2); p.setPen(QColor(pal.mute))
        extra = (f"{r['count']} photo" + ("s" if r["count"] != 1 else "")) if r["kind"] == "photo" else fmt_bytes(r["size"])
        # date pinned to the left edge, size pinned to the right edge (same insets at every card size)
        lt = time.localtime(r["posted"]) if r["posted"] else None
        dates = [time.strftime("%d %b %Y", lt).lstrip("0"), time.strftime("%d %b %y", lt).lstrip("0")] if lt else [""]
        ew = m2.horizontalAdvance(extra); gap_min = 10
        date = next((d for d in dates if m2.horizontalAdvance(d) + gap_min + ew <= width), None)
        if date is None:                              # too narrow for both: keep the date only
            date, extra = m2.elidedText(dates[-1] or extra, Qt.ElideRight, width), ""
        p.drawText(QRectF(left, y2, width, m2.height()), Qt.AlignLeft | Qt.AlignVCenter, date)
        if extra:
            p.drawText(QRectF(left, y2, width, m2.height()), Qt.AlignRight | Qt.AlignVCenter, extra)
        p.restore()


def _font(px: int, weight) -> QFont:
    """Painted text in the app's font with the same smooth, evenly spaced rendering as the rest of the UI."""
    f = QFont("Geist"); f.setPixelSize(px); f.setWeight(weight); f.setHintingPreference(QFont.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy(QFont.PreferAntialias | QFont.NoSubpixelAntialias | QFont.PreferQuality))
    return f


def _rgba(s: str) -> QColor:
    if s.startswith("#"):
        return QColor(s)
    v = s[s.index("(") + 1:s.index(")")].split(",")
    c = QColor(int(v[0]), int(v[1]), int(v[2])); c.setAlphaF(float(v[3])); return c


def _count(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "-"
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            v = n / div
            return (f"{v:.1f}".rstrip("0").rstrip(".") if v < 100 else f"{v:.0f}") + suf
    return str(n)


def _when(ts) -> str:
    if not ts:
        return "-"
    lt = time.localtime(float(ts))
    return time.strftime("%d %b %Y, ", lt).lstrip("0") + time.strftime("%I:%M %p", lt).lstrip("0")


class InfoPanel(Card):
    """Details for the selected post: creator, caption, counts (saved when it was downloaded), dates, quality, file."""
    def __init__(self, page):
        super().__init__(margins=(18, 16, 18, 16), spacing=10); self.page = page; self.setFixedWidth(330)
        self.row = None
        h = QHBoxLayout(); t = QLabel("POST INFO"); t.setObjectName("h2"); h.addWidget(t); h.addStretch(); self.title = t
        x = button("", "ghost", "ph.x-bold", page.pal); x.setFixedSize(30, 30); x.setToolTip("Close"); x.clicked.connect(lambda: page.show_info(False))
        h.addWidget(x); self.v.addLayout(h)
        self.who = QHBoxLayout(); self.who.setSpacing(10); self.v.addLayout(self.who)
        self.cap = label("", "sub", 12); self.cap.setWordWrap(True); self.v.addWidget(self.cap)
        self.stats = QHBoxLayout(); self.stats.setSpacing(6); self.v.addLayout(self.stats)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(12); self.grid.setVerticalSpacing(8); self.v.addLayout(self.grid)
        bh = QHBoxLayout(); bh.setSpacing(8)
        cp = button("Copy link", "ghost", "ph.link-simple-bold", page.pal); cp.clicked.connect(self._copy); bh.addWidget(cp)
        op = button("Open on TikTok", "ghost", "ph.arrow-square-out-bold", page.pal); op.clicked.connect(self._open_web); bh.addWidget(op)
        self.cp, self.op = cp, op; self.rows: list = []
        bh.addStretch(); self.v.addLayout(bh); self.v.addStretch(1)
        self.note = label("", "mute", 10); self.note.setWordWrap(True); self.v.addWidget(self.note)
        self.empty = label("Select a post to see its details.", "mute"); self.empty.setWordWrap(True); self.v.insertWidget(1, self.empty)

    @staticmethod
    def _clear(lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                InfoPanel._clear(it.layout())

    def _meta(self, pid: str) -> dict:
        import json
        path = os.path.join(os.path.dirname(os.path.abspath(self.page.qm.cfg.cache_path)), ".tikvortex", "meta", f"{pid}.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f) or {}
        except (OSError, ValueError):
            return {}

    def show_rows(self, rows: list) -> None:
        """One post: its details. Several: a summary of all of them."""
        self.rows = list(rows)
        if len(rows) > 1:
            self._summary(rows)
        else:
            self.show_row(rows[0] if rows else None)

    def _summary(self, rows: list) -> None:
        self.row = None
        for lay in (self.who, self.stats, self.grid):
            self._clear(lay)
        self.title.setText("SELECTION"); self.cp.setText(" Copy links"); self.op.setText(" Show in folder")
        self.empty.hide(); self.cap.show(); self.note.show()
        # a fanned stack of up to three thumbnails + the count and total size
        stack = _ThumbStack([self.page.model.thumbs.get(r["pid"]) for r in rows[:3]], [r["creator"] for r in rows[:3]])
        self.who.addWidget(stack)
        wv = QVBoxLayout(); wv.setSpacing(1)
        n = QLabel(f"{len(rows)} posts"); n.setStyleSheet("font-weight: 800; font-size: 14px;"); wv.addWidget(n)
        wv.addWidget(label(fmt_bytes(sum(r["size"] for r in rows)) + " in total", "mute", 11)); self.who.addLayout(wv, 1)
        per: dict = {}
        for r in rows:
            per[r["creator"]] = per.get(r["creator"], 0) + 1
        top = sorted(per.items(), key=lambda kv: -kv[1])
        txt = ", ".join(f"@{c} ({k})" for c, k in top[:3]) + (f", +{len(top) - 3} more" if len(top) > 3 else "")
        fm = self.cap.fontMetrics(); self.cap.setFixedHeight(fm.lineSpacing() * 2 + 2)
        self.cap.setText(fm.elidedText(txt, Qt.ElideRight, max(60, (self.width() - 40) * 2 - 30)))
        metas = [self._meta(r["pid"]) for r in rows]; known = [m for m in metas if m]
        for key, what in (("play_count", "views"), ("digg_count", "likes"), ("comment_count", "comments"), ("share_count", "shares")):
            tot = sum(int(m.get(key) or 0) for m in known) if known else None
            box = glass("row"); bv = QVBoxLayout(box); bv.setContentsMargins(4, 7, 4, 7); bv.setSpacing(0)
            a = QLabel(_count(tot)); a.setAlignment(Qt.AlignCenter); a.setStyleSheet("font-weight: 900; font-size: 13px;"); bv.addWidget(a)
            b = label(what, "mute", 10); b.setAlignment(Qt.AlignCenter); bv.addWidget(b); self.stats.addWidget(box)

        def span(vals):
            vals = sorted(v for v in vals if v)
            if not vals:
                return "-"
            a, b = time.strftime("%d %b %Y", time.localtime(vals[0])).lstrip("0"), time.strftime("%d %b %Y", time.localtime(vals[-1])).lstrip("0")
            return a if a == b else f"{a} - {b}"
        vids = sum(1 for r in rows if r["kind"] == "video"); photos = len(rows) - vids
        comp = sum(1 for r in rows if r["compressed"]); total = sum(self.page.duration_of(r) for r in rows if r["kind"] == "video")
        mix = " · ".join(x for x in (f"{vids} video{'s' if vids != 1 else ''}" if vids else "",
                                     f"{photos} photo post{'s' if photos != 1 else ''}" if photos else "") if x)
        grid_rows = [("Posted", span(r["posted"] for r in rows)), ("Downloaded", span(r["mtime"] for r in rows)),
                     ("Mix", mix), ("Quality", f"{len(rows) - comp} original" + (f" · {comp} compressed" if comp else "")),
                     ("Length", f"{int(total) // 3600}:{int(total) % 3600 // 60:02d}:{int(total) % 60:02d}" if total >= 3600 else
                      (f"{int(total) // 60}:{int(total) % 60:02d} of video" if total else "-")),
                     ("Creators", str(len(per)))]
        for i, (k, val) in enumerate(grid_rows):
            self.grid.addWidget(label(k, "mute", 11), i, 0, Qt.AlignTop)
            lv = label(val, "", 12); lv.setText(lv.fontMetrics().elidedText(val, Qt.ElideRight, 200))
            lv.setFixedHeight(lv.fontMetrics().lineSpacing() + 1)
            if k == "Quality":
                lv.setStyleSheet(f"color: {WARN if comp else '#22c55e'}; font-weight: 800;")
            self.grid.addWidget(lv, i, 1)
        self.grid.setColumnStretch(1, 1)
        self.note.setText(f"Totals use the {len(known)} of {len(rows)} posts with saved details." if len(known) < len(rows)
                          else "Counts are as of when each post was downloaded.")

    def show_row(self, r) -> None:
        self.row = r; self.rows = [r] if r else []
        self.title.setText("POST INFO"); self.cp.setText(" Copy link"); self.op.setText(" Open on TikTok")
        for lay in (self.who, self.stats, self.grid):
            self._clear(lay)
        has = r is not None
        self.empty.setVisible(not has); self.cap.setVisible(has); self.note.setVisible(has)
        if not has:
            return
        m = self._meta(r["pid"])
        self.who.addWidget(AvatarWidget("user", r["creator"], 42))
        wv = QVBoxLayout(); wv.setSpacing(1)
        n = QLabel(m.get("nickname") or r["creator"]); n.setStyleSheet("font-weight: 800; font-size: 14px;"); wv.addWidget(n)
        wv.addWidget(label("@" + r["creator"], "mute", 11)); self.who.addLayout(wv, 1)
        cap = r.get("caption") or m.get("title") or "No caption"
        fm = self.cap.fontMetrics(); self.cap.setFixedHeight(fm.lineSpacing() * 2 + 2)   # always two lines tall
        self.cap.setText(fm.elidedText(cap, Qt.ElideRight, max(60, (self.width() - 40) * 2 - 30)))
        if True:                                      # the counts row is always there ("-" when unknown)
            for key, what in (("play_count", "views"), ("digg_count", "likes"), ("comment_count", "comments"), ("share_count", "shares")):
                box = glass("row"); bv = QVBoxLayout(box); bv.setContentsMargins(4, 7, 4, 7); bv.setSpacing(0)
                a = QLabel(_count(m.get(key))); a.setAlignment(Qt.AlignCenter); a.setStyleSheet("font-weight: 900; font-size: 13px;"); bv.addWidget(a)
                b = label(what, "mute", 10); b.setAlignment(Qt.AlignCenter); bv.addWidget(b); self.stats.addWidget(box)
        q = "Compressed" if r["compressed"] else "Original"
        if r["kind"] == "photo":
            from PySide6.QtGui import QImageReader
            sz = QImageReader(r["main"]).size()
            q += f" · {sz.width()} × {sz.height()}" if sz.isValid() else ""
        size = fmt_bytes(r["size"])
        dur = m.get("duration")
        length = (f"{int(dur) // 60}:{int(dur) % 60:02d} · " if dur and r["kind"] == "video" else "") + size
        if r["kind"] == "photo":
            length = f"{r['count']} photo" + ("s" if r["count"] != 1 else "") + f" · {size}"
        out = os.path.abspath(self.page.qm.output_dir)
        try:
            rel = os.path.relpath(r["main"], os.path.dirname(out))
        except ValueError:
            rel = r["main"]
        rows = [("Posted", _when(r["posted"])), ("Downloaded", _when(m.get("downloaded_at") or r["mtime"])),
                ("Quality", q), ("Size" if r["kind"] == "photo" else "Length", length)]
        rows.append(("Sound", m.get("music") or "-"))
        rows += [("Post ID", r["pid"]), ("File", rel)]
        for i, (k, val) in enumerate(rows):
            self.grid.addWidget(label(k, "mute", 11), i, 0, Qt.AlignTop)
            lv = label(val, "", 12); lv.setWordWrap(True); lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lines = 3 if k == "File" else 1                # fixed heights, so nothing jumps between posts
            if k != "File":
                lv.setWordWrap(False); lv.setText(lv.fontMetrics().elidedText(val, Qt.ElideRight, 200))
            lv.setFixedHeight(lv.fontMetrics().lineSpacing() * lines + 1)
            if k == "Quality":
                lv.setStyleSheet(f"color: {WARN if r['compressed'] else '#22c55e'}; font-weight: 800;")
            self.grid.addWidget(lv, i, 1)
        self.grid.setColumnStretch(1, 1)
        self.note.setText("Counts are as of when the post was downloaded." if m else
                          "Downloaded before TikVortex saved post details, so counts and sound aren't available.")

    def _copy(self) -> None:
        if self.rows:
            QGuiApplication.clipboard().setText("\n".join(self.page.links(self.rows)))

    def _open_web(self) -> None:
        if len(self.rows) > 1:
            reveal_many([r["main"] for r in self.rows])
        elif self.row:
            QDesktopServices.openUrl(QUrl(self.page.links([self.row])[0]))


class _ThumbStack(QWidget):
    """Up to three thumbnails fanned out like a small stack of cards."""
    def __init__(self, pms, seeds):
        super().__init__(); self.pms, self.seeds = pms, seeds; self.setFixedSize(58, 46)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        n = len(self.pms)
        for i in reversed(range(n)):
            r = QRectF(4 + i * 9, 2 + i * 2, 30, 40)
            p.save(); p.translate(r.center()); p.rotate((i - (n - 1) / 2) * 7); p.translate(-r.center())
            path = QPainterPath(); path.addRoundedRect(r, 6, 6); p.setClipPath(path)
            pm = self.pms[i]
            if pm:
                s = pm.size().scaled(r.size().toSize(), Qt.KeepAspectRatioByExpanding)
                p.drawPixmap(QRectF(r.center().x() - s.width() / 2, r.center().y() - s.height() / 2, s.width(), s.height()), pm, QRectF(pm.rect()))
            else:
                a, b = tile_colors(self.seeds[i]); g = QLinearGradient(r.topLeft(), r.bottomRight()); g.setColorAt(0, QColor(a)); g.setColorAt(1, QColor(b)); p.fillRect(r, g)
            p.setClipping(False); p.setPen(QPen(QColor(255, 255, 255, 200), 1.5)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(r, 6, 6); p.restore()


class _FitLabel(QLabel):
    """Shows the longest of its texts that fits (never a half-cut sentence)."""
    def __init__(self, texts):
        super().__init__(texts[0]); self._texts = texts

    def resizeEvent(self, e):
        super().resizeEvent(e)
        fm = self.fontMetrics()
        self.setText(next((t for t in self._texts if fm.horizontalAdvance(t) <= self.width() - 8), ""))


class LibraryPage(QWidget):
    title = "LIBRARY"
    subtitle = "Everything you've saved"
    redownload = Signal(list)
    watchRequested = Signal(str)
    creatorsScanned = Signal(dict, dict)          # {creator: posts}, {creator: last download time}
    statsChanged = Signal(int, int)
    notify = Signal(str, str)
    _scanned = Signal(list)

    def __init__(self, qm, pal: Palette, thumbs, store):
        super().__init__(); self.qm, self.pal, self.thumbs, self.store = qm, pal, thumbs, store
        self.tile = int(store.ui("library_tile") or 150)
        self._pending_recent: dict | None = None
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
        bar = Card(margins=(16, 12, 16, 12)); bh = QHBoxLayout(); bh.setSpacing(10)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search captions, creators or post IDs…")
        self.search.textChanged.connect(self._search); bh.addWidget(self.search, 1)
        self.sort_btn = button("", "ghost", "ph.sort-descending-bold", pal); self.sort_btn.setFixedHeight(38)
        self.sort_btn.clicked.connect(self._sort_menu); bh.addWidget(self.sort_btn)
        self._sort_choice = store.ui("library_sort") or "posted_new"
        if self._sort_choice not in SORTS:
            self._sort_choice = "posted_new"
        self.kind = SegGroup(["All", "Videos", "Photos", "Compressed", "Duplicates"]); self.kind.changed.connect(self._kind); bh.addWidget(self.kind)
        # zoom = cards per row; it snaps, so cards always fill the width with even gaps
        self.per_row = max(3, min(9, int(store.ui("library_per_row") or 5)))
        self.zoom_out = button("", "ghost", "ph.minus-bold", pal); self.zoom_out.setFixedSize(32, 32); self.zoom_out.setToolTip("Bigger cards")
        self.zoom_out.clicked.connect(lambda: self.size_slider.setValue(self.size_slider.value() - 1)); bh.addWidget(self.zoom_out)
        self.size_slider = QSlider(Qt.Horizontal); self.size_slider.setRange(3, 9); self.size_slider.setPageStep(1)
        self.size_slider.setValue(self.per_row); self.size_slider.setFixedWidth(92); self.size_slider.setToolTip("Cards per row")
        self.size_slider.valueChanged.connect(self._tile_size); bh.addWidget(self.size_slider)
        self.zoom_in = button("", "ghost", "ph.plus-bold", pal); self.zoom_in.setFixedSize(32, 32); self.zoom_in.setToolTip("Smaller cards")
        self.zoom_in.clicked.connect(lambda: self.size_slider.setValue(self.size_slider.value() + 1)); bh.addWidget(self.zoom_in)
        self.per_row_lbl = label("", "mute", 11); self.per_row_lbl.setStyleSheet("font-weight: 800;"); self.per_row_lbl.setFixedWidth(64)
        bh.addWidget(self.per_row_lbl)
        self.refresh_btn = button("", "ghost", "ph.arrow-clockwise-bold", pal); self.refresh_btn.setFixedSize(34, 32)
        self.refresh_btn.setToolTip("Rescan your download folder"); self.refresh_btn.clicked.connect(self.rescan); bh.addWidget(self.refresh_btn)
        bar.v.addLayout(bh); v.addWidget(bar)

        self.chips_w = QWidget(); self.chips = QHBoxLayout(self.chips_w); self.chips.setContentsMargins(0, 0, 0, 0); self.chips.setSpacing(8)
        v.addWidget(self.chips_w)
        self._chip_timer = QTimer(self); self._chip_timer.setSingleShot(True); self._chip_timer.setInterval(120)
        self._chip_timer.timeout.connect(lambda: self._chips(self.model.rows)); self.chips_w.installEventFilter(self)

        # Permanent selection bar (always visible, buttons disabled until at least one post is selected)
        self.selbar = Card("row", margins=(14, 8, 10, 8)); sh = QHBoxLayout(); sh.setSpacing(8)
        self.sel_lbl = QLabel("None selected"); self.sel_lbl.setFixedWidth(118)        # room for "9,999 selected"
        sh.addWidget(self.sel_lbl)
        hint = _FitLabel(("Ctrl+click to add · Shift+click for a range · drag a box · Ctrl+A for all",
                          "Ctrl+click · Shift+click · drag · Ctrl+A", ""))
        hint.setObjectName("mute"); hint.setStyleSheet("font-size: 11px;")
        hint.setAlignment(Qt.AlignCenter); hint.setMinimumWidth(1)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred); sh.addWidget(hint, 1)
        self._sel_btns = []
        for txt, ic, fn in (
            ("Info", "ph.info-fill", lambda _=False: self.toggle_info()),
            ("Open", "ph.play-fill", lambda _=False: self._open()),
            ("Show in folder", "ph.folder-simple-fill", lambda _=False: self._reveal()),
            ("Copy links", "ph.link-simple-bold", lambda _=False: self._copy()),
            ("Re-download", "ph.arrow-clockwise-bold", lambda _=False: self._redl()),
        ):
            b = button(txt, "ghost", ic, pal); b.clicked.connect(fn); sh.addWidget(b); self._sel_btns.append(b)
            if txt == "Info":
                self.info_btn = b; b.setCheckable(True); b.setToolTip("Show or hide post details")
        self.btn_trash = button("Recycle bin", "danger"); self.btn_trash.clicked.connect(self._trash); sh.addWidget(self.btn_trash)
        self.btn_clear_sel = button("", "ghost", "ph.x-bold", pal); self.btn_clear_sel.setFixedWidth(34); self.btn_clear_sel.setToolTip("Clear selection")
        self.btn_clear_sel.clicked.connect(lambda: self.view.clearSelection())
        sh.addWidget(self.btn_clear_sel)
        self.selbar.v.addLayout(sh); v.addWidget(self.selbar)

        grid = Card(margins=(12, 12, 6, 12))
        self.model = LibraryModel(); self.proxy = LibraryFilter(); self.proxy.setSourceModel(self.model)
        self.proxy.setDynamicSortFilter(True); self.proxy.sort(0, Qt.DescendingOrder)
        self.view = QListView(); self.view.setModel(self.proxy); self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust); self.view.setMovement(QListView.Static); self.view.setUniformItemSizes(True)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection); self.view.setSelectionRectVisible(True)
        self.view.setSpacing(0); self.view.setItemDelegate(TileDelegate(self)); self.view.setMouseTracking(True)
        self.view.viewport().installEventFilter(self)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        # The scrollbar's room is always reserved: it appearing/disappearing used to change the width,
        # re-flow the cards, and (at some sizes) flip back and forth between two layouts.
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.view.doubleClicked.connect(lambda ix: self._open([ix]))
        self.view.selectionModel().selectionChanged.connect(self._selection)
        # filtering, rescans and deletions drop selected rows without a selectionChanged signal
        for sig in (self.proxy.modelReset, self.proxy.layoutChanged, self.proxy.rowsRemoved, self.proxy.rowsInserted):
            sig.connect(lambda *_: QTimer.singleShot(0, self._selection))
        self.view.setContextMenuPolicy(Qt.CustomContextMenu); self.view.customContextMenuRequested.connect(self._menu)
        from PySide6.QtGui import QKeySequence, QShortcut
        sc = QShortcut(QKeySequence(Qt.Key_Delete), self); sc.setContext(Qt.WidgetWithChildrenShortcut); sc.activated.connect(self.delete_key)
        grid.v.addWidget(self.view)
        self.empty = label("Nothing here yet - your downloads will appear in the library.", "empty"); self.empty.setAlignment(Qt.AlignCenter)
        grid.v.addWidget(self.empty)
        from ..creators import CreatorGallery
        self.gallery = CreatorGallery(self.pal, store); self.gallery.hide(); grid.v.addWidget(self.gallery, 1)
        self.gallery.picked.connect(self._from_gallery_pick); self.gallery.back.connect(self.hide_gallery)
        self.gallery.menu.connect(self.creator_menu)
        self._from_gallery = False
        row = QHBoxLayout(); row.setSpacing(14); row.addWidget(grid, 1)
        self.info = InfoPanel(self); self.info.hide(); row.addWidget(self.info); v.addLayout(row, 1)
        thumbs.ready.connect(self.model.thumb_ready)
        self._scanned.connect(self._apply_scan)
        self._scanning = False
        self.creator_chips = {}
        self._selection()
        self._apply_sort(self._sort_choice); self._tile_size(self.per_row)
        self._setup_preview()
        if STORE[0] is not None:                  # profile pictures arriving: repaint cards and creator buttons
            STORE[0].ready.connect(lambda u: (self.view.viewport().update(), self.chips_w.update(),
                                              [b.update() for _, b in getattr(self, "_chip_btns", [])]))

    # ---------------------------------------------------------- muted hover preview
    def _setup_preview(self) -> None:
        self.preview_pid = None; self.preview_frame = None; self.preview_progress = 0.0; self.preview_pos = 0
        self._durations: dict = {}
        self._hover_ix = None; self._player = None
        self._hover_timer = QTimer(self); self._hover_timer.setSingleShot(True); self._hover_timer.setInterval(450)
        self._hover_timer.timeout.connect(self._start_preview)
        self.view.entered.connect(self._hovered)

    def _hovered(self, ix) -> None:
        r = ix.data(ROLE) if ix.isValid() else None
        if r is None or r["kind"] != "video" or (self.preview_pid == r["pid"]):
            if r is None:
                self.stop_preview()
            return
        self.stop_preview(); self._hover_ix = ix; self._hover_timer.start()

    def _start_preview(self) -> None:
        ix = self._hover_ix
        if ix is None or not ix.isValid() or not self.view.viewport().underMouse():
            return
        r = ix.data(ROLE)
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        except ImportError:
            return
        if self._player is None:
            self._player = QMediaPlayer(self); self._sink = QVideoSink(self)
            self._player.setVideoSink(self._sink)            # no audio output at all: always muted
            self._player.setLoops(QMediaPlayer.Infinite)
            self._sink.videoFrameChanged.connect(self._frame)
        self.preview_pid = r["pid"]; self._preview_rect = self.view.visualRect(ix)
        self._player.setSource(QUrl.fromLocalFile(r["main"])); self._player.play()

    def _frame(self, frame) -> None:
        if self.preview_pid is None:
            return
        from PySide6.QtGui import QCursor
        vp = self.view.viewport()                 # safety net: never keep playing once the pointer has gone
        if not (self.isVisible() and self.window().isActiveWindow() and
                self._preview_rect.contains(vp.mapFromGlobal(QCursor.pos()))):
            self.stop_preview(); return
        img = frame.toImage()
        if img.isNull():
            return
        self.preview_frame = img
        dur = self._player.duration() or 0
        self.preview_pos = self._player.position()
        if dur and self.preview_pid:
            self._durations[self.preview_pid] = dur / 1000
        self.preview_progress = (self._player.position() / dur) if dur else 0.0
        self.view.viewport().update(self._preview_rect)

    def duration_of(self, r) -> float:
        return self._durations.get(r["pid"]) or r.get("dur") or 0.0

    def stop_preview(self) -> None:
        self._hover_timer.stop(); self._hover_ix = None
        if self._player is not None:
            # stop() alone keeps the file open, and Windows then refuses to delete it (it was always
            # the last video you hovered that "couldn't be deleted"). Clearing the source lets it go.
            self._player.stop(); self._player.setSource(QUrl())
        if self.preview_pid is not None:
            self.preview_pid = None; self.preview_frame = None
            self.view.viewport().update()

    # ---------------------------------------------------------- scanning
    def rescan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        out, cache = self.qm.output_dir, self.qm.cache
        threading.Thread(target=lambda: self._scanned.emit(scan_library(out, dict(cache))), daemon=True).start()

    def _apply_scan(self, rows: list) -> None:
        counts: dict = {}; lasts: dict = {}
        for r in rows:
            c = (r["creator"] or "").lower()
            if c and c != "unknown":
                counts[c] = counts.get(c, 0) + 1; lasts[c] = max(lasts.get(c, 0), r["mtime"])
        self.creatorsScanned.emit(counts, lasts)
        self._scanning = False
        self.model.set_rows(rows)
        from ..avatars import STORE as _AV
        if _AV[0] is not None:                       # lets profile pictures be found through any post of theirs
            _AV[0].note_posts((r["creator"], r["pid"]) for r in rows if r["creator"] != "unknown")
        if self.gallery.isVisible():
            self.gallery.set_creators(self.creator_stats(rows))
        else:
            self.empty.setVisible(not rows); self.view.setVisible(bool(rows))
        comp = sum(1 for r in rows if r["compressed"]); dup = sum(1 for r in rows if r["dup"])
        self.kind.set_count("Compressed", comp); self.kind.set_count("Duplicates", dup)
        self._chips(rows)
        self.statsChanged.emit(len(rows), sum(r["size"] for r in rows))
        if self._pending_recent is not None:
            ent = self._pending_recent
            self._pending_recent = None
            self._apply_recent_selection(ent)

    def creator_stats(self, rows) -> list[dict]:
        """One entry per creator (or creator group folder): count, size, last download, picture kind."""
        groups = self.qm.cfg.creators; out: dict = {}
        for r in rows:
            k = r["folder"] or r["creator"]
            c = out.setdefault(k, {"key": k, "title": k if k in groups else "@" + k, "count": 0, "size": 0, "last": 0.0,
                                   "kind": "group" if k in groups else "user", "accounts": groups.get(k)})
            c["count"] += 1; c["size"] += r["size"]; c["last"] = max(c["last"], r["mtime"])
        return sorted(out.values(), key=lambda c: -c["last"])

    def _chips(self, rows) -> None:
        """All creators, then creators by most recent download (as many as fit), then Gallery on the right."""
        from ..creators import CreatorChip
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        stats = self.creator_stats(rows); gal_on = self.gallery.isVisible()
        self._chip_btns = []; self._chip_back = bool(self._from_gallery and self.proxy.creator and not gal_on)
        if self._from_gallery and self.proxy.creator and not gal_on:
            back = button("Gallery", "ghost", "ph.arrow-left-bold", self.pal); back.setFixedHeight(CreatorChip.H)
            back.setToolTip("Back to all creators"); back.clicked.connect(self.show_gallery); self.chips.addWidget(back)
        allb = CreatorChip(self.pal, "", len(rows), "all"); allb.setChecked(not self.proxy.creator and not gal_on)
        allb.clicked.connect(lambda: self._creator("")); self.chips.addWidget(allb); self._chip_btns.append(("", allb))
        room = max(0, self.chips_w.width() - 260) if self.chips_w.width() > 300 else 900; used = 0
        for c in stats:                           # always most recently downloaded first, whatever is selected
            b = CreatorChip(self.pal, c["key"], c["count"], c["kind"], c["accounts"]); used += b.width() + 8
            if used > room and c["key"] != self.proxy.creator:
                b.deleteLater(); continue
            b.setChecked(self.proxy.creator == c["key"] and not gal_on)
            b.clicked.connect(lambda _=False, n=c["key"]: (setattr(self, "_from_gallery", False), self._creator(n)))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, k=c["key"], w=b: self.creator_menu(k, w.mapToGlobal(pos)))
            self.chips.addWidget(b); self._chip_btns.append((c["key"], b))
        self.chips.addStretch()
        g = CreatorChip(self.pal, "", 0, "gallery"); g.setCheckable(True); g.setChecked(gal_on)
        g.clicked.connect(lambda: self.show_gallery() if not self.gallery.isVisible() else self.hide_gallery()); self.chips.addWidget(g)
        self._chip_btns.append(("\0gallery", g))

    def _sync_chips(self) -> None:
        """Just move the highlight between chips (no rebuild, so nothing jumps); rebuild only if needed."""
        btns = getattr(self, "_chip_btns", [])
        keys = {k for k, _ in btns}
        need_back = bool(self._from_gallery and self.proxy.creator and not self.gallery.isVisible())
        if not btns or need_back != getattr(self, "_chip_back", False) or (self.proxy.creator and self.proxy.creator not in keys):
            self._chips(self.model.rows); return
        gal_on = self.gallery.isVisible()
        for k, b in btns:
            b.setChecked(gal_on if k == "\0gallery" else (not gal_on and k == self.proxy.creator)); b.update()

    def pick_creator(self, name: str) -> None:
        """Show one creator's posts, matching the name however its folder is spelled."""
        low = name.lower().lstrip("@")
        for c in self.creator_stats(self.model.rows):
            if c["key"].lower() == low:
                self._creator(c["key"]); return
        self.search.setText(name)

    def creator_menu(self, key: str, at) -> None:
        from ..widgets import add_action, styled_menu
        m = styled_menu(self); title = key if key in self.qm.cfg.creators else "@" + key
        add_action(m, f"See {title}'s posts", "ph.squares-four-fill",
                   lambda: (self._from_gallery_pick(key) if self.gallery.isVisible() else self._creator(key)))
        add_action(m, "Watch for new posts", "ph.eye-fill", lambda: self.watchRequested.emit(key))
        add_action(m, "Download again from their profile", "ph.arrow-clockwise-bold",
                   lambda: self.qm.submit(key if key in self.qm.cfg.creators else "@" + key))
        m.addSeparator()
        add_action(m, f"Delete all posts by {title}", "ph.trash-fill", lambda: self.delete_creator(key))
        m.popup(at)

    def creator_rows(self, key: str) -> list:
        return [r for r in self.model.rows if (r["folder"] or r["creator"]) == key]

    def delete_creator(self, key: str) -> None:
        rows = self.creator_rows(key)
        if rows:
            title = key if key in self.qm.cfg.creators else "@" + key
            self._trash_rows(rows, f"Delete all posts by {title}",
                             f"Move all {len(rows)} post(s) by {title} ({sum(len(r['files']) for r in rows)} file(s)) to the Recycle Bin?\n"
                             "You can restore them from there.")

    def delete_key(self) -> None:
        """Delete: the posts you've selected, or - with the pointer on a creator chip or gallery tile - that creator's posts."""
        from PySide6.QtGui import QCursor
        from ..creators import CreatorChip, CreatorTile
        w = QApplication.widgetAt(QCursor.pos())
        while w is not None and not isinstance(w, (CreatorChip, CreatorTile)) and w is not self:
            w = w.parentWidget()
        if isinstance(w, CreatorTile):
            self.delete_creator(w.key); return
        if isinstance(w, CreatorChip) and w.kind in ("user", "group"):
            self.delete_creator(w.name); return
        self._trash()

    def show_gallery(self) -> None:
        self.view.hide(); self.empty.hide(); self.stop_preview()
        from ..widgets import fade_in
        self.gallery.show(); self.gallery.layout().activate()          # sized first, so the tiles flow at full width
        QTimer.singleShot(0, lambda: fade_in(self.gallery, 220))
        self.gallery.set_creators(self.creator_stats(self.model.rows), animate=True); self._sync_chips()

    def hide_gallery(self) -> None:
        from ..widgets import fade_in
        self.gallery.hide(); rows = self.model.rows
        QTimer.singleShot(0, lambda: fade_in(self.view, 220))
        self.empty.setVisible(not rows); self.view.setVisible(bool(rows)); self._sync_chips()

    def _from_gallery_pick(self, key: str) -> None:
        self._from_gallery = True; self.gallery.hide(); self.view.show(); self._creator(key)

    def request_thumb(self, r: dict) -> None:
        self.thumbs.request(r["pid"], r["main"])

    # ---------------------------------------------------------- recent batch navigation
    def select_recent_batch(self, entry: dict) -> None:
        """Filter to the recent batch's creator/group and highlight the downloaded posts."""
        if not self.model.rows or self._scanning:
            self._pending_recent = entry
            if not self._scanning:
                self.rescan()
            return
        self._apply_recent_selection(entry)

    def _apply_recent_selection(self, entry: dict) -> None:
        raw_label = (entry.get("label") or "").strip()
        target = raw_label.lstrip("@")
        pids = set(str(p) for p in (entry.get("pids") or []))
        creators = [c.lstrip("@") for c in (entry.get("creators") or [])]

        self.search.clear()
        self.kind.select("All", True)

        chosen_creator = ""
        known = {r["folder"] for r in self.model.rows if r["folder"]} | {r["creator"] for r in self.model.rows if r["creator"]}
        if target in known:
            chosen_creator = target
        elif len(creators) == 1 and creators[0] in known:
            chosen_creator = creators[0]
        elif pids:
            for r in self.model.rows:
                if r["pid"] in pids:
                    chosen_creator = r["folder"] or r["creator"]
                    break

        self._creator(chosen_creator)

        sel_model = self.view.selectionModel()
        sel_model.clearSelection()
        selection = QItemSelection()
        first_idx = None

        if pids:
            for row in range(self.proxy.rowCount()):
                ix = self.proxy.index(row, 0)
                r = ix.data(ROLE)
                if r and r["pid"] in pids:
                    selection.select(ix, ix)
                    if first_idx is None:
                        first_idx = ix
        else:
            # Fallback for older recent entries saved before pids were recorded
            count = int(entry.get("count") or 0)
            batch_ts = float(entry.get("time") or 0)
            candidates = []
            for row in range(self.proxy.rowCount()):
                ix = self.proxy.index(row, 0)
                r = ix.data(ROLE)
                if r:
                    candidates.append((abs(r["mtime"] - batch_ts), ix))
            candidates.sort(key=lambda t: t[0])
            for _dt, ix in candidates[:max(0, count)]:
                selection.select(ix, ix)
                if first_idx is None:
                    first_idx = ix

        if not selection.isEmpty():
            sel_model.select(selection, QItemSelectionModel.Select)
            if first_idx is not None:
                self.view.scrollTo(first_idx, QAbstractItemView.PositionAtCenter)

    # ---------------------------------------------------------- filters
    def _search(self, t: str) -> None:
        self.proxy.text = t.strip(); self.proxy.invalidateFilter()

    def _sort_menu(self) -> None:
        from ..widgets import popup_recently_closed, track_popup
        if popup_recently_closed(self.sort_btn):
            return
        m = styled_menu(self)
        for i, (key, (txt, _k, _o)) in enumerate(SORTS.items()):
            if i in (2, 4):
                m.addSeparator()
            a = m.addAction(txt); a.setCheckable(True); a.setChecked(key == self._sort_choice)
            a.triggered.connect(lambda _=False, k=key: self._apply_sort(k))
        track_popup(self.sort_btn, m); m.popup(self.sort_btn.mapToGlobal(QPoint(0, self.sort_btn.height() + 6)))

    def _apply_sort(self, key: str) -> None:
        self._sort_choice = key; self.store.set_ui("library_sort", key)
        txt, k, order = SORTS[key]
        self.sort_btn.setText(f" {SORT_LABEL[key]}   ▾")
        self.proxy.sort_key = k; self.proxy.invalidate(); self.proxy.sort(0, order)

    def _kind(self, k: str) -> None:
        self.proxy.kind = k; self.proxy.invalidateFilter()

    def _creator(self, name: str) -> None:
        from ..widgets import fade_in
        if name != self.proxy.creator or self.gallery.isVisible():           # (leaving the gallery fades too)
            self.stop_preview(); QTimer.singleShot(0, lambda: (fade_in(self.view, 220), fade_in(self.empty, 220)))   # the new posts fade in
        if not name:
            self._from_gallery = False
        if self.gallery.isVisible():
            self.gallery.hide(); self.view.setVisible(bool(self.model.rows)); self.empty.setVisible(not self.model.rows)
        self.proxy.creator = name; self.proxy.invalidateFilter()
        self.view.scrollToTop(); self._sync_chips()

    def _tile_size(self, v: int) -> None:
        self.per_row = v; self.store.set_ui("library_per_row", v)
        self.per_row_lbl.setText(f"{v} per row"); self._fit_tiles()

    def _fit_tiles(self) -> None:
        """Card width = the grid's width split into exactly `per_row` equal cells."""
        vp = self.view.viewport().width()
        if vp < 50:
            return
        cell = max(80, (vp - 1) // self.per_row)       # -1: a full row must never be a pixel too wide and wrap
        size = QSize(cell, int(cell * 1.33) + 46)
        if size == self.view.gridSize() and cell == self.tile:
            return
        self.tile = cell
        self.view.setGridSize(size)
        self.view.doItemsLayout()

    def eventFilter(self, obj, e):
        try:
            vp = self.view.viewport()
        except RuntimeError:                      # shutting down
            return False
        if obj is self.chips_w and e.type() == QEvent.Resize and e.oldSize().width() != e.size().width():
            self._chip_timer.start(); return False
        if obj is vp and e.type() == QEvent.Resize:
            QTimer.singleShot(0, self._fit_tiles)
        if obj is vp and e.type() in (QEvent.Leave, QEvent.Hide, QEvent.Wheel):
            self.stop_preview()
        if obj is vp and e.type() == QEvent.MouseMove and (self.preview_pid or self._hover_ix is not None):
            ix = self.view.indexAt(e.position().toPoint())          # gaps between cards count as leaving
            r = ix.data(ROLE) if ix.isValid() else None
            if r is None or (self.preview_pid and r["pid"] != self.preview_pid and self._hover_ix is None):
                self.stop_preview()
        return super().eventFilter(obj, e)

    # ---------------------------------------------------------- selection actions
    def _selected(self, idxs=None) -> list[dict]:
        idxs = idxs if idxs is not None else self.view.selectionModel().selectedIndexes()
        return [ix.data(ROLE) for ix in idxs]

    def toggle_info(self) -> None:
        self.show_info(not self.info.isVisible())

    def show_info(self, on: bool) -> None:
        from ..widgets import fade_in
        was = self.info.isVisible(); self.info.setVisible(on); self.info_btn.setChecked(on)
        if on:
            self.info.show_rows(self._selected())
            if not was:
                QTimer.singleShot(0, lambda: fade_in(self.info, 220))
        QTimer.singleShot(0, self._fit_tiles)

    def _selection(self, *a) -> None:
        if getattr(self, "info", None) is not None and self.info.isVisible():
            self.info.show_rows(self._selected())
        n = len([ix for ix in self.view.selectionModel().selectedIndexes() if ix.isValid()])
        has = n > 0
        for b in (*self._sel_btns, self.btn_trash, self.btn_clear_sel):
            b.setEnabled(has or b is self.info_btn)
        if has:
            self.sel_lbl.setText(f"{n} selected")
            self.sel_lbl.setStyleSheet(f"font-weight: 900; font-size: 13px; color: {self.pal.nav_icon};")
        else:
            self.sel_lbl.setText("None selected")
            self.sel_lbl.setStyleSheet(f"font-weight: 800; font-size: 13px; color: {self.pal.mute};")

    def _open(self, idxs=None) -> None:
        for r in self._selected(idxs)[:10]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(r["main"]))

    def _reveal(self) -> None:
        sel = self._selected()
        if sel:
            reveal_many([r["main"] for r in sel])

    def links(self, rows) -> list[str]:
        return [f"https://www.tiktok.com/@{r['creator']}/{'photo' if r['kind'] == 'photo' else 'video'}/{r['pid']}" for r in rows]

    def _copy(self) -> None:
        sel = self._selected()
        if not sel:
            return
        urls = self.links(sel)
        QGuiApplication.clipboard().setText("\n".join(urls))
        self.notify.emit(
            "Link copied" if len(urls) == 1 else f"{len(urls)} links copied",
            urls[0] if len(urls) == 1 else "Copied to your clipboard",
        )

    def _redl(self) -> None:
        sel = self._selected()
        if sel:
            self.redownload.emit(self.links(sel))

    def _trash(self) -> None:
        sel = self._selected()
        if sel:
            n = len([f for r in sel for f in r["files"]])
            self._trash_rows(sel, "Move to Recycle Bin",
                             f"Move {len(sel)} post(s) ({n} file(s)) to the Recycle Bin?\nYou can restore them from there.")

    def _trash_rows(self, sel: list, title: str, body: str) -> None:
        files = [f for r in sel for f in r["files"]]
        if not ConfirmDialog.ask(
            self,
            title,
            body,
            self.pal,
            confirm_text="Yes",
            cancel_text="No",
            danger=True,
        ):
            return
        self.stop_preview()
        QApplication.processEvents()                  # let the player close the file first

        def trash(f) -> bool:
            ok = QFile.moveToTrash(f)
            return bool(ok[0] if isinstance(ok, tuple) else ok) or not os.path.exists(f)
        left = [f for f in files if not trash(f)]
        if left:                                      # something still had a file open for a moment: try once more
            time.sleep(0.4); QApplication.processEvents()
            left = [f for f in left if not trash(f)]
        self._prune_dirs({os.path.dirname(f) for f in files})
        failed = len(left)
        if failed:
            ConfirmDialog.info(self, "Recycle Bin", f"{failed} file(s) couldn't be moved.", self.pal)
        self.rescan()

    def _prune_dirs(self, dirs) -> None:
        """Remove folders the delete left empty (a creator's folder once their last post is gone), up to - never
        including - the download folder. Only truly empty folders go; Windows' own Thumbs.db/desktop.ini don't count."""
        root = os.path.normcase(os.path.abspath(self.qm.output_dir))
        junk = {"thumbs.db", "desktop.ini", ".ds_store"}
        for d in sorted(dirs, key=len, reverse=True):
            d = os.path.abspath(d)
            while os.path.normcase(d).startswith(root + os.sep):
                try:
                    names = os.listdir(d)
                    if any(n.lower() not in junk for n in names):
                        break
                    for n in names:
                        os.remove(os.path.join(d, n))
                    os.rmdir(d)
                except OSError:
                    break
                d = os.path.dirname(d)

    def _menu(self, pos) -> None:
        if not self.view.indexAt(pos).isValid():
            return
        m = styled_menu(self)
        add_action(m, "Info", "ph.info-fill", lambda: self.show_info(True))
        add_action(m, "Open", "ph.play-fill", self._open)
        add_action(m, "Show in folder", "ph.folder-simple-fill", self._reveal)
        add_action(m, "Copy links", "ph.link-simple-bold", self._copy)
        add_action(m, "Re-download in original quality", "ph.arrow-clockwise-bold", self._redl)
        m.addSeparator()
        add_action(m, "Move to Recycle Bin", "ph.trash-fill", self._trash)
        m.exec(self.view.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.view.clearSelection()
        else:
            super().keyPressEvent(e)

    def restyle(self, pal: Palette) -> None:
        self.pal = pal
        self._selection()
        self.view.viewport().update()