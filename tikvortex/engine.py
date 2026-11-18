"""The download queue: runs the Ez-TikTok engine in background threads and reports
per-post progress and results to the GUI through Qt signals."""
from __future__ import annotations

import io
import itertools
import json
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests
from PySide6.QtCore import QObject, Signal

from tikvortex.core.bulk import _post_id, iter_profile_urls, parse_profiles
from tikvortex.core.cache import save_cache
from tikvortex.core.download import find_all_downloads, normalize_post_input, process_one_url
from tikvortex.core.tikwm import (api_call_count, extract_media_id_from_url, extract_username_from_url,
                            resolve_tiktok_redirect)

URL_RE = re.compile(r"https?://(?:[\w-]+\.)*(?:tiktok\.com|douyin\.com)/[^\s\"'<>]+", re.IGNORECASE)
ID_RE = re.compile(r"(?<![\w/])(\d{15,20})(?![\w])")
HANDLE_RE = re.compile(r"(?<![\w/])@([\w.]{2,24})")

TERMINAL = {"Original", "Compressed", "Failed", "Already saved", "Skipped", "Deleted"}
ATTENTION = {"Compressed", "Failed"}

_tls = threading.local()


class _StoppedError(Exception):
    """Raised inside chunk progress callbacks to abort an active download immediately."""


# ------------------------------------------------------------------ stdout routing
class LogBridge(QObject):
    line = Signal(str)


class StdRouter(io.TextIOBase):
    """Replaces sys.stdout/sys.stderr. Every printed line goes to log.txt and the app log;
    lines printed while a queue item is being processed are also kept on that item, and
    a tool can capture its own output by setting a sink for its thread."""

    def __init__(self, log_path: str, bridge: Optional[LogBridge] = None):
        self.log_path = log_path
        self.bridge = bridge
        self._lock = threading.Lock()
        self._fh = None
        try:
            if os.path.isfile(log_path) and os.path.getsize(log_path) > 2_000_000:
                os.replace(log_path, log_path + ".1")
            self._fh = open(log_path, "a", encoding="utf-8")
        except OSError:
            self._fh = None

    def writable(self):
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        buf = getattr(_tls, "buf", "") + s
        *lines, rest = buf.split("\n")
        _tls.buf = rest
        for ln in lines:
            ln = ln.rstrip("\r")
            if "\r" in ln:
                ln = ln.split("\r")[-1]
            self._emit(ln)
        return len(s)

    def _emit(self, ln: str) -> None:
        item = getattr(_tls, "item", None)
        if item is not None:
            item.log.append(ln)
        sink = getattr(_tls, "sink", None)
        if sink is not None:
            try:
                sink(ln)
            except Exception:
                pass
        with self._lock:
            if self._fh:
                try:
                    self._fh.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + ln + "\n"); self._fh.flush()
                except Exception:
                    pass
        if self.bridge is not None:
            try:
                self.bridge.line.emit(ln)
            except RuntimeError:
                pass

    def flush(self):
        pass


def set_sink(fn: Optional[Callable[[str], None]]) -> None:
    _tls.sink = fn


# ------------------------------------------------------------------ data
_ids = itertools.count(1)


@dataclass
class Item:
    url: str
    force: bool = False
    folder: Optional[str] = None
    batch: int = 0
    id: int = field(default_factory=lambda: next(_ids))
    vid: str = ""
    username: str = ""
    caption: str = ""
    cover: str = ""
    state: str = "Queued"
    done: int = 0
    total: int = 0
    speed: float = 0.0
    info: str = ""
    path: str = ""
    kind: str = "video"
    retried: bool = False
    log: list = field(default_factory=list)
    _t: float = 0.0
    _last_emit: float = 0.0
    _last_bytes: int = 0
    _gen: int = 0
    _cancelled: bool = False
    _paused: bool = False         # this item on its own (the whole queue has QueueManager.paused)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def link(self) -> str:
        if self.vid and self.username:
            return f"https://www.tiktok.com/@{self.username}/{'photo' if self.kind == 'photo' else 'video'}/{self.vid}"
        return self.url


