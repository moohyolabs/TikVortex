from __future__ import annotations

import os
import re
import warnings
from urllib.parse import urlparse
import subprocess
import time
from datetime import datetime, timezone
import requests
import urllib3
from concurrent.futures import Executor, Future
from typing import Callable, Optional, Union

from .cache import save_cache
from .config import AppConfig, load_config
from .media_dates import apply_date, apply_dates, split_media_name, stamp_for
from .tikwm import (
    TIKWM_HEADERS,
    gallery_dl_command,
    content_type_from_url,
    extract_media_id_from_url,
    extract_username_from_url,
    http,
    resolve_tiktok_redirect,
    sanitize_filename,
    submit_tikwm_task,
)

ProgressCallback = Optional[Callable[[int, Optional[int]], None]]


def build_date_str(create_time: Optional[int], fmt: str = "%y-%m-%d") -> str:
    if create_time is not None:
        try:
            return datetime.fromtimestamp(int(create_time), tz=timezone.utc).strftime(fmt)
        except (ValueError, OSError):
            pass
    return datetime.now(timezone.utc).strftime(fmt)


def build_filename(entry: dict, ext: str = ".mp4", cfg: Optional[AppConfig] = None) -> str:
    cfg = cfg or load_config()
    username = sanitize_filename(entry.get("username") or "unknown")
    date_str = build_date_str(entry.get("create_time"), getattr(cfg, "date_format", "%y-%m-%d") or "%y-%m-%d")
    profile_uid = entry.get("profile_uid") or "unknown"
    if isinstance(profile_uid, (int, float)):
        profile_uid = str(int(profile_uid))
    video_id = entry.get("video_id") or "unknown"
    template = cfg.naming_template
    return template.format(
        date=date_str,
        profile_uid=profile_uid,
        username=username,
        video_id=video_id,
        ext=ext,
    )


