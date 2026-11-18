"""Watchlist: creators (or creator groups) TikVortex checks for new posts, then downloads them or lets you know.

A watch starts from the creator's newest post at the time you add it, so only posts after that count as new.
Checks use TikWM's profile API (newest 35 posts per account) and keep running while the app sits in the tray.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QSizePolicy, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from .avatars import AvatarWidget
from .creators import ago, paint_badge
from .widgets import Card, EmptyBox, SegGroup, StyledComboBox, Switch, button, label

EVERY = {"Every hour": 1, "Every 6 hours": 6, "Every 12 hours": 12, "Daily": 24}


class Watchlist(QObject):
    changed = Signal()
    found = Signal(str, int, bool)        # key, how many new posts, downloaded automatically?
    _done = Signal(str, dict)

    def __init__(self, store, qm):
        super().__init__(); self.store, self.qm = store, qm
        self.busy: set[str] = set(); self._lock = threading.Lock()
        es = self.entries()                             # watches that never got a first look start from now
        if any(not e.get("baseline") and not e.get("added") for e in es):
            for e in es:
                if not e.get("baseline") and not e.get("added"):
                    e["added"] = time.time()
            self.store.set_ui("watchlist", es)
        from .core import bulk as _bulk                 # profile IDs found on earlier checks
        for e in self.entries():
            for a, sec in (e.get("sec") or {}).items():
                _bulk.SEC_UIDS.setdefault(a.lower(), sec)
        self._done.connect(self._finish)
        self.timer = QTimer(self); self.timer.setInterval(60_000); self.timer.timeout.connect(self.tick); self.timer.start()
        QTimer.singleShot(8000, self.tick)            # first look shortly after launch

    # ---------------------------------------------------------- data
    def entries(self) -> list[dict]:
        return list(self.store.ui("watchlist") or [])

    def entry(self, key: str) -> Optional[dict]:
        return next((e for e in self.entries() if e["key"] == key), None)

    def _save(self, entries: list[dict]) -> None:
        self.store.set_ui("watchlist", entries); self.changed.emit()

    def _update(self, key: str, quiet: bool = False, **kw) -> None:
        es = self.entries()
        for e in es:
            if e["key"] == key:
                e.update(kw)
        if quiet:                                     # a control on the card changed it: no need to rebuild the cards
            self.store.set_ui("watchlist", es)
        else:
            self._save(es)

    def accounts(self, e: dict) -> list[str]:
        return list(self.qm.cfg.creators.get(e["key"], [])) if e["kind"] == "group" else [e["key"]]

    def parse(self, text: str) -> Optional[tuple[str, str]]:
        """'@user', a profile link or a creator group name -> (key, kind)."""
        t = text.strip()
        if not t:
            return None
        if t in self.qm.cfg.creators:
            return t, "group"
        import re
        m = re.search(r"tiktok\.com/@([\w.\-]+)", t) or re.fullmatch(r"@?([\w.\-]+)", t)
        return (m.group(1).lower(), "user") if m else None

    def add(self, text: str) -> Optional[str]:
        got = self.parse(text)
        if not got:
            return None
        key, kind = got
        if self.entry(key) is None:
            auto = (self.store.ui("watch_default") or "Download") == "Download"
            es = self.entries() + [{"key": key, "kind": kind, "every": 6, "auto": auto, "last": 0, "baseline": {},
                                    "pending": [], "error": "", "added": time.time()}]
            self._save(es); self.check(key)
        return key

    def remove(self, key: str) -> None:
        self._save([e for e in self.entries() if e["key"] != key])

    def pending_total(self) -> int:
        return sum(len(e.get("pending") or []) for e in self.entries())

    # ---------------------------------------------------------- checking
    def _downloading(self) -> bool:
        """True while the queue is actively downloading (paused items don't count)."""
        try:
            return any(not it.terminal and not it._paused for it in list(self.qm.items.values()))
        except Exception:
            return False

    def tick(self) -> None:
        # Scheduled checks wait for downloads to finish: downloads use TikWM too, and TikWM refuses (HTTP 403)
        # when it gets more than about one request a second.
        if self._downloading():
            return
        now = time.time()
        for e in self.entries():
            if e["key"] not in self.busy and now - (e.get("last") or 0) >= e.get("every", 6) * 3600:
                self.check(e["key"])

    def check_all(self) -> None:
        for e in self.entries():
            self.check(e["key"])

    def check(self, key: str) -> None:
        e = self.entry(key)
        if e is None or key in self.busy:
            return
        self.busy.add(key); self.changed.emit()
        accs, cfg = self.accounts(e), self.qm.cfg
        want_hl = e.get("highlights", True)

        def work():
            # Same order as profile downloads: TikWM, then yt-dlp, then gallery-dl (if it's switched on).
            from itertools import islice
            from .core.bulk import (_iter_via_gallery_dl_cli, _list_via_gallery_dl_api, _list_via_ytdlp, _post_id,
                                    _post_url, _tikwm_list_page)
            out: dict = {"urls": {}, "error": "", "stories": {}}
            with self._lock:                         # one account at a time: TikWM allows about a request a second
                for a in accs:
                    urls: list[str] = []; why = []; hl_ids: list = []
                    if want_hl:                         # highlight stories (listed through the highlights viewer)
                        try:
                            from .core.highlights import fetch_highlights, post_url
                            hl = fetch_highlights(a, cfg)
                            out["stories"][a] = {it["post_id"]: post_url(a, it) for it in hl}
                            hl_ids = sorted((it["post_id"] for it in hl), key=int, reverse=True)
                        except Exception:
                            pass                        # no highlights / viewer unavailable
                    j, _used, diag = _tikwm_list_page(a, "0", cfg, None)
                    if not (j and j.get("code") == 0) and "403" in (diag or ""):
                        time.sleep(4)                   # refused: usually rate limiting - wait, then try once more
                        j, _used, diag = _tikwm_list_page(a, "0", cfg, None)
                    if j and j.get("code") == 0:
                        for vd in (j.get("data") or {}).get("videos") or []:
                            pid = str(vd.get("video_id") or vd.get("aweme_id") or "")
                            if pid.isdigit():
                                urls.append(_post_url(a, pid, bool(vd.get("images")), ((vd.get("author") or {}).get("unique_id")) or a))
                    else:
                        m = re.search(r"HTTP (\d+)", diag or "")
                        why.append(f"TikWM refused (HTTP {m.group(1)})" if m else "TikWM: " + ((diag or "no answer")[:60]))
                    if not urls:
                        from .core import bulk as _bulk
                        _bulk.LAST_YTDLP_ERROR[0] = ""
                        try:
                            # yt-dlp needs one of their post IDs when TikTok blocks the profile page: from your
                            # downloads, their highlights, or a TikWM search for their name
                            known = self.qm.known_posts(a) or hl_ids[:3] or self._search_ids(a, cfg)
                            urls = _list_via_ytdlp(a, cfg, limit=40, known_posts=known) or []
                        except Exception as e:
                            _bulk.LAST_YTDLP_ERROR[0] = str(e)
                        if not urls:
                            said = re.sub(r"^ERROR:\s*(\[[^\]]*\]\s*)*", "", _bulk.LAST_YTDLP_ERROR[0].strip())
                            if "does not have any videos" in said:
                                time.sleep(1.1); continue   # no posts yet: nothing to report, the next check looks again
                            why.append("yt-dlp: " + (said[:90] or "found no posts"))
                    if not urls and cfg.gallery_dl_fallback:
                        try:
                            urls = _list_via_gallery_dl_api(a, cfg) or []
                        except Exception:
                            urls = []
                        if not urls:               # gallery-dl's slow method (the one profile downloads end with),
                            try:                       # stopped after the newest 40 posts
                                urls = list(islice(_iter_via_gallery_dl_cli(a, cfg), 40))
                            except Exception:
                                urls = []
                        if not urls:
                            why.append("gallery-dl couldn't list it")
                    if urls:
                        out["urls"][a] = {str(_post_id(u)): u for u in urls if _post_id(u)}
                    else:
                        out["error"] = " · ".join(why)[:220]
                    time.sleep(1.1)
            from .core import bulk as _bulk
            out["sec"] = {a: _bulk.SEC_UIDS[a.lower()] for a in accs if a.lower() in _bulk.SEC_UIDS}
            self._done.emit(key, out)
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _search_ids(username: str, cfg) -> list:
        """Post IDs by this creator from TikWM's search (best effort - it may be blocked too)."""
        try:
            from .core.tikwm import http
            r = http().post("https://www.tikwm.com/api/feed/search", data={"keywords": username, "count": 20, "cursor": 0},
                            timeout=cfg.request_timeout)
            vids = ((r.json() or {}).get("data") or {}).get("videos") or []
            u = username.lower()
            return [str(v.get("video_id")) for v in vids
                    if ((v.get("author") or {}).get("unique_id") or "").lower() == u and str(v.get("video_id") or "").isdigit()][:3]
        except Exception:
            return []

    def _finish(self, key: str, out: dict) -> None:
        self.busy.discard(key)
        e = self.entry(key)
        if e is None:
            self.changed.emit(); return
        base = dict(e.get("baseline") or {}); pending = list(e.get("pending") or []); new_urls = []
        for a, urls in out["urls"].items():
            ids = sorted(int(p) for p in urls)
            if not ids:
                continue
            if a not in base:
                # first successful look: everything posted since you started watching counts as new (TikTok post
                # IDs start with the time they were posted), so a creator with no posts yet still works
                start = int(e.get("added") or 0) << 32
                if not start:
                    base[a] = ids[-1]; continue       # (watches from older versions: start from the newest post)
                base[a] = start
            fresh = [p for p in ids if p > base[a]]
            new_urls += [urls[str(p)] for p in fresh]
            if fresh:
                base[a] = max(fresh)
        seen = {a: set(v) for a, v in (e.get("stories_seen") or {}).items()}; new_stories = 0
        for a, stories in out.get("stories", {}).items():
            if a not in seen:                         # first look: what's there now counts as already seen
                seen[a] = set(stories); continue
            fresh = [pid for pid in stories if pid not in seen[a]]
            new_urls += [stories[pid] for pid in fresh]; new_stories += len(fresh)
            seen[a] |= set(fresh)
        new_urls = [u for u in new_urls if u not in pending]
        if new_urls:
            if e.get("auto"):
                self._queue(e, new_urls)
            else:
                pending += new_urls
        failed = not out["urls"] and not out.get("stories")
        sec = dict(e.get("sec") or {}); sec.update(out.get("sec") or {})
        # a failed check tries again in 15 minutes instead of waiting for the next scheduled one
        last = time.time() - (e.get("every", 6) * 3600 - 900 if failed else 0)
        err = out["error"] if not out["urls"] else ""
        if err and out.get("stories"):
            err = "posts: " + err + " (highlights were checked)"
        self._update(key, last=last, checked=time.time(), baseline=base, pending=pending, error=err,
                     stories_seen={a: sorted(v) for a, v in seen.items()}, sec=sec)
        if new_urls:
            self.found.emit(key, len(new_urls), bool(e.get("auto")))

    def _queue(self, e: dict, urls: list[str]) -> None:
        folder = e["key"] if e["kind"] == "group" and self.qm.cfg.folder_layout == "group" else None
        self.qm.submit(" ".join(urls), folder=folder, label=("@" if e["kind"] == "user" else "") + e["key"] + " · new posts")

    def download_pending(self, key: str) -> None:
        e = self.entry(key)
        if e and e.get("pending"):
            self._queue(e, e["pending"]); self._update(key, pending=[])


# ------------------------------------------------------------------ page
class _NewBadge(QWidget):
    def __init__(self, pal, text: str, h: int = 20):
        super().__init__(); self.pal, self.text, self.h = pal, text, h
        from PySide6.QtGui import QFont, QFontMetricsF
        f = QFont("Geist"); f.setPixelSize(10); f.setWeight(QFont.ExtraBold)
        self.setFixedSize(int(max(h, QFontMetricsF(f).horizontalAdvance(text) + 12)), h)

    def paintEvent(self, ev):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        paint_badge(p, self.width() / 2, self.height() / 2, self.text, QColor(self.pal.accent[0]), QColor("white"), self.h)


class WatchCard(QFrame):
    def __init__(self, page, e: dict):
        super().__init__(); self.setObjectName("opt"); self.key = e["key"]
        wl, pal = page.wl, page.pal
        h = QHBoxLayout(self); h.setContentsMargins(14, 12, 14, 12); h.setSpacing(14)
        h.addWidget(AvatarWidget(e["kind"], e["key"], 48, wl.accounts(e) if e["kind"] == "group" else None))
        v = QVBoxLayout(); v.setSpacing(2)
        tl = QHBoxLayout(); tl.setSpacing(8)
        t = QLabel(e["key"] if e["kind"] == "group" else "@" + e["key"]); t.setStyleSheet("font-weight: 800; font-size: 14px;"); tl.addWidget(t)
        n = len(e.get("pending") or [])
        if n:
            tl.addWidget(_NewBadge(pal, f"{n} new"))
        tl.addStretch(); v.addLayout(tl)
        if e["key"] in wl.busy:
            sub = "Checking now…"
        elif e.get("error"):
            sub = f"Couldn't check, trying again soon - {e['error']}"
        else:
            sub = f"Checked {ago(e.get('checked') or e['last'])}" if e.get("last") else "Not checked yet"
        if e["kind"] == "group":
            sub = f"Group · {len(wl.accounts(e))} accounts  ·  " + sub
        sl = label(sub, "mute", 11); sl.setWordWrap(True); sl.setMinimumWidth(1)
        sl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred); sl.setToolTip(e.get("error") or "")
        v.addWidget(sl); h.addLayout(v, 1)
        if n:
            d = button(f"Download {n}", "primary", "mdi6.download", pal); d.setFixedHeight(36); d.setStyleSheet("padding: 0 16px;")
            d.clicked.connect(lambda: wl.download_pending(self.key)); h.addWidget(d)
        cb = StyledComboBox(); cb.addItems(list(EVERY)); cb.setFixedWidth(178)
        cb.setCurrentText(next((k for k, v in EVERY.items() if v == e.get("every", 6)), "Every 6 hours"))
        cb.currentTextChanged.connect(lambda t: wl._update(self.key, quiet=True, every=EVERY[t])); h.addWidget(cb)
        hw = QHBoxLayout(); hw.setSpacing(8); hw.addWidget(label("Highlights", "mute", 11))
        hs = Switch(bool(e.get("highlights", True)), pal.accent); hs.setToolTip("Also check their highlight stories")
        hs.toggled.connect(lambda on: wl._update(self.key, quiet=True, highlights=on)); hw.addWidget(hs); h.addLayout(hw)
        sw = QHBoxLayout(); sw.setSpacing(8); sw.addWidget(label("Auto-download", "mute", 11))
        s = Switch(bool(e.get("auto")), pal.accent); s.toggled.connect(lambda on: wl._update(self.key, quiet=True, auto=on)); sw.addWidget(s); h.addLayout(sw)
        c = button("Check now", "ghost", "ph.arrow-clockwise-bold", pal); c.setFixedHeight(36); c.clicked.connect(lambda: wl.check(self.key))
        c.setEnabled(e["key"] not in wl.busy); h.addWidget(c)
        x = button("", "ghost", "ph.x-bold", pal); x.setFixedSize(36, 36); x.setToolTip("Stop watching")
        x.clicked.connect(lambda: wl.remove(self.key)); h.addWidget(x)

    def flash(self) -> None:
        self.setProperty("down", True); self.style().unpolish(self); self.style().polish(self)
        QTimer.singleShot(900, lambda: (self.setProperty("down", False), self.style().unpolish(self), self.style().polish(self)))


class WatchlistPage(QWidget):
    title = "WATCHLIST"
    subtitle = "Creators TikVortex checks for new posts"

    def __init__(self, wl: Watchlist, pal):
        super().__init__(); self.wl, self.pal = wl, pal; self.cards: dict[str, WatchCard] = {}
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
        bar = Card(margins=(16, 12, 16, 12)); bh = QHBoxLayout(); bh.setSpacing(10)
        self.input = QLineEdit(); self.input.setPlaceholderText("@username, a profile link, or a creator group name")
        self.input.returnPressed.connect(self._add); bh.addWidget(self.input, 1)
        w = button("Watch", "primary", "ph.eye-fill", pal); w.setFixedHeight(38); w.setStyleSheet("padding: 0 18px;"); w.clicked.connect(self._add); bh.addWidget(w)
        bh.addSpacing(6)
        ca = button("Check all now", "ghost", "ph.arrow-clockwise-bold", pal); ca.setFixedHeight(38); ca.clicked.connect(wl.check_all); bh.addWidget(ca)
        bar.v.addLayout(bh); v.addWidget(bar)
        c = Card(margins=(20, 14, 20, 16), spacing=13); hh = QHBoxLayout(); hh.setSpacing(10)
        t = QLabel("WATCHING"); t.setObjectName("h2"); hh.addWidget(t, 0, Qt.AlignVCenter)
        self.sub = label("", "mute", 12); hh.addWidget(self.sub, 0, Qt.AlignVCenter); hh.addStretch()
        hh.addWidget(label("New watches", "mute", 12), 0, Qt.AlignVCenter)
        seg = SegGroup(["Download", "Just notify"], wl.store.ui("watch_default") or "Download")
        seg.changed.connect(lambda s: wl.store.set_ui("watch_default", s)); seg.setToolTip("What a newly added creator does with new posts")
        hh.addWidget(seg, 0, Qt.AlignVCenter)
        c.v.addLayout(hh)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
        body = QWidget(); self.list = QVBoxLayout(body); self.list.setContentsMargins(0, 0, 6, 0); self.list.setSpacing(10)
        self.list.setAlignment(Qt.AlignTop); self.scroll.setWidget(body); c.v.addWidget(self.scroll, 1)
        self.empty = EmptyBox("Nothing on your watchlist yet\nType a @username above and press Watch", pal); self.empty.setFixedHeight(72)    # the same size as a watched creator's card
        self.list.addWidget(self.empty)
        v.addWidget(c, 1)
        wl.changed.connect(self.refresh); self.refresh()

    def _add(self) -> None:
        key = self.wl.add(self.input.text())
        if key:
            self.input.clear(); self.focus(key)

    def refresh(self) -> None:
        for card in self.cards.values():
            card.setParent(None); card.deleteLater()
        self.cards = {}
        es = self.wl.entries()
        for e in es:
            card = WatchCard(self, e); self.list.insertWidget(self.list.count() - 1, card); self.cards[e["key"]] = card
        self.empty.setVisible(not es)
        due = [e.get("last", 0) + e.get("every", 6) * 3600 - time.time() for e in es]
        nxt = max(0, min(due)) if due else 0
        when = "soon" if nxt < 60 else (f"in {int(nxt // 60)} min" if nxt < 3600 else f"in {nxt / 3600:.0f} h")
        self.sub.setText(f"{len(es)} creator{'s' if len(es) != 1 else ''}" + (f" · next check {when}" if es else ""))

    def focus(self, key: str) -> None:
        card = self.cards.get(key)
        if card is not None:
            QTimer.singleShot(50, lambda: (self.scroll.ensureWidgetVisible(card), card.flash()))

    def restyle(self, pal) -> None:
        self.pal = pal; self.refresh()
