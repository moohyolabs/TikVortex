from __future__ import annotations

import os
import sys
import re
import threading
import time
from typing import Optional
from urllib.parse import quote

import requests

from .config import AppConfig, load_config

SUBMIT_URL = "https://tikwm.com/api/video/task/submit"
RESULT_BASE = "https://tikwm.com/api/video/task/result?task_id="
QUICK_API = "https://www.tikwm.com/api/"

TIKWM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://tikwm.com",
    "Referer": "https://tikwm.com/",
    "x-requested-with": "XMLHttpRequest",
}


_local = threading.local()
_api_calls = 0


def gallery_dl_command(*args: str) -> Optional[list]:
    """Command line that runs gallery-dl, or None if it isn't available.
    In the packaged app sys.executable is TikVortex itself, so gallery-dl must be
    installed separately (gallery-dl.exe on PATH or next to the app)."""
    import shutil
    if not getattr(sys, "frozen", False):
        return [sys.executable, *args, "-m", "gallery_dl"]
    here = os.path.dirname(os.path.abspath(sys.executable))
    for name in ("gallery-dl.exe", "gallery-dl"):
        p = os.path.join(here, name)
        if os.path.isfile(p):
            return [p]
    found = shutil.which("gallery-dl")
    return [found] if found else None


def http() -> requests.Session:
    """Per-thread Session so connections to TikWM / the CDN are reused (keep-alive)."""
    sess = getattr(_local, "session", None)
    if sess is None:
        sess = requests.Session()
        _local.session = sess
    return sess


def api_call_count() -> int:
    """How many TikWM extractions have run (used to skip batch delays for cached posts)."""
    return _api_calls


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "unknown"


def load_sessionid(cfg: Optional[AppConfig] = None) -> Optional[str]:
    cfg = cfg or load_config()
    p = cfg.sessionid_path
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            line = f.readline().strip()
    except Exception:
        return None
    if not line or line.startswith("#"):
        return None
    if line.lower().startswith("sessionid="):
        line = line[10:].strip()
    if line.upper().startswith("PASTE_"):
        return None
    return line if line else None


def tikwm_headers_with_session(session_id: Optional[str]) -> dict:
    h = dict(TIKWM_HEADERS)
    if session_id:
        h["Referer"] = f"https://www.tikwm.com/originalDownloader.html?cookie=sessionid={session_id}"
        h["x-proxy-cookie"] = f"sessionid={session_id}"
    return h


def normalize_tiktok_url(url: str) -> str:
    u = url.strip()
    if "#" in u:
        u = u.split("#")[0]
    if "?" in u:
        u = u.split("?")[0]
    u = u.rstrip("/")
    if u.startswith("http://"):
        u = "https://" + u[7:]
    return u


def extract_username_from_url(url: str) -> Optional[str]:
    m = re.search(r"tiktok\.com/@([\w.-]+)", url, re.IGNORECASE)
    return m.group(1) if m else None


def extract_media_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/(?:video|photo)/(\d+)", url)
    return m.group(1) if m else None


def content_type_from_url(url: str) -> str:
    u = url.lower()
    if "/story" in u:
        return "story"
    if "highlight" in u:
        return "highlight"
    if "/photo/" in u:
        return "photo"
    return "video"


def url_candidates(tiktok_url: str) -> list[str]:
    normalized = normalize_tiktok_url(tiktok_url)
    video_id = extract_media_id_from_url(normalized)
    candidates = [normalized]
    if video_id:
        for u in (
            video_id,
            f"https://www.tiktok.com/video/{video_id}",
            f"https://www.tiktok.com/@tiktok/video/{video_id}",
            f"https://m.tiktok.com/v/{video_id}.html",
            f"https://www.tiktok.com/@/video/{video_id}",
        ):
            if u not in candidates:
                candidates.append(u)
    return candidates


def resolve_tiktok_redirect(url: str, cfg: Optional[AppConfig] = None) -> str:
    cfg = cfg or load_config()
    u = url.strip()
    if not re.search(r"https?://(vt|vm)\.tiktok\.com/", u, re.IGNORECASE):
        return u
    try:
        r = http().get(
            u,
            headers={"User-Agent": TIKWM_HEADERS["User-Agent"]},
            timeout=cfg.request_timeout,
            allow_redirects=True,
        )
        if r.url and "tiktok.com" in r.url.lower():
            return r.url
    except requests.RequestException:
        pass
    return u


def _post_meta(d: dict, author: dict) -> dict:
    """Details shown in the app's Info panel (saved at download time; counts are as of then)."""
    music = d.get("music_info") or {}
    keys = ("play_count", "digg_count", "comment_count", "share_count", "collect_count", "duration", "size", "region")
    meta = {k: d.get(k) for k in keys if d.get(k) is not None}
    meta.update({"nickname": author.get("nickname") or "", "avatar": author.get("avatar") or "",
                 "music": (music.get("title") or "") if isinstance(music, dict) else "", "saved_at": int(time.time())})
    return meta


