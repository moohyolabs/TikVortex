"""Background loading of cover images (from TikWM links) and library thumbnails."""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import requests
from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".m4v")


class CoverLoader(QObject):
    """Downloads small cover images once and hands back QPixmaps on the GUI thread."""
    _ready = Signal(str, QImage)

    def __init__(self, cache_dir: str):
        super().__init__()
        self.dir = cache_dir; os.makedirs(cache_dir, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tv-cover")
        self.mem: dict[str, QPixmap] = {}
        self.waiting: dict[str, list[Callable]] = {}
        self._ready.connect(self._deliver)

    def request(self, url: str, cb: Callable[[QPixmap], None]) -> None:
        if not url:
            return
        if url in self.mem:
            cb(self.mem[url]); return
        if url in self.waiting:
            self.waiting[url].append(cb); return
        self.waiting[url] = [cb]
        self.pool.submit(self._fetch, url)

    def _fetch(self, url: str) -> None:
        path = os.path.join(self.dir, hashlib.sha1(url.encode()).hexdigest()[:20] + ".jpg")
        img = QImage()
        if os.path.isfile(path):
            img.load(path)
        if img.isNull():
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"})
                if r.ok:
                    img.loadFromData(r.content)
                    if not img.isNull():
                        img = img.scaled(240, 320, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                        img.save(path, "JPG", 85)
            except requests.RequestException:
                pass
        self._ready.emit(url, img)

    def _deliver(self, url: str, img: QImage) -> None:
        cbs = self.waiting.pop(url, [])
        if img.isNull():
            return
        pm = QPixmap.fromImage(img); self.mem[url] = pm
        for cb in cbs:
            try:
                cb(pm)
            except RuntimeError:
                pass


class ThumbLoader(QObject):
    """Library thumbnails: images are scaled in a thread; videos get their first frame via
    Qt Multimedia (one at a time). Results are cached on disk by post ID."""
    ready = Signal(str, QPixmap)          # key, pixmap
    _img = Signal(str, QImage)

    def __init__(self, cache_dir: str, size: int = 320):
        super().__init__()
        self.dir = cache_dir; os.makedirs(cache_dir, exist_ok=True); self.size = size
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tv-thumb")
        self.video_queue: list[tuple[str, str]] = []
        self.pending: set[str] = set()
        self.player = None; self.sink = None; self._cur = None
        self._timeout = QTimer(self); self._timeout.setSingleShot(True); self._timeout.timeout.connect(self._video_failed)
        self._img.connect(self._got)

    def cache_path(self, key: str) -> str:
        return os.path.join(self.dir, f"{key}.jpg")

    def request(self, key: str, path: str) -> None:
        if key in self.pending:
            return
        self.pending.add(key)
        cp = self.cache_path(key)
        if os.path.isfile(cp) and os.path.getmtime(cp) >= os.path.getmtime(path):
            self.pool.submit(self._load_cached, key, cp); return
        if path.lower().endswith(IMAGE_EXTS):
            self.pool.submit(self._load_image, key, path)
        else:
            self.video_queue.append((key, path))
            if self._cur is None:
                self._next_video()

    def _load_cached(self, key: str, cp: str) -> None:
        self._img.emit(key, QImage(cp))

    def _load_image(self, key: str, path: str) -> None:
        r = QImageReader(path); r.setAutoTransform(True)
        s = r.size()
        if s.isValid():
            r.setScaledSize(s.scaled(QSize(self.size, self.size), Qt.KeepAspectRatioByExpanding))
        img = r.read()
        if not img.isNull():
            img.save(self.cache_path(key), "JPG", 85)
        self._img.emit(key, img)

    def _got(self, key: str, img: QImage) -> None:
        self.pending.discard(key)
        if not img.isNull():
            self.ready.emit(key, QPixmap.fromImage(img))

    # ---- videos ----
    def _ensure_player(self) -> bool:
        if self.player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        except Exception:
            return False
        self.player = QMediaPlayer(self)          # no audio output: thumbnails only, nothing is heard
        self.sink = QVideoSink(self); self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player.errorOccurred.connect(lambda *a: self._video_failed())
        return True

    def _next_video(self) -> None:
        if not self.video_queue:
            self._cur = None; return
        if not self._ensure_player():
            for k, _p in self.video_queue:
                self.pending.discard(k)
            self.video_queue.clear(); self._cur = None; return
        self._cur = self.video_queue.pop(0)
        self.player.setSource(QUrl.fromLocalFile(self._cur[1]))
        self.player.play(); self._timeout.start(4000)

    def _frame(self, frame) -> None:
        if self._cur is None or not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        self._timeout.stop()
        key, _path = self._cur
        self.player.stop(); self.player.setSource(QUrl())
        img = img.scaled(self.size, self.size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        img.save(self.cache_path(key), "JPG", 85)
        self._got(key, img)
        self._cur = None
        QTimer.singleShot(0, self._next_video)

    def _video_failed(self) -> None:
        if self._cur is None:
            return
        self.pending.discard(self._cur[0])
        try:
            self.player.stop(); self.player.setSource(QUrl())
        except Exception:
            pass
        self._cur = None
        QTimer.singleShot(0, self._next_video)
