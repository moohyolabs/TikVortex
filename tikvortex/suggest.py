"""Creator suggestions under text boxes (like a search engine's autocomplete), with profile pictures.

Names are remembered in the app settings ("known_creators"): everyone in the library, creator groups,
the watchlist, recent batches and highlight lookups. Type a few letters (with or without @) to see them.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from . import widgets as widgets_mod
from .avatars import STORE, avatar_pixmap


class CreatorMemory:
    """The remembered names: {name: {"kind": "user"|"group", "last": time, "count": posts}}."""
    MAX = 400

    def __init__(self, store, qm):
        self.store, self.qm = store, qm

    def _all(self) -> dict:
        return dict(self.store.ui("known_creators") or {})

    def note(self, names, kind: str = "user", counts: Optional[dict] = None, lasts: Optional[dict] = None) -> None:
        """Remember names. From a library scan, pass post counts and last-download times."""
        data = self._all(); now = time.time(); changed = False
        for n in names:
            n = (n or "").strip().lstrip("@")
            if not n or n == "unknown":
                continue
            key = n if kind == "group" else n.lower()
            old = data.get(key) or {"kind": kind, "last": 0, "count": 0}
            e = dict(old)
            if kind == "group":
                e["kind"] = "group"
            if counts is not None:
                e["count"] = counts.get(key, e.get("count", 0))
                e["last"] = max(e.get("last", 0), (lasts or {}).get(key, 0))
            else:
                e["last"] = now
            if e != old:
                data[key] = e; changed = True
        if counts is not None:                        # a library scan: anyone not in the library now has 0 posts
            for k, e in data.items():
                if e.get("kind") != "group" and k not in counts and e.get("count"):
                    data[k] = dict(e, count=0); changed = True
        if changed:
            if len(data) > self.MAX:                 # keep the most recent ones
                data = dict(sorted(data.items(), key=lambda kv: -kv[1].get("last", 0))[: self.MAX])
            self.store.set_ui("known_creators", data)

    def tidy(self, watchlist) -> None:
        """One-time clean-up: earlier versions remembered every name you typed or searched. Keep only names
        you've actually done something with (posts in the library, recent downloads, groups, the watchlist)."""
        if self.store.ui("known_creators_tidied"):
            return
        keep = {a.lstrip("@").lower() for accs in self.qm.cfg.creators.values() for a in accs}
        for e in watchlist.entries():
            keep |= {a.lstrip("@").lower() for a in watchlist.accounts(e)}
        for r in self.store.ui("recent") or []:
            lab = str(r.get("label") or "").split()[0] if r.get("label") else ""
            if lab.startswith("@"):
                keep.add(lab.lstrip("@").lower())
        data = {k: e for k, e in self._all().items()
                if e.get("kind") == "group" or e.get("count") or k.lower() in keep}
        self.store.set_ui("known_creators", data); self.store.set_ui("known_creators_tidied", True)

    def matches(self, text: str, limit: int = 6) -> list[tuple[str, dict]]:
        t = text.strip().lstrip("@").lower()
        if not t:
            return []
        groups = self.qm.cfg.creators; data = self._all()
        for g in groups:                              # groups are always current
            data.setdefault(g, {"kind": "group", "last": 0, "count": 0})["kind"] = "group"
        data = {k: v for k, v in data.items() if v.get("kind") != "group" or k in groups}
        hits = []
        for name, e in data.items():
            low = name.lower()
            if low.startswith(t):
                rank = 0
            elif any(part.startswith(t) for part in re.split(r"[._\s-]+", low)):
                rank = 1
            elif t in low:
                rank = 2
            else:
                continue
            hits.append((rank, -e.get("last", 0), name, e))
        hits.sort(key=lambda h: (h[0], h[1], h[2]))
        return [(h[2], h[3]) for h in hits[:limit] if not (h[2].lower() == t and len(hits) == 1)]