def _parse_tikwm_entry(j: dict, username_from_url: str, video_id_from_url: Optional[str]) -> Optional[dict]:
    data = j.get("data")
    if not data:
        return None

    if isinstance(data, dict) and (data.get("play") or data.get("hdplay") or data.get("wmplay")):
        author = data.get("author") or {}
        vid = data.get("id") or video_id_from_url or "unknown"
        if isinstance(vid, (int, float)):
            vid = str(int(vid))
        username = author.get("unique_id") or author.get("uniqueId") or author.get("nickname") or username_from_url
        profile_uid = author.get("id")
        return {
            "play_url": data.get("hdplay") or data.get("play") or data.get("wmplay"),
            "images": data.get("images") or [],
            "username": sanitize_filename(username or username_from_url),
            "video_id": str(vid),
            "create_time": data.get("create_time"),
            "profile_uid": str(profile_uid) if profile_uid is not None else "unknown",
            "title": (data.get("title") or "").strip(),
            "cover": data.get("cover") or data.get("origin_cover") or "",
            "meta": _post_meta(data, author),
        }

    detail = data.get("detail") if isinstance(data, dict) else None
    if detail is None and isinstance(data, dict):
        detail = data
    if not isinstance(detail, dict):
        return None

    play_url = detail.get("play_url") or detail.get("url") or detail.get("play") or detail.get("hdplay")
    images = detail.get("images") or (data.get("images") if isinstance(data, dict) else []) or []
    author = detail.get("author") or (data.get("author") if isinstance(data, dict) else {}) or {}
    username = (author.get("unique_id") or author.get("nickname") or username_from_url or "").strip()
    username = sanitize_filename(username) if username else sanitize_filename(username_from_url)
    vid = detail.get("video_id") or (data.get("video_id") if isinstance(data, dict) else None) or video_id_from_url or "unknown"
    if isinstance(vid, (int, float)):
        vid = str(int(vid))
    create_time = detail.get("create_time") or detail.get("createTime") or (data.get("create_time") if isinstance(data, dict) else None)
    profile_uid = author.get("id") or author.get("uid") or author.get("uniqueId")
    if profile_uid is not None:
        profile_uid = str(profile_uid)
    else:
        profile_uid = "unknown"

    if not play_url and not images:
        return None

    return {
        "play_url": play_url,
        "username": username,
        "video_id": vid,
        "images": images if isinstance(images, list) else [],
        "create_time": create_time,
        "profile_uid": profile_uid,
        "title": (detail.get("title") or detail.get("desc") or "").strip(),
        "cover": detail.get("cover") or detail.get("origin_cover") or "",
        "meta": _post_meta(detail, author if isinstance(author, dict) else {}),
    }


def submit_tikwm_quick(tiktok_url: str, cfg: Optional[AppConfig] = None) -> Optional[dict]:
    cfg = cfg or load_config()
    session_id = load_sessionid(cfg)
    api_headers = tikwm_headers_with_session(session_id)
    username_from_url = extract_username_from_url(tiktok_url) or "unknown"
    video_id_from_url = extract_media_id_from_url(tiktok_url)

    for candidate in url_candidates(tiktok_url):
        try:
            url = f"{QUICK_API}?url={quote(candidate)}&hd=1"
            r = http().get(url, headers=api_headers, timeout=cfg.request_timeout)
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("code") != 0:
                continue
            entry = _parse_tikwm_entry(j, username_from_url, video_id_from_url)
            if entry:
                entry["quality"] = "compressed"
                return entry
        except (requests.RequestException, ValueError):
            continue
    return None


BUSY_MARKERS = ("limit", "busy", "too many", "try again", "frequent")
last_original_failure = ""  # why the last original-quality attempt failed (for messages)


def _submit_task(candidate: str, headers: dict, cfg: AppConfig) -> tuple[Optional[str], str]:
    """Submit one Original Downloader task. When TikWM says it's busy or
    rate-limited, wait and retry the same link instead of giving up.
    Returns (task_id or None, reason)."""
    body = f"web=1&url={quote(candidate)}"
    reason = ""
    for attempt in range(5):
        try:
            r = http().post(SUBMIT_URL, data=body, headers=headers, timeout=cfg.request_timeout)
            if r.status_code in (429, 502, 503, 504):
                reason = f"TikWM busy (HTTP {r.status_code})"
                time.sleep(2 + attempt * 2)
                continue
            r.raise_for_status()
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            reason = f"couldn't reach TikWM ({e.__class__.__name__})"
            time.sleep(2 + attempt * 2)
            continue
        data = j.get("data") if isinstance(j.get("data"), dict) else {}
        if j.get("code") == 0 and data.get("task_id"):
            return str(data["task_id"]), ""
        msg = str(j.get("msg") or j.get("code"))
        if any(m in msg.lower() for m in BUSY_MARKERS):
            reason = f"TikWM busy ({msg})"
            time.sleep(2 + attempt * 2)
            continue
        return None, f"TikWM refused it ({msg})"
    return None, reason or "TikWM busy"