def subfolder_for_content_type(content_type: str) -> str:
    if content_type == "story":
        return "story"
    if content_type == "highlight":
        return "highlight"
    return ""


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def gallery_dl_photo_fallback(
    tiktok_url: str, output_dir: str, username: str, cfg: Optional[AppConfig] = None
) -> list[str]:
    """Download a photo post with gallery-dl. Returns the image paths saved (or
    already present), empty list on failure."""
    cfg = cfg or load_config()
    folder = os.path.join(output_dir, sanitize_filename(username or "unknown"), "photo")
    os.makedirs(folder, exist_ok=True)
    cookies = cfg.cookies_path
    # No --no-skip: files that already exist are skipped, not re-downloaded
    base = gallery_dl_command()
    if base is None:
        print("gallery-dl isn't installed, so this slideshow can't use the fallback (pip install gallery-dl).")
        return []
    cmd = [*base, "-D", folder]
    if os.path.isfile(cookies):
        cmd.extend(["--cookies", cookies])
    cmd.append(tiktok_url)
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception:
        return []
    if run.returncode != 0:
        err = (run.stderr or run.stdout or "").strip()
        if err:
            print(f"gallery-dl failed: {err[:500]}")
        return []
    # gallery-dl prints one path per file when piped; skipped files start with "# "
    paths = []
    for line in (run.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("# "):
            line = line[2:]
        if line.lower().endswith(IMAGE_EXTS):
            paths.append(line)
    if paths:
        print(f"Saved {len(paths)} image(s) with gallery-dl -> {folder}")
    return paths


EXPIRED_CERT_MARKERS = ("certificate has expired", "not within its validity period")
_expired_cert_warned = False


def _may_skip_expired_cert(url: str, err: Exception, cfg: AppConfig) -> bool:
    global _expired_cert_warned
    host = (urlparse(url).hostname or "").lower()
    if not (host == "tokcdn.com" or host.endswith(".tokcdn.com")):
        return False
    if not any(m in str(err).lower() for m in EXPIRED_CERT_MARKERS):
        return False  # a wrong/fake certificate is never accepted
    if not cfg.accept_expired_tokcdn_cert:
        if not _expired_cert_warned:
            _expired_cert_warned = True
            print("TikWM's file server (tokcdn.com) has an EXPIRED certificate, so downloads are refused.\n"
                  "  Wait for TikWM to renew it, or set \"accept_expired_tokcdn_cert\": true in config.json\n"
                  "  to accept it temporarily (like clicking \"Proceed\" in the browser).")
        return False
    if not _expired_cert_warned:
        _expired_cert_warned = True
        print("WARNING: accepting TikWM's expired file-server certificate (accept_expired_tokcdn_cert is on).\n"
              "  Switch it off in config.json once TikWM renews it - check with: python cert_check.py")
    return True


def download_to_file(
    url: str,
    filepath: str,
    headers: Optional[dict] = None,
    cfg: Optional[AppConfig] = None,
    progress_callback: ProgressCallback = None,
) -> bool:
    cfg = cfg or load_config()
    h = headers or TIKWM_HEADERS
    part = filepath + ".part"
    try:
        try:
            r = http().get(url, headers=h, timeout=cfg.request_timeout, stream=True)
        except requests.exceptions.SSLError as e:
            if not _may_skip_expired_cert(url, e, cfg):
                raise
            # Opt-in workaround (config "accept_expired_tokcdn_cert"): TikWM's
            # file server certificate has expired. Only for *.tokcdn.com, only
            # for an EXPIRED certificate - the equivalent of clicking "Proceed"
            # in the browser. TikWM's API and every other site stay checked.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                r = http().get(url, headers=h, timeout=cfg.request_timeout, stream=True, verify=False)
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0)) or None
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        downloaded = 0
        limit = max(0, int(getattr(cfg, "speed_limit_kbps", 0) or 0)) * 1024  # bytes/s, 0 = unlimited
        started = time.monotonic()
        with open(part, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024 if limit else 1024 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
                    if limit:
                        ahead = downloaded / limit - (time.monotonic() - started)
                        if ahead > 0:
                            time.sleep(ahead)
        if downloaded == 0 or (total and downloaded < total):
            raise IOError(f"incomplete download ({downloaded} of {total or '?'} bytes)")
        os.replace(part, filepath)
        return True
    except Exception as e:
        print(f"Download failed for {os.path.basename(filepath)}: {e}")
        try:
            os.remove(part)
        except OSError:
            pass
        return False


def do_download(
    entry: dict,
    output_dir: str,
    cfg: Optional[AppConfig] = None,
    progress_callback: ProgressCallback = None,
) -> bool:
    cfg = cfg or load_config()
    username = entry.get("username") or "unknown"
    video_id = entry.get("video_id") or "unknown"
    play_url = entry.get("play_url")
    images = entry.get("images") or []
    content_type = entry.get("content_type") or "video"
    sub = subfolder_for_content_type(content_type)
    folder_name = sanitize_filename(entry.get("_folder") or username)  # creator-group folder, if any
    if getattr(cfg, "folder_layout", "group") == "single":
        folder = os.path.join(output_dir, sub) if sub else output_dir   # everything in one folder
    else:
        folder = os.path.join(output_dir, folder_name, sub) if sub else os.path.join(output_dir, folder_name)
    os.makedirs(folder, exist_ok=True)

    if images and (not play_url or content_type == "photo"):
        ok = 0
        existing = 0
        base_name = build_filename(entry, "", cfg) or f"{username}_{video_id}"
        for i, item in enumerate(images):
            img_url = item if isinstance(item, str) else (item.get("url") or item.get("image_url") or "")
            if not img_url:
                continue
            ext = ".jpg"
            if "." in img_url.split("?")[0]:
                ext = "." + img_url.split("?")[0].rsplit(".", 1)[-1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"
            filepath = os.path.join(folder, f"{base_name}_img_{i + 1}{ext}")
            if os.path.exists(filepath):
                ok += 1
                existing += 1
                continue
            if download_to_file(img_url, filepath, progress_callback=progress_callback, cfg=cfg):
                ok += 1
                if entry.get("_stamp") is not None:
                    apply_date(filepath, entry["_stamp"] + i)
        if ok - existing:
            print(f"Downloaded {ok - existing} image(s) -> {folder}")
        elif ok:
            print(f"Already exists: {ok} image(s) in {folder}")
        if ok > 0:
            save_meta(entry, cfg)
        return ok > 0

    if not play_url:
        return False
    if play_url.startswith("//"):
        play_url = "https:" + play_url
    fname = build_filename(entry, ".mp4", cfg)
    filepath = os.path.join(folder, fname)
    if os.path.exists(filepath):
        print(f"Already exists: {filepath}")
        return True
    h = {**TIKWM_HEADERS, "Referer": "https://www.tiktok.com/", "Origin": "https://www.tiktok.com"}
    if download_to_file(play_url, filepath, h, cfg, progress_callback):
        apply_date(filepath, entry.get("_stamp"))
        save_meta(entry, cfg)
        print(f"Downloaded: {filepath}")
        return True
    return False


MEDIA_EXTS = (".mp4", ".mov", ".m4v") + IMAGE_EXTS


def meta_dir(cfg: AppConfig) -> str:
    """Where per-post details and profile-picture links are kept (next to the app's cache)."""
    return os.path.join(os.path.dirname(os.path.abspath(cfg.cache_path)), ".tikvortex", "meta")


def save_meta(entry: dict, cfg: AppConfig) -> None:
    """Remember a downloaded post's details (counts, sound, author) for the app's Info panel,
    and the author's profile-picture link."""
    import json
    meta = dict(entry.get("meta") or {})
    vid = str(entry.get("video_id") or "")
    if not vid or not meta:
        return
    meta.update({"username": entry.get("username") or "", "title": entry.get("title") or "",
                 "create_time": entry.get("create_time"), "downloaded_at": int(time.time())})
    try:
        d = meta_dir(cfg); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{vid}.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        user, avatar = (entry.get("username") or "").lower(), meta.get("avatar") or ""
        if user and avatar:
            idx_path = os.path.join(d, "avatars.json")
            try:
                with open(idx_path, encoding="utf-8") as f:
                    idx = json.load(f)
            except (OSError, ValueError):
                idx = {}
            if idx.get(user) != avatar:
                idx[user] = avatar
                with open(idx_path, "w", encoding="utf-8") as f:
                    json.dump(idx, f)
    except OSError:
        pass


def post_id_of(filename: str) -> Optional[str]:
    """The post ID in a saved file's name. Must not match the account's user ID,
    which the default filename also contains."""
    return split_media_name(filename)[0]


def find_existing_download(output_dir: str, usernames, video_id: str) -> Optional[str]:
    """Return a file already saved for this post (in any of the given folders), if any."""
    if not video_id or video_id == "unknown":
        return None
    if isinstance(usernames, str):
        usernames = [usernames]
    for username in usernames:
        if not username or username == "unknown":
            continue
        base = os.path.join(output_dir, sanitize_filename(username))
        for folder in (base, os.path.join(base, "photo"), os.path.join(base, "story"), os.path.join(base, "highlight")):
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                if name.lower().endswith(MEDIA_EXTS) and post_id_of(name) == video_id:
                    return os.path.join(folder, name)
    return None


def process_one_url(
    tiktok_url: str,
    cache: dict,
    output_dir: str,
    cfg: Optional[AppConfig] = None,
    progress_callback: ProgressCallback = None,
    executor: Optional[Executor] = None,
    folder_name: Optional[str] = None,
    force: bool = False,
    item_progress: ProgressCallback = None,
) -> Union[bool, Future]:
    """Extract (or use cache) then download. With an executor, the download runs in
    the background and a Future[bool] is returned so the next post can start.
    item_progress: progress callback for this post's background download (the GUI
    queue uses it; progress_callback is only used for foreground downloads).
    force=True re-downloads even if the post is already saved or cached.
    A bare post ID ("7689799376637676820") works in place of a link."""
    tiktok_url = normalize_post_input(tiktok_url)
    if not force:
        return _process_one_url(tiktok_url, cache, output_dir, cfg, progress_callback, executor, folder_name,
                                item_progress=item_progress)
    vid = extract_media_id_from_url(tiktok_url.strip()) or ""
    before = set(find_all_downloads(output_dir, vid))
    try:
        ok = _process_one_url(tiktok_url, cache, output_dir, cfg, item_progress or progress_callback, None, folder_name, True)
    except Exception as e:
        print(f"Error: {e}")
        ok = False
    olds = sorted(before)  # original paths (set aside as "<name>.old" during the download)
    if ok and olds:
        # Put the new file(s) exactly where the old ones were
        new = sorted(set(find_all_downloads(output_dir, vid)) - before)
        if len(new) == len(olds):
            for n_path, o_path in zip(new, olds):
                if os.path.normcase(n_path) != os.path.normcase(o_path):
                    os.replace(n_path, o_path)
        else:
            dest = os.path.dirname(olds[0])
            for n_path in new:
                if os.path.dirname(n_path) != dest:
                    os.replace(n_path, os.path.join(dest, os.path.basename(n_path)))
    for o_path in olds:
        old = o_path + ".old"
        if not os.path.exists(old):
            continue
        if ok:
            os.remove(old)
        elif not os.path.exists(o_path):
            os.replace(old, o_path)
            print(f"Force re-download failed - kept the original: {os.path.basename(o_path)}")
    return ok


def normalize_post_input(text: str) -> str:
    """A bare post ID becomes a link; anything else is returned unchanged."""
    t = (text or "").strip()
    return f"https://www.tiktok.com/video/{t}" if re.fullmatch(r"\d{15,20}", t) else t


# Videos saved in compressed quality because original quality failed (this run).
compressed_this_run: list[tuple[str, str]] = []


def find_all_downloads(output_dir: str, video_id: str) -> list[str]:
    """Every saved file for this post, in any folder (used by force re-download)."""
    found = []
    if not video_id or video_id == "unknown" or not os.path.isdir(output_dir):
        return found
    for root, _dirs, names in os.walk(output_dir):
        for name in names:
            if name.lower().endswith(MEDIA_EXTS) and post_id_of(name) == video_id:
                found.append(os.path.join(root, name))
    return found


def _process_one_url(
    tiktok_url: str,
    cache: dict,
    output_dir: str,
    cfg: Optional[AppConfig] = None,
    progress_callback: ProgressCallback = None,
    executor: Optional[Executor] = None,
    folder_name: Optional[str] = None,
    force: bool = False,
    item_progress: ProgressCallback = None,
) -> Union[bool, Future]:
    """Extract (or use cache) then download. With an executor, the download runs in
    the background and a Future[bool] is returned so the next post can start."""
    cfg = cfg or load_config()

    keep_stamp: Optional[int] = None  # force re-download keeps the old file's date

    def _download(ent: dict, from_cache: bool = False) -> Union[bool, Future]:
        ent.setdefault("_folder", folder_name)
        is_slideshow = bool(ent.get("images")) and (not ent.get("play_url") or ent.get("content_type") == "photo")
        ent["_stamp"] = keep_stamp if keep_stamp is not None else stamp_for(
            ent, cfg.media_dates, len(ent["images"]) if is_slideshow else 1)
        if executor is not None:
            return executor.submit(_download_job, ent, from_cache, item_progress)
        return _download_job(ent, from_cache, progress_callback)

    def _download_job(ent: dict, from_cache: bool, cb) -> bool:
        if do_download(ent, output_dir, cfg, cb):
            return True
        if not from_cache:
            return False
        # Cached CDN links expire - fetch a fresh one once and retry
        print(f"Cached link for {ent.get('video_id')} failed (probably expired) - fetching a fresh one...")
        cache.pop(video_id, None)  # don't reuse the stale link next time
        fresh = submit_tikwm_task(tiktok_url, cfg)
        if not fresh:
            return False
        retry = {**ent, **{k: fresh.get(k) for k in ("play_url", "images", "quality") if fresh.get(k) is not None}}
        return do_download(retry, output_dir, cfg, cb)

    tiktok_url = tiktok_url.strip()
    if "tiktok.com" not in tiktok_url and "douyin.com" not in tiktok_url:
        print(f"Skipped (not TikTok): {tiktok_url[:60]}...")
        return False

    tiktok_url = resolve_tiktok_redirect(tiktok_url, cfg)
    video_id = extract_media_id_from_url(tiktok_url) or "unknown"
    username_from_url = extract_username_from_url(tiktok_url) or "unknown"
    content_type = content_type_from_url(tiktok_url)

    # Folders this post may already be saved in: the creator-group folder (if
    # any) and the account's own folder.
    folders = [n for n in dict.fromkeys([folder_name, username_from_url]) if n]
    old_files: list[str] = []
    if force:
        # Re-download from scratch: set the old file(s) aside (restored if this
        # fails), ignore the cache, and give the new file the old file's date.
        old_files = find_all_downloads(output_dir, video_id)
        if old_files:
            keep_stamp = int(min(os.path.getmtime(f) for f in old_files))
            for f in old_files:
                os.replace(f, f + ".old")
            print(f"Force: re-downloading {video_id} (replacing {len(old_files)} file(s), keeping their date)")
        cache.pop(video_id, None)
    cached = cache.get(video_id) or {}
    if not force and not cached.get("images"):
        # Videos (and gallery-dl slideshows): skip if already on disk anywhere.
        # Cached TikWM slideshows go through do_download, which resumes missing images.
        # With only a post ID (no username in the link), search every folder.
        known = [f for f in folders if f and f != "unknown"]
        single = getattr(cfg, "folder_layout", "group") == "single"
        found_any = find_all_downloads(output_dir, video_id) if (not known or single) else []
        existing = found_any[0] if found_any else find_existing_download(output_dir, folders, video_id)
        if existing:
            print(f"Already downloaded: {existing}")
            return True

    if video_id in cache:
        ent = dict(cache[video_id])
        if folder_name and ent.get("images") and find_existing_download(output_dir, [username_from_url], video_id):
            ent["_folder"] = username_from_url  # finish it where it was started
        ent.setdefault("video_id", video_id)
        ent.setdefault("username", username_from_url)
        ent.setdefault("content_type", content_type)
        ent.setdefault("create_time", None)
        ent.setdefault("profile_uid", "unknown")
        if ent.get("source") == "gallery-dl":
            # Slideshow previously saved by the gallery-dl fallback
            if ent.get("local_files") and all(os.path.isfile(f) for f in ent["local_files"]):
                print(f"[Cache] Slideshow {video_id} already downloaded")
                return True
            print(f"[Cache] Slideshow {video_id} files missing - downloading again")
        elif ent.get("images"):
            # Slideshow: TikWM images are full size, always usable from cache
            ent["content_type"] = "photo"
            print(f"[Cache] Using cached slideshow for {video_id}")
            return _download(ent, from_cache=True)
        elif ent.get("play_url") and ent.get("quality") == "original":
            print(f"[Cache] Using cached link for {video_id}")
            return _download(ent, from_cache=True)
        elif ent.get("play_url"):
            # Video links cached before this fix (or from the quick API) are
            # compressed - re-extract them so we get the original file.
            print(f"[Cache] Cached link for {video_id} is not original quality - re-extracting")


    print(f"Extracting link for {video_id}...")
    # `force` (and the end-of-batch retry) never settles for the compressed copy:
    # if original quality still isn't available, the existing file is kept.
    result = submit_tikwm_task(tiktok_url, cfg, allow_fallback=False if force else None)
    if not result:
        if content_type == "photo":
            print(f"TikWM extraction failed for photo/slideshow {video_id}, trying gallery-dl fallback...")
            files = gallery_dl_photo_fallback(tiktok_url, output_dir, folder_name or username_from_url, cfg)
            if not files:
                return False
            apply_dates(sorted(files), stamp_for({}, cfg.media_dates, len(files)))
            cache[video_id] = {
                "username": username_from_url,
                "video_id": video_id,
                "images": [],
                "local_files": files,
                "source": "gallery-dl",
                "content_type": "photo",
                "quality": "original",
            }
            save_cache(cache, cfg.cache_path)
            return True
        print(f"Extraction failed for {video_id}")
        return False

    if not folder_name and username_from_url == "unknown" and cfg.creator_folders:
        # Only a post ID was given: if the creator turns out to be in a creator
        # group, save into the group's folder like their other downloads.
        who = (result.get("username") or "").lower()
        folder_name = next((g for g, accs in cfg.creators.items() if who in (a.lower() for a in accs)), None)

    cache[video_id] = {
        "username": result.get("username") or username_from_url,
        "play_url": result.get("play_url"),
        "video_id": result.get("video_id") or video_id,
        "images": result.get("images") or [],
        "create_time": result.get("create_time"),
        "profile_uid": result.get("profile_uid") or "unknown",
        "content_type": result.get("content_type") or content_type,
        "quality": result.get("quality") or "unknown",
        "title": result.get("title") or "",
        "cover": result.get("cover") or "",
    }
    if result.get("quality") != "original" and not result.get("images"):
        compressed_this_run.append((video_id, tiktok_url))
        print(f"Note: {video_id} saved in compressed quality for now "
              f"(type: force {video_id}  later to retry in original quality).")
    save_cache(cache, cfg.cache_path)
    return _download(dict(cache[video_id]))