@dataclass
class Batch:
    id: int
    label: str
    items: list = field(default_factory=list)
    listing: bool = False
    started: float = field(default_factory=time.time)
    finished: bool = False
    bytes_done: int = 0
    _gen: int = 0

    def counts(self, by_id: dict) -> dict:
        c = {"Original": 0, "Compressed": 0, "Failed": 0, "Already saved": 0, "Skipped": 0, "left": 0, "total": 0}
        for i in self.items:
            it = by_id.get(i)
            if not it:
                continue
            c["total"] += 1
            if it.state in c:
                c[it.state] += 1
            if not it.terminal:
                c["left"] += 1
        return c


class _ItemExecutor:
    """Thread pool whose jobs remember which queue item they belong to and skip cancelled items."""

    def __init__(self, workers: int):
        self.ex = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="tv-dl")
        self.workers = max(1, workers)

    def submit(self, fn, *args, **kw) -> Future:
        item = getattr(_tls, "item", None)

        def run():
            if item is not None and getattr(item, "_cancelled", False):
                raise _StoppedError("stopped")
            _tls.item = item
            try:
                return fn(*args, **kw)
            finally:
                _tls.item = None
        return self.ex.submit(run)

    def shutdown(self, cancel: bool = False):
        self.ex.shutdown(wait=False, cancel_futures=cancel)