def submit_tikwm_original(tiktok_url: str, cfg: Optional[AppConfig] = None) -> Optional[dict]:
    """Original-quality extraction via TikWM's Original Downloader task API."""
    global last_original_failure
    cfg = cfg or load_config()
    candidates = url_candidates(tiktok_url)
    username_from_url = extract_username_from_url(tiktok_url) or "unknown"
    video_id_from_url = extract_media_id_from_url(tiktok_url)
    session_id = load_sessionid(cfg)
    api_headers = tikwm_headers_with_session(session_id)
    accepted = 0
    reason = "no response from TikWM"

    for candidate in candidates:
        # TikWM accepted the link but couldn't process it twice: other spellings
        # of the same URL won't help, so stop instead of waiting on 4 more tasks.
        if accepted >= 2:
            break
        task_id, why = _submit_task(candidate, api_headers, cfg)
        if not task_id:
            reason = why
            if why.startswith("TikWM busy"):
                break  # already waited and retried; other spellings won't help
            continue
        accepted += 1

        # Keep polling the SAME task while TikWM is still working on it - on slow
        # days preparing the original file can take minutes. Resubmitting would
        # only put the video back at the end of TikWM's queue.
        deadline = time.monotonic() + max(cfg.original_wait_sec, cfg.poll_attempts * cfg.poll_interval)
        polls = 0
        timed_out = True
        while time.monotonic() < deadline and polls < 10000:
            polls += 1
            time.sleep(cfg.poll_interval)
            try:
                poll = http().get(RESULT_BASE + str(task_id), headers=api_headers, timeout=cfg.request_timeout)
                if poll.status_code != 200:
                    continue
                j2 = poll.json()
                if j2.get("code") != 0 or not isinstance(j2.get("data"), dict):
                    continue
                data = j2["data"]
                status = data.get("status")
                if status == 2:
                    detail = data.get("detail") or data
                    # Wrap so the parser always takes the task "detail" branch
                    # (play_url = original source file), never the quick-API
                    # branch that prefers hdplay/play.
                    wrapped = {
                        "detail": detail,
                        "images": data.get("images"),
                        "author": data.get("author"),
                        "video_id": data.get("video_id"),
                        "create_time": data.get("create_time"),
                    }
                    entry = _parse_tikwm_entry({"data": wrapped}, username_from_url, video_id_from_url)
                    if entry:
                        entry["quality"] = "original"
                        last_original_failure = ""
                        return entry
                if status == 3:
                    timed_out = False
                    reason = "TikWM couldn't process it"
                    break
            except (requests.RequestException, ValueError):
                continue
        if timed_out:
            waited = int(max(cfg.original_wait_sec, cfg.poll_attempts * cfg.poll_interval))
            reason = f"TikWM still hadn't finished after {waited}s"
            break  # resubmitting a slow job won't make it faster
    last_original_failure = reason
    return None


def _mark_slideshow(entry: dict) -> dict:
    """Slideshow images from TikWM are already full resolution, so treat them as
    complete (not 'compressed'); the play_url on these posts is just the
    slideshow-with-music render, which we don't want."""
    entry["content_type"] = "photo"
    entry["quality"] = "original"
    return entry


def submit_tikwm_task(tiktok_url: str, cfg: Optional[AppConfig] = None,
                      allow_fallback: Optional[bool] = None) -> Optional[dict]:
    """Original quality for videos; images for photo slideshows.
    allow_fallback=False never settles for the compressed quick-API version
    (used by `force` and the end-of-batch retry)."""
    global _api_calls
    cfg = cfg or load_config()
    _api_calls += 1

    # Photo posts: the Original Downloader doesn't support them, so only use the
    # quick API (full-size images). If that fails, the caller tries gallery-dl.
    if content_type_from_url(tiktok_url) == "photo":
        entry = submit_tikwm_quick(tiktok_url, cfg)
        return _mark_slideshow(entry) if entry and entry.get("images") else None

    entry = submit_tikwm_original(tiktok_url, cfg)
    if entry:
        return _mark_slideshow(entry) if entry.get("images") else entry

    entry = submit_tikwm_quick(tiktok_url, cfg)
    if entry and entry.get("images"):
        # A slideshow shared as a /video/ link
        return _mark_slideshow(entry)
    if not entry:
        return None
    why = last_original_failure or "unknown reason"
    if allow_fallback is False:
        print(f"Original quality still unavailable ({why}).")
        return None
    if not cfg.allow_compressed_fallback:
        print(f"Original-quality extraction failed ({why}); compressed fallback disabled in config.")
        return None
    print(f"WARNING: Original quality failed ({why}); using TikWM quick API (compressed, lower quality).")
    if not load_sessionid(cfg):
        print("         No sessionid.txt found - TikWM's Original Downloader may need one. See sessionid.txt.example.")
    return entry