class SuggestPopup(QFrame):
    """The dropdown itself. It never takes focus, so typing carries on in the box."""
    ROW = 44

    def __init__(self, edit: QLineEdit, memory: CreatorMemory, on_pick: Callable[[str, dict], None],
                 token: bool = False):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating); self.edit, self.mem, self.on_pick, self.token = edit, memory, on_pick, token
        widgets_mod.prepare_popup(self, "suggest", 12)
        v = QVBoxLayout(self); v.setContentsMargins(6, 6, 6, 6); v.setSpacing(0)
        self.list = QListWidget(); self.list.setFrameShape(QFrame.NoFrame); self.list.setIconSize(QSize(26, 26))
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(Qt.NoFocus); self.list.setMouseTracking(True); self.list.setCursor(Qt.PointingHandCursor)
        f = QFont(self.font()); f.setPixelSize(13); f.setWeight(QFont.DemiBold); f.setHintingPreference(QFont.PreferNoHinting)
        self.list.setFont(f)
        self.list.itemEntered.connect(self.list.setCurrentItem)
        self.list.itemClicked.connect(lambda it: self._pick(it))
        v.addWidget(self.list)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(60); self._timer.timeout.connect(self.refresh)
        edit.textEdited.connect(lambda _t: self._timer.start())
        edit.installEventFilter(self)

    def _restyle(self) -> None:
        a = widgets_mod.ACCENT[0]; txt = widgets_mod.TEXT[0]
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; }"
                                f"QListWidget::item {{ padding: 4px 8px; border-radius: 8px; color: {txt}; }}"
                                f"QListWidget::item:selected {{ background: {a}; color: white; }}")

    def _current_token(self) -> tuple[str, int, int]:
        """The word under the cursor (for boxes that hold several links/names)."""
        text, pos = self.edit.text(), self.edit.cursorPosition()
        if not self.token:
            return text, 0, len(text)
        start = text.rfind(" ", 0, pos) + 1; end = text.find(" ", pos)
        end = len(text) if end < 0 else end
        return text[start:end], start, end

    def refresh(self) -> None:
        word, _s, _e = self._current_token()
        if not self.edit.hasFocus() or len(word.lstrip("@")) < 1 or "/" in word or word.isdigit():
            self.hide(); return
        hits = self.mem.matches(word)
        if not hits:
            self.hide(); return
        self._restyle(); self.list.clear()
        groups = self.mem.qm.cfg.creators
        for name, e in hits:
            grp = e.get("kind") == "group"
            pm = avatar_pixmap(STORE[0], "group" if grp else "user", name, 26, list(groups.get(name, [])) if grp else None)
            n = e.get("count") or 0
            sub = (f"group · {len(groups.get(name, []))} accounts" if grp else (f"{n} post{'s' if n != 1 else ''}" if n else ""))
            it = QListWidgetItem(QIcon(pm), (name if grp else "@" + name) + (f"    {sub}" if sub else ""))
            it.setData(Qt.UserRole, (name, e)); it.setSizeHint(QSize(0, self.ROW - 4)); self.list.addItem(it)
        self.list.setCurrentRow(0)
        w = max(260, self.edit.width()); h = self.list.count() * (self.ROW - 4) + 12
        self.setFixedSize(w, h)
        g = self.edit.mapToGlobal(QPoint(0, self.edit.height() + 6)); self.move(g)
        if not self.isVisible():
            self.show(); widgets_mod.animate_popup_in(self, 120)

    def _pick(self, it=None) -> None:
        it = it or self.list.currentItem()
        if it is None:
            return
        name, e = it.data(Qt.UserRole); self.hide()
        if self.token:
            text = self.edit.text(); _w, s, end = self._current_token()
            ins = name if e.get("kind") == "group" else "@" + name
            new = text[:s] + ins + " " + text[end:].lstrip()
            self.edit.setText(new); self.edit.setCursorPosition(s + len(ins) + 1)
        self.on_pick(name, e)

    def eventFilter(self, obj, ev):
        if obj is self.edit:
            t = ev.type()
            if t == QEvent.KeyPress and self.isVisible():
                k = ev.key()
                if k in (Qt.Key_Down, Qt.Key_Up):
                    r = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                    self.list.setCurrentRow(max(0, min(self.list.count() - 1, r))); return True
                if k in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter) and self.list.currentItem() is not None:
                    self._pick(); return True
                if k == Qt.Key_Escape:
                    self.hide(); return True
            elif t == QEvent.FocusOut:
                QTimer.singleShot(150, lambda: self.hide() if not self.edit.hasFocus() else None)
            elif t in (QEvent.Hide, QEvent.Move, QEvent.Resize):
                self.hide()
        return False


def attach(edit: QLineEdit, memory: CreatorMemory, on_pick: Callable[[str, dict], None] = lambda n, e: None,
           token: bool = False) -> SuggestPopup:
    pop = SuggestPopup(edit, memory, on_pick, token); edit._suggest = pop
    return pop