# ------------------------------------------------------------------ queue manager
class QueueManager(QObject):
    itemAdded = Signal(object)
    itemChanged = Signal(object)
    itemsRemoved = Signal(list)
    batchChanged = Signal(object)
    batchFinished = Signal(object, dict)
    message = Signal(str, str)          # level, text
    activeChanged = Signal(int)         # items not finished

    def __init__(self, cfg, cache: dict, state_path: str):
        super().__init__()
        self.cfg = cfg
        self.cache = cache
        self.state_path = state_path
        self.items: dict[int, Item] = {}
        self.order: list[int] = []
        self.batches: dict[int, Batch] = {}
        self.current: Optional[Batch] = None
        self._jobs: deque = deque()
        self._cv = threading.Condition()
        self._pause = threading.Event(); self._pause.set()
        self._stop_gen = 0
        self._thread: Optional[threading.Thread] = None
        self._ex: Optional[_ItemExecutor] = None
        self._batch_ids = itertools.count(1)
        self._lock = threading.RLock()

    # ---------------------------------------------------------- config
    def set_config(self, cfg) -> None:
        self.cfg = cfg
        if self._ex and self._ex.workers != cfg.download_workers and not self.busy():
            self._ex.shutdown(); self._ex = None

    @property
    def output_dir(self) -> str:
        return self.cfg.output_path

    # ---------------------------------------------------------- parsing input
    def parse(self, text: str, bare_names: bool = False) -> tuple[list[str], list[str]]:
        """(post links/IDs, profile specs) from whatever was pasted or dropped.
        bare_names: plain words like `sunny.studio` count as usernames even without the @."""
        text = (text or "").strip()
        posts: list[str] = []
        profiles: list[str] = []
        for tok in re.findall(r'"([^"]+\.(?:txt|url))"|(\S+\.(?:txt|url))', text, re.IGNORECASE):
            path = tok[0] or tok[1]
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        body = f.read()
                except OSError:
                    continue
                text = text.replace(path, " ")
                if path.lower().endswith(".url"):
                    m = re.search(r"^URL=(.+)$", body, re.MULTILINE)
                    body = m.group(1) if m else ""
                p2, pr2 = self.parse(body)
                posts += p2; profiles += pr2
        for m in URL_RE.finditer(text):
            u = m.group(0).rstrip(").,;]")
            if re.search(r"tiktok\.com/@[\w.]+/?(?:\?|$)", u):
                profiles.append("@" + re.search(r"@([\w.]+)", u).group(1))
            else:
                posts.append(u)
        rest = URL_RE.sub(" ", text)
        posts += ID_RE.findall(rest)
        rest = ID_RE.sub(" ", rest)
        handles = HANDLE_RE.findall(rest)
        if handles:
            profiles.append(" ".join("@" + h for h in handles))
        rest = HANDLE_RE.sub(" ", rest)
        groups = {g.lower(): g for g in self.cfg.creators}
        bare = []
        for w in re.split(r"[\s,]+", rest):
            if w and w.lower() in groups:
                profiles.append(groups[w.lower()])
            elif bare_names and re.fullmatch(r"(?=[\w.]*[A-Za-z_])[\w.]{2,24}", w or "") and not w.lower().endswith((".txt", ".url")):
                bare.append("@" + w)
        if bare:
            profiles.append(" ".join(bare))
        return list(dict.fromkeys(posts)), list(dict.fromkeys(profiles))

    # ---------------------------------------------------------- submitting
    def submit(self, text: str, force: bool = False, folder: Optional[str] = None, label: str = "",
               bare_names: bool = False) -> int:
        posts, profiles = self.parse(text, bare_names)
        n = 0
        if posts:
            b = self._new_batch(label or (f"{len(posts)} link" + ("s" if len(posts) != 1 else "")))
            for u in posts:
                self._add_item(Item(url=u, force=force, folder=folder, batch=b.id), b)
                n += 1
        for spec in profiles:
            b = self._new_batch(spec if spec.startswith("@") else spec, listing=True)
            with self._cv:
                self._jobs.append(("list", spec, b.id)); self._cv.notify()
            n += 1
        if not n:
            self.message.emit("warn", "Nothing to download there - paste TikTok links, post IDs, @usernames or a group name.")

        self._ensure_thread()
        return n

    def _new_batch(self, label: str, listing: bool = False) -> Batch:
        with self._lock:
            gen = self._stop_gen
        b = Batch(id=next(self._batch_ids), label=label, listing=listing, _gen=gen)
        self.batches[b.id] = b
        if self.current is None or self.current.finished:
            self.current = b
        self.batchChanged.emit(b)
        return b

    def _add_item(self, it: Item, b: Batch, enqueue: bool = True) -> None:
        with self._lock:
            it._gen = self._stop_gen
            it._cancelled = False
            self.items[it.id] = it; self.order.append(it.id); b.items.append(it.id)
        vid = extract_media_id_from_url(normalize_post_input(it.url))
        if vid:
            it.vid = vid
            ent = self.cache.get(vid) or {}
            it.caption = ent.get("title") or ""
            it.username = ent.get("username") or extract_username_from_url(it.url) or ""
            it.cover = ent.get("cover") or ""
        self.itemAdded.emit(it)
        if enqueue:
            with self._cv:
                self._jobs.append(("item", it.id, b.id)); self._cv.notify()
        self._changed_active()

    def retry(self, it: Item) -> None:
        """Try again in original quality (force: replaces a compressed copy in place)."""
        if not it.terminal:
            return
        with self._lock:
            it._gen = self._stop_gen
            it._cancelled = False
        it.force = True; it.state = "Queued"; it.done = it.total = 0; it.info = "retrying in original quality"; it.log.clear()
        b = self.batches.get(it.batch)
        if b:
            b.finished = False
            if self.current is None or self.current.finished:
                self.current = b
        self.itemChanged.emit(it)
        with self._cv:
            self._jobs.append(("item", it.id, it.batch)); self._cv.notify()
        self._ensure_thread(); self._changed_active()

    def retry_all_compressed(self) -> int:
        todo = [self.items[i] for i in self.order if self.items[i].state in ("Compressed", "Failed")]
        for it in todo:
            self.retry(it)
        return len(todo)

    def remove(self, ids: list[int]) -> None:
        with self._lock:
            gone = [i for i in ids if i in self.items and (self.items[i].terminal or self.items[i].state == "Queued")]
            for i in gone:
                it = self.items.pop(i); self.order.remove(i)
                it._cancelled = True
                it.state = "Skipped"
        if gone:
            self.itemsRemoved.emit(gone); self._changed_active()

    def cancel(self, it: Item) -> None:
        """Clear one item: stops it first if it's downloading (its partial file is removed)."""
        if not it.terminal:
            with self._lock:
                it._cancelled = True; it.state = "Skipped"; it.info = "cleared"; it.speed = 0.0; it._paused = False
            self.itemChanged.emit(it)
        with self._lock:
            if it.id in self.items:
                self.items.pop(it.id); self.order.remove(it.id)
        self.itemsRemoved.emit([it.id]); self._check_batches(); self._changed_active()

    def pause_item(self, it: Item, paused: bool) -> None:
        """Pause or resume one item. A downloading item stops mid-file and carries on from there."""
        if it.terminal:
            return
        it._paused = paused
        if paused:
            if it.state in ("Queued",):
                it.state = "Paused"; it.info = "paused"
            self.itemChanged.emit(it)
        else:
            if it.state == "Paused" and not it.done and not getattr(it, "_running", False):
                it.state = "Queued"; it.info = "queued"
                with self._cv:
                    self._jobs.append(("item", it.id, it.batch)); self._cv.notify()
                self._ensure_thread()
            self.itemChanged.emit(it)

    def mark_deleted(self, it: Item) -> None:
        """The downloaded file was moved to the Recycle Bin or deleted outside the app."""
        if it.state in ("Original", "Compressed", "Already saved"):
            it.state = "Deleted"; it.info = "file was deleted"; self.itemChanged.emit(it)

    def clear_finished(self) -> None:
        self.remove([i for i in self.order if self.items[i].terminal])

    # ---------------------------------------------------------- control
    def busy(self) -> bool:
        return any(not self.items[i].terminal for i in self.order) or bool(self._jobs)

    def known_posts(self, username: str, n: int = 3) -> list:
        """A few post IDs we already have from this creator (newest first) - yt-dlp can find the profile from them."""
        u = (username or "").lstrip("@").lower()
        ids = [pid for pid, e in list(self.cache.items()) if isinstance(e, dict) and (e.get("username") or "").lower() == u]
        return sorted(ids, key=lambda p: int(p) if str(p).isdigit() else 0, reverse=True)[:n]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for i in self.order if i in self.items and not self.items[i].terminal)

    def pause(self, paused: bool) -> None:
        """Pause or resume everything in the queue. It's the same as each item's own pause button, so the
        status card and the queue always agree. Things added afterwards start as normal."""
        for i in list(self.order):
            it = self.items.get(i)
            if it is not None and not it.terminal and it._paused != paused:
                self.pause_item(it, paused)
        if self.current:
            self.batchChanged.emit(self.current)

    @property
    def paused(self) -> bool:
        """True when something is left to download and all of it is paused."""
        act = [self.items[i] for i in self.order if i in self.items and not self.items[i].terminal]
        return bool(act) and all(it._paused for it in act)

    def stop(self) -> None:
        """Immediately stop all queued, waiting, and in-flight downloads."""
        with self._lock:
            self._stop_gen += 1
            to_cancel = [self.items[i] for i in list(self.order) if i in self.items and not self.items[i].terminal]
            for it in to_cancel:
                it._cancelled = True
                it.state = "Skipped"
                it.info = "stopped"
                it.speed = 0.0
            for b in self.batches.values():
                b.listing = False
            old_ex = self._ex
            self._ex = None
        with self._cv:
            self._jobs.clear()
            self._cv.notify_all()
        if old_ex is not None:
            old_ex.shutdown(cancel=True)
        for it in to_cancel:
            self.itemChanged.emit(it)
        self._pause.set()
        self._check_batches()
        self._changed_active()

    def _changed_active(self) -> None:
        self.activeChanged.emit(self.active_count())
        self.save_state()

    # ---------------------------------------------------------- persistence
    def save_state(self) -> None:
        pending = [{"url": self.items[i].url, "force": self.items[i].force, "folder": self.items[i].folder}
                   for i in self.order if not self.items[i].terminal]
        try:
            if pending:
                with open(self.state_path, "w", encoding="utf-8") as f:
                    json.dump(pending, f)
            elif os.path.isfile(self.state_path):
                os.remove(self.state_path)
        except OSError:
            pass

    def load_unfinished(self) -> list[dict]:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [d for d in data if isinstance(d, dict) and d.get("url")]
        except (OSError, ValueError):
            return []

    def resume(self, entries: list[dict]) -> None:
        if not entries:
            return
        b = self._new_batch(f"{len(entries)} unfinished")
        for d in entries:
            self._add_item(Item(url=d["url"], force=bool(d.get("force")), folder=d.get("folder"), batch=b.id), b)
        self._ensure_thread()

    # ---------------------------------------------------------- worker
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="tv-queue", daemon=True)
            self._thread.start()

    def _executor(self) -> _ItemExecutor:
        with self._lock:
            if self._ex is None:
                self._ex = _ItemExecutor(self.cfg.download_workers)
            return self._ex

    def _is_cancelled(self, it: Item) -> bool:
        with self._lock:
            return it._cancelled or it._gen != self._stop_gen

    def _run(self) -> None:
        last_hit_api = False
        while True:
            with self._cv:
                while not self._jobs:
                    if not self._cv.wait(timeout=30):
                        if not self._jobs:
                            return
                job = self._jobs.popleft()
            kind = job[0]
            try:
                if kind == "list":
                    b = self.batches.get(job[2])
                    if b is not None and b.listing and b._gen == self._stop_gen:
                        self._list_profiles(job[1], b)
                        last_hit_api = True
                else:
                    it = self.items.get(job[1])
                    if it is None or it.state != "Queued" or self._is_cancelled(it):
                        continue
                    if it._paused:                    # paused while waiting: it rejoins the line on resume
                        it.state = "Paused"; it.info = "paused"; self.itemChanged.emit(it); continue
                    if last_hit_api and self.cfg.batch_delay_sec:
                        deadline = time.monotonic() + self.cfg.batch_delay_sec
                        while time.monotonic() < deadline:
                            if self._is_cancelled(it):
                                break
                            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                        if self._is_cancelled(it):
                            continue
                    before = api_call_count()
                    self._process(it)
                    last_hit_api = api_call_count() != before
            except Exception as e:
                print(f"Error: {e}")
            self._check_batches()

    def _list_profiles(self, spec: str, b: Batch) -> None:
        parsed = parse_profiles(spec, self.cfg)
        if not parsed or not parsed[0]:
            self.message.emit("warn", f"Couldn't understand “{spec}”.")
            b.listing = False
            return
        accounts, group = parsed
        self.message.emit("info", f"Listing posts for {', '.join('@' + a for a in accounts)}…")
        by_id: dict[int, str] = {}
        said: list[str] = []
        set_sink(said.append)
        try:
            for acc in accounts:
                if not b.listing or b._gen != self._stop_gen:
                    break
                try:
                    for url in iter_profile_urls(acc, self.cfg, self.known_posts(acc)):
                        if not b.listing or b._gen != self._stop_gen:
                            break
                        by_id.setdefault(_post_id(url), url)
                except Exception as e:
                    print(f"Listing @{acc} failed: {e}")
        finally:
            set_sink(None)
        if not b.listing or b._gen != self._stop_gen:
            b.listing = False
            self.batchChanged.emit(b)
            return
        b.listing = False
        if not by_id:
            why = [ln.strip() for ln in said if "couldn't" in ln.lower() or "failed" in ln.lower() or "error" in ln.lower()]
            detail = ("\n" + "\n".join(w[:220] for w in why[-3:])) if why else ""
            self.message.emit("warn", f"Couldn't list {spec}. Paste the post links instead, or add cookies.txt "
                                      f"for restricted profiles. Details are in log.txt.{detail}")
            self.batchChanged.emit(b)
            return
        folder = group if group and self.cfg.folder_layout == "group" else None
        keys = sorted(by_id, reverse=self.cfg.profile_order == "newest")
        for k in keys:
            if b._gen != self._stop_gen:
                break
            self._add_item(Item(url=by_id[k], folder=folder, batch=b.id), b)
        self.batchChanged.emit(b)

    def _process(self, it: Item) -> None:
        if self._is_cancelled(it):
            return
        _tls.item = it
        try:
            it.state = "Waiting for TikWM"; it.info = "preparing the original file…"; it._t = time.monotonic()
            it.done = it.total = 0; it.speed = 0.0; it._last_bytes = 0; it._last_emit = 0.0
            self.itemChanged.emit(it)
            url = normalize_post_input(it.url.strip())
            if self._is_cancelled(it):
                return
            if re.search(r"//(vm|vt)\.tiktok\.com/|tiktok\.com/t/", url):
                url = resolve_tiktok_redirect(url, self.cfg)
            if self._is_cancelled(it):
                return
            it.vid = extract_media_id_from_url(url) or it.vid
            it.username = extract_username_from_url(url) or it.username
            if "/photo/" in url:
                it.kind = "photo"
            it._had = bool(it.vid) and not it.force and it.kind != "photo" and bool(find_all_downloads(self.output_dir, it.vid))
            res = process_one_url(url, self.cache, self.output_dir, self.cfg, None,
                                  executor=self._executor(), folder_name=it.folder, force=it.force,
                                  item_progress=self._progress_cb(it))
        except _StoppedError:
            res = False
        except Exception as e:
            if not self._is_cancelled(it):
                print(f"Error: {e}")
            res = False
        finally:
            _tls.item = None
        if self._is_cancelled(it):
            if isinstance(res, Future):
                res.cancel()
                res.add_done_callback(lambda f, it=it: self._cleanup_cancelled(it))
            else:
                self._cleanup_cancelled(it)
            return
        self._absorb_cache(it)
        if isinstance(res, Future):
            if not self._is_cancelled(it):
                if it.state != "Downloading" and not res.done():
                    it.state = "Downloading"; it.info = "starting download…"
                self.itemChanged.emit(it)
            res.add_done_callback(lambda f, it=it: self._finish(it, self._safe_result(f)))
        else:
            self._finish(it, bool(res))

    def _cleanup_cancelled(self, it: Item) -> None:
        """Remove any partial/new file written by an item that was stopped mid-flight."""
        if getattr(it, "_had", False):
            return
        for ln in it.log:
            m = re.match(r"Downloaded:\s*(.+)$", ln.strip())
            if m:
                p = m.group(1).strip()
                for cand in (p, p + ".part"):
                    if os.path.isfile(cand):
                        try:
                            os.remove(cand)
                        except OSError:
                            pass

    @staticmethod
    def _safe_result(f: Future) -> bool:
        if f.cancelled():
            return False
        try:
            return bool(f.result())
        except _StoppedError:
            return False
        except Exception as e:
            print(f"Download error: {e}")
            return False

    def _absorb_cache(self, it: Item) -> None:
        if self._is_cancelled(it):
            return
        ent = self.cache.get(it.vid) or {}
        it.caption = ent.get("title") or it.caption
        it.cover = ent.get("cover") or it.cover
        it.username = ent.get("username") or it.username
        if ent.get("images") or ent.get("source") == "gallery-dl":
            it.kind = "photo"
        self.itemChanged.emit(it)

    def _progress_cb(self, it: Item):
        lock = threading.Lock()

        def cb(done: int, total: Optional[int]) -> None:
            if self._is_cancelled(it):
                raise _StoppedError("stopped")
            if it._paused:                                  # pause mid-file: hold here until resumed
                it._running = True
                if it.state != "Paused":
                    it.state = "Paused"; it.info = "paused"; it.speed = 0.0; self.itemChanged.emit(it)
                    b = self.batches.get(it.batch)
                    if b:
                        self.batchChanged.emit(b)
                while it._paused and not self._is_cancelled(it):
                    time.sleep(0.15)
                if self._is_cancelled(it):
                    raise _StoppedError("stopped")
                it.state = "Downloading"; it._last_emit = time.monotonic(); it._last_bytes = done
            with lock:
                if self._is_cancelled(it):
                    raise _StoppedError("stopped")
                now = time.monotonic()
                if it.state != "Downloading":
                    it.state = "Downloading"; it._last_bytes = 0; it._last_emit = now; it.speed = 0.0
                it.done = done
                if total:
                    it.total = total
                dt = now - it._last_emit
                finished = bool(total and done >= total)
                if dt < 0.25 and not finished:
                    return
                inst = (done - it._last_bytes) / dt if dt > 0 else 0.0
                it.speed = inst if it.speed == 0 else it.speed * 0.6 + inst * 0.4
                it._last_bytes = done; it._last_emit = now
            self.itemChanged.emit(it)
            b = self.batches.get(it.batch)
            if b:
                self.batchChanged.emit(b)
        return cb

    def _finish(self, it: Item, ok: bool) -> None:
        if self._is_cancelled(it) or it.state == "Skipped":
            self._cleanup_cancelled(it)
            it.state = "Skipped"
            it.info = "stopped"
            self.itemChanged.emit(it)
            self._check_batches()
            self._changed_active()
            return
        text = "\n".join(it.log)
        low = text.lower()
        ent = self.cache.get(it.vid) or {}
        for ln in it.log:
            m = re.match(r"(?:Downloaded|Already downloaded|Already exists):\s*(.+)$", ln.strip())
            if m and os.path.isfile(m.group(1).strip()):
                it.path = m.group(1).strip()
        if not it.path and it.vid:
            found = find_all_downloads(self.output_dir, it.vid) if ok else []
            it.path = found[0] if found else ""
        size = ""
        if it.path and os.path.isfile(it.path):
            size = _fmt_bytes(os.path.getsize(it.path))
        posted = ""
        if ent.get("create_time"):
            try:
                posted = "posted " + time.strftime("%d %b %Y", time.localtime(int(ent["create_time"]))).lstrip("0")
            except Exception:
                posted = ""
        if ok:
            if not it.force and (getattr(it, "_had", False) or "already downloaded" in low or "already exists" in low):
                it.state = "Already saved"
                it.info = " · ".join(x for x in (size, "already in your library") if x)
            elif ent.get("images") or ent.get("source") == "gallery-dl" or it.kind == "photo":
                it.state = "Original"; it.kind = "photo"
                n = len(ent.get("images") or ent.get("local_files") or [])
                it.info = " · ".join(x for x in (f"{n} photos" if n else "photos", posted) if x)
            elif ent.get("quality") == "original":
                it.state = "Original"
                it.info = " · ".join(x for x in (size, posted) if x)
            else:
                it.state = "Compressed"
                it.info = " · ".join(x for x in (size, "TikWM didn't have the original ready in time") if x)
        else:
            it.state = "Failed"
            why = next((ln for ln in reversed(it.log) if any(k in ln.lower() for k in ("fail", "error", "unavailable", "skipped", "not tiktok"))), "")
            if it.force and "unavailable" in why.lower():
                why = "original still not available - your copy was kept"
            it.info = _short(why) or "couldn't download this post"
        it.done = it.total or it.done
        b = self.batches.get(it.batch)
        if b and it.path and os.path.isfile(it.path):
            b.bytes_done += os.path.getsize(it.path)
        self.itemChanged.emit(it)
        self._check_batches()
        self._changed_active()

    def _check_batches(self) -> None:
        with self._lock:
            self._check_batches_locked()

    def _check_batches_locked(self) -> None:
        for b in list(self.batches.values()):
            if b.finished or b.listing:
                continue
            if any((j[0] == "list" and j[2] == b.id) for j in list(self._jobs)):
                continue
            items = [self.items[i] for i in b.items if i in self.items]
            if any(not it.terminal for it in items):
                continue
            if self.cfg.retry_compressed_at_end:
                again = [it for it in items if it.state == "Compressed" and not it.retried and not it._cancelled]
                if again:
                    for it in again:
                        it.retried = True
                    print(f"Retrying {len(again)} compressed video(s) in original quality…")
                    for it in again:
                        self.retry(it)
                    continue
            b.finished = True
            counts = b.counts(self.items)
            self.batchChanged.emit(b)
            self.batchFinished.emit(b, counts)
        if self.current and self.current.finished:
            nxt = next((b for b in self.batches.values() if not b.finished), None)
            if nxt:
                self.current = nxt
                self.batchChanged.emit(nxt)

    def failed_links(self, b: Batch) -> list[str]:
        return [self.items[i].link for i in b.items if i in self.items and self.items[i].state == "Failed"]

    def shutdown(self) -> None:
        save_cache(self.cache, self.cfg.cache_path) if self.cache else None
        if self._ex:
            self._ex.shutdown(cancel=True)


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _short(s: str, n: int = 90) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


fmt_bytes = _fmt_bytes


# ------------------------------------------------------------------ TikWM status
class StatusProbe(QObject):
    changed = Signal(dict)

    def __init__(self, interval: int = 90):
        super().__init__()
        self.interval = interval
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="tv-probe").start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            info = {"state": "offline", "latency": None, "calls": api_call_count(), "checked": time.time()}
            try:
                t0 = time.monotonic()
                r = requests.get("https://www.tikwm.com/", timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                info["latency"] = time.monotonic() - t0
                info["code"] = r.status_code
                if r.status_code < 400:
                    info["state"] = "busy" if info["latency"] > 4 else "online"
                elif r.status_code in (403, 429, 503):
                    info["state"] = "busy"
            except requests.RequestException as e:
                info["error"] = e.__class__.__name__
            try:
                self.changed.emit(info)
            except RuntimeError:
                return
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()