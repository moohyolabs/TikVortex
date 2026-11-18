from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Iterable, Iterator, Optional

from .config import AppConfig, load_config
from . import download as download_mod
from .download import process_one_url
from .tikwm import api_call_count, gallery_dl_command, http, load_sessionid, tikwm_headers_with_session

PROFILE_PATTERN = re.compile(r"^@?([\w.-]+)$")
PROFILE_URL_PATTERN = re.compile(r"tiktok\.com/@([\w.-]+)/?(?:\?|$)", re.IGNORECASE)


COMMAND_WORDS = {"force", "redownload", "reorder", "fixdates", "highlights", "highlight", "help", "commands", "quit", "exit", "q"}
CREATOR_PREFIX = re.compile(r"^(?:creator:|--creator\s+)\s*(\S+)$", re.IGNORECASE)


def _single_account(text: str) -> Optional[str]:
    text = text.strip()
    m = PROFILE_PATTERN.match(text)
    if m:
        return m.group(1)
    m = PROFILE_URL_PATTERN.search(text)
    if m and "/video/" not in text and "/photo/" not in text:
        return m.group(1)
    return None


def _find_group(name: str, cfg: AppConfig) -> Optional[str]:
    for g in cfg.creators:
        if g.lower() == name.lower().lstrip("@"):
            return g
    return None


def parse_profiles(text: str, cfg: Optional[AppConfig] = None) -> Optional[tuple[list[str], Optional[str]]]:
    """Understands:
         @user                      one account
         @a @b @c  (or commas)      several accounts, merged oldest -> newest
         creator:jane / jane        a saved group from config.json "creators"
                                    (with "@jane" you get only the account)
       Returns (accounts, group_name) or None if the text isn't a profile request."""
    cfg = cfg or load_config()
    text = (text or "").strip()
    if not text:
        return None
    m = CREATOR_PREFIX.match(text)
    if m:
        g = _find_group(m.group(1), cfg)
        if not g:
            print(f'No creator group "{m.group(1)}" in config.json. Known: {", ".join(cfg.creators) or "(none)"}')
            return [], m.group(1)
        return list(cfg.creators[g]), g
    tokens = [t for t in re.split(r"[\s,]+", text) if t]
    if tokens[0].lower() in COMMAND_WORDS:
        return None  # "force", "reorder", ... are commands, never usernames
    if all(re.fullmatch(r"\d{15,20}", t) for t in tokens):
        return None  # bare post IDs, not usernames
    if len(tokens) == 1 and not tokens[0].startswith("@"):  # "@jane" = just that account
        g = _find_group(tokens[0], cfg)
        if g:
            return list(cfg.creators[g]), g
    accounts = [_single_account(t) for t in tokens]
    if accounts and all(accounts):
        return list(dict.fromkeys(accounts)), None
    return None


POST_LINE_PATTERN = re.compile(r"^(\d+) (video|image) ([\w.-]*)$")


def _post_url(username: str, post_id: str, is_photo: bool, user: str = "") -> str:
    kind = "photo" if is_photo else "video"
    return f"https://www.tiktok.com/@{user or username}/{kind}/{post_id}"


def _list_via_gallery_dl_api(username: str, cfg: AppConfig) -> Optional[list[str]]:
    """Fast path: use gallery-dl's TikTok post-list API directly. This pages
    through the profile 15 posts per request and never opens the individual
    post pages, so 100 posts take a few seconds instead of minutes.
    Returns None if this gallery-dl version doesn't support it."""
    try:
        from gallery_dl import config as gconfig
        from gallery_dl import extractor as gextractor
    except Exception:
        return None
    try:
        gconfig.load()  # respect the user's own gallery-dl config, if any
        if os.path.isfile(cfg.cookies_path):
            gconfig.set(("extractor",), "cookies", cfg.cookies_path)
        ex = gextractor.find(f"https://www.tiktok.com/@{username}/posts")
        if ex is None or not hasattr(ex, "posts"):
            return None
        ex.initialize()
        posts = ex.posts()
    except Exception as e:
        print(f"Fast profile listing unavailable ({e.__class__.__name__}: {e}); using slower method...")
        return None

    urls: list[str] = []
    seen: set[str] = set()
    items = posts.items() if isinstance(posts, dict) else ((u, None) for u in posts or [])
    for url, item in items:
        m = re.search(r"/(?:video|photo)/(\d+)", str(url))
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        author = (item or {}).get("author") or {} if isinstance(item, dict) else {}
        is_photo = isinstance(item, dict) and "imagePost" in item or "/photo/" in str(url)
        urls.append(_post_url(username, m.group(1), is_photo, author.get("uniqueId", "")))
    return urls or None


def _iter_via_gallery_dl_cli(username: str, cfg: AppConfig) -> Iterator[str]:
    """Slow fallback: gallery-dl opens every post page. Output is unbuffered so
    each post is handed over (and starts downloading) as soon as it's found."""
    posts_url = f"https://www.tiktok.com/@{username}/posts"
    base = gallery_dl_command("-u")
    if base is None:
        print("gallery-dl isn't installed, so profiles can't be listed with it (pip install gallery-dl).")
        return
    cmd = [*base, "--print", "post:{id} {post_type} {user}"]
    if os.path.isfile(cfg.cookies_path):
        cmd.extend(["--cookies", cfg.cookies_path])
    cmd.append(posts_url)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except Exception as e:
        print(f"Failed to list profile @{username}: {e}")
        return

    seen: set[str] = set()
    other: list[str] = []
    try:
        for line in proc.stdout:
            m = POST_LINE_PATTERN.match(line.strip())
            if not m:
                if line.strip():
                    other = (other + [line.strip()])[-5:]
                continue
            post_id, post_type, user = m.groups()
            if post_id in seen:
                continue
            seen.add(post_id)
            yield _post_url(username, post_id, post_type == "image", user)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    if not seen and proc.returncode not in (0, None, -9):
        print(f"gallery-dl profile list failed: {' | '.join(other)[:500]}")
        print("Tip: add cookies.txt for private or restricted profiles.")


def _post_id(url: str) -> int:
    m = re.search(r"/(?:video|photo)/(\d+)", url)
    return int(m.group(1)) if m else 0


TIKWM_USER_POSTS = "https://www.tikwm.com/api/user/posts"


def _tikwm_list_page(username: str, cursor: str, cfg: AppConfig, style: Optional[str]):
    """One page of TikWM's profile API. Tries several request styles until one
    returns JSON (TikWM can answer some styles with an HTML page instead).
    Returns (json_or_None, style_that_worked, diagnostic)."""
    params = {"unique_id": username, "count": 35, "cursor": cursor}
    full = tikwm_headers_with_session(load_sessionid(cfg))
    plain = {"User-Agent": full["User-Agent"], "Accept": "application/json, text/plain, */*"}
    styles = {
        "post": lambda: http().post(TIKWM_USER_POSTS, data=params, headers=full, timeout=cfg.request_timeout),
        "get": lambda: http().get(TIKWM_USER_POSTS, params=params, headers=full, timeout=cfg.request_timeout),
        "get-plain": lambda: http().get(TIKWM_USER_POSTS, params=params, headers=plain, timeout=cfg.request_timeout),
    }
    order = [style] if style else list(styles)
    diag = ""
    for name in order:
        for attempt in range(3):
            try:
                r = styles[name]()
            except Exception as e:
                diag = f"{name}: {e.__class__.__name__}"
                break
            try:
                j = r.json()
            except ValueError:
                body = (r.text or "")[:100].replace("\n", " ")
                diag = f"{name}: HTTP {r.status_code}, {r.headers.get('Content-Type', '?')}, starts {body!r}"
                break
            if j.get("code") == 0:
                return j, name, ""
            msg = str(j.get("msg", ""))
            if "limit" in msg.lower():  # "Free Api Limit: 1 request/second"
                time.sleep(1.5)
                continue
            return j, name, f"{name}: {msg}"  # a real answer (e.g. user not found) - don't try other styles
    return None, None, diag


def _list_via_tikwm(username: str, cfg: AppConfig) -> Optional[list[str]]:
    """Primary listing method: TikWM's profile API (35 posts per page, about one
    request a second). Doesn't touch tiktok.com, so TikTok's bot checks on
    profile pages don't affect it. Returns None if TikWM can't list this profile."""
    urls: list[str] = []
    seen: set[str] = set()
    cursor, style = "0", None
    for page in range(300):  # safety cap: ~10,000 posts
        j, used, diag = _tikwm_list_page(username, cursor, cfg, style)
        if not j or j.get("code") != 0:
            if not urls:
                print(f"TikWM couldn't list @{username} ({diag or 'no response'}); trying yt-dlp...")
                return None
            print(f"TikWM stopped listing after {len(urls)} post(s) ({diag}) - continuing with those.")
            break
        style = used  # keep using the request style that worked
        data = j.get("data") or {}
        for v in data.get("videos") or []:
            pid = str(v.get("video_id") or v.get("aweme_id") or "")
            if not pid.isdigit() or pid in seen:
                continue
            seen.add(pid)
            user = ((v.get("author") or {}).get("unique_id")) or username
            urls.append(_post_url(username, pid, bool(v.get("images")), user))
        if not data.get("hasMore") or not data.get("cursor") or str(data.get("cursor")) == cursor:
            break
        cursor = str(data["cursor"])
        if page and page % 5 == 0:
            print(f"  ...listed {len(urls)} post(s) so far")
        time.sleep(1.1)  # TikWM's free API allows about one request per second
    return urls or None


class _QuietYtdlpLog:
    """yt-dlp logger that keeps its chatter out of the console (errors are reported by the caller)."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _ytdlp_impersonate():
    """yt-dlp's Chrome impersonation target when curl_cffi is installed, else None."""
    try:
        import curl_cffi  # noqa: F401
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str("chrome")
    except Exception:
        return None


def _tikwm_sec_uid(username: str, cfg: AppConfig) -> Optional[str]:
    """The profile's secUid from TikWM's user-info API (None if unavailable)."""
    try:
        r = http().get("https://www.tikwm.com/api/user/info", params={"unique_id": username},
                       headers=tikwm_headers_with_session(load_sessionid(cfg)), timeout=cfg.request_timeout)
        user = ((r.json() or {}).get("data") or {}).get("user") or {}
        return user.get("secUid") or None
    except Exception:
        return None


LAST_YTDLP_ERROR = [""]          # why yt-dlp last failed (shown on the watchlist)


SEC_UIDS: dict = {}            # username -> secUid, found once and reused (the watchlist keeps them too)


def _sec_uid_from_post(post_id: str, username: str, opts: dict) -> Optional[str]:
    """yt-dlp reads a single post fine even when TikTok blocks the profile page; the post's details include
    its creator's secUid (yt-dlp's 'channel_id'), which is what yt-dlp needs to list the profile."""
    try:
        import yt_dlp
        o = dict(opts); o.pop("extract_flat", None); o.pop("playlistend", None)
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(f"https://www.tiktok.com/@{username}/video/{post_id}", download=False, process=False)
        sec = (info or {}).get("channel_id") or ""
        return sec if sec.startswith("MS4") else None
    except Exception:
        return None


def _list_via_ytdlp(username: str, cfg: AppConfig, limit: Optional[int] = None,
                    known_posts: Optional[list] = None) -> Optional[list[str]]:
    """Second listing method: yt-dlp's TikTok profile extractor (bundled with the app).
    Uses cookies.txt when present, for restricted profiles. Returns None if it can't list."""
    try:
        import yt_dlp
    except ImportError:
        print("yt-dlp isn't installed (pip install yt-dlp).")
        return None
    print(f"Listing @{username}'s posts via yt-dlp...")
    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True, "skip_download": True,
            "noprogress": True, "logger": _QuietYtdlpLog(), "socket_timeout": cfg.request_timeout}
    if limit:
        opts["playlistend"] = limit                 # newest first: enough for the watchlist
    if os.path.isfile(cfg.cookies_path):
        opts["cookiefile"] = cfg.cookies_path
    imp = _ytdlp_impersonate()
    if imp is not None:                       # look like a real Chrome browser (needs curl_cffi)
        opts["impersonate"] = imp

    def run(target: str):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(target, download=False)
        except Exception as e:
            if "impersonat" in str(e).lower() and opts.pop("impersonate", None) is not None:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(target, download=False)
            raise
    try:
        info = run(f"https://www.tiktok.com/@{username}")
    except Exception as e:
        # yt-dlp needs the profile's secUid; when it can't read it from TikTok's profile page, try (1) one we
        # found before, (2) TikWM's user info, (3) the details of any post we already have from this creator.
        sec = None
        if "user ID" in str(e) or "secUid" in str(e) or "403" in str(e):
            sec = SEC_UIDS.get(username.lower()) or _tikwm_sec_uid(username, cfg)
            for pid in (known_posts or [])[:3]:
                if sec:
                    break
                sec = _sec_uid_from_post(str(pid), username, opts)
            if sec:
                SEC_UIDS[username.lower()] = sec
        info = None
        if sec:
            try:
                info = run(f"tiktokuser:{sec}")
            except Exception as e2:
                e = e2
        if info is None:
            first = (str(e).strip().splitlines() or ["unknown error"])[0]
            LAST_YTDLP_ERROR[0] = first
            print(f"yt-dlp couldn't list @{username}: {first[:240]}")
            return None
    sid = str((info or {}).get("id") or "")
    if sid.startswith("MS4wLjABAAAA"):             # the profile's secUid: kept so the next listing doesn't need it found again
        SEC_UIDS[username.lower()] = sid
    urls: list[str] = []
    seen: set[str] = set()
    for entry in (info or {}).get("entries") or []:
        entry = entry or {}
        pid = str(entry.get("id") or "")
        if not pid.isdigit() or pid in seen:
            continue
        seen.add(pid)
        link = str(entry.get("url") or entry.get("webpage_url") or "")
        user = entry.get("uploader") or entry.get("channel") or username
        urls.append(_post_url(username, pid, "/photo/" in link, user))
    if not urls:
        print(f"yt-dlp found no posts for @{username}.")
    return urls or None


def iter_profile_urls(username: str, cfg: Optional[AppConfig] = None, known_posts: Optional[list] = None) -> Iterator[str]:
    """Yield a profile's post URLs. With profile_order "oldest" (default) they are
    sorted oldest -> newest. TikTok post IDs start with the post's timestamp, so
    sorting by ID is chronological (and also moves pinned posts to their real place)."""
    cfg = cfg or load_config()
    oldest_first = cfg.profile_order == "oldest"
    print(f"Listing @{username}'s posts via TikWM...")
    # TikWM first, then yt-dlp; then gallery-dl if it's switched on.
    urls = _list_via_tikwm(username, cfg) or _list_via_ytdlp(username, cfg, known_posts=known_posts)
    if not urls and cfg.gallery_dl_fallback:
        print("Trying gallery-dl...")
        urls = _list_via_gallery_dl_api(username, cfg)
    if urls:
        print(f"Found {len(urls)} post(s) for @{username}" + (" - downloading oldest first" if oldest_first else ""))
        yield from (sorted(urls, key=_post_id) if oldest_first else urls)
        return
    if not cfg.gallery_dl_fallback:
        print(f"Couldn't list @{username}. Paste the post links instead, or add cookies.txt for restricted profiles.")
        return
    if not oldest_first:
        yield from _iter_via_gallery_dl_cli(username, cfg)
        return
    print("Listing every post first so they can be downloaded oldest first (slow method)...")
    urls = sorted(_iter_via_gallery_dl_cli(username, cfg), key=_post_id)
    if urls:
        print(f"Found {len(urls)} post(s) for @{username} - downloading oldest first")
    yield from urls


def process_many(
    urls: Iterable[str],
    cache: dict,
    output_dir: str,
    cfg: Optional[AppConfig] = None,
    progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    folder_name: Optional[str] = None,
) -> tuple[int, int]:
    """Process many post URLs. TikWM extraction stays one-at-a-time (rate limits);
    file downloads run in the background on `download_workers` threads.
    The batch delay is only applied after posts that actually called TikWM."""
    cfg = cfg or load_config()
    workers = cfg.download_workers
    ok = total = 0
    futures: list[Future] = []
    last_hit_api = False
    fallback_mark = len(download_mod.compressed_this_run)
    ex = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for url in urls:
            total += 1
            if last_hit_api:
                time.sleep(cfg.batch_delay_sec)
            before = api_call_count()
            try:
                res = process_one_url(url, cache, output_dir, cfg, progress_callback, executor=ex, folder_name=folder_name)
            except Exception as e:
                print(f"Error on {url}: {e}")
                res = False
            last_hit_api = api_call_count() != before
            if isinstance(res, Future):
                futures.append(res)
            elif res:
                ok += 1
        for f in futures:
            try:
                ok += 1 if f.result() else 0
            except Exception as e:
                print(f"Download error: {e}")
    finally:
        if ex is not None:
            ex.shutdown(wait=True)
    retry_compressed(download_mod.compressed_this_run[fallback_mark:], cache, output_dir, cfg)
    return ok, total


def retry_compressed(items, cache: dict, output_dir: str, cfg: AppConfig) -> None:
    """Videos that fell back to compressed quality during a batch get one more
    original-quality attempt at the end (TikWM has often caught up by then).
    A success replaces the compressed file in place and keeps its date."""
    items = list(dict.fromkeys(items))
    if not items:
        return
    still = []
    if cfg.retry_compressed_at_end:
        print(f"\nRetrying {len(items)} video(s) that fell back to compressed quality...")
        time.sleep(5)
        for vid, url in items:
            try:
                ok = process_one_url(url, cache, output_dir, cfg, force=True)
            except Exception as e:
                print(f"Error on {vid}: {e}")
                ok = False
            got = (cache.get(vid) or {}).get("quality") == "original"
            if not (ok and got):
                still.append(vid)
            time.sleep(cfg.batch_delay_sec)
    else:
        still = [vid for vid, _ in items]
    if still:
        print(f"\n{len(still)} video(s) are still in compressed quality. When TikWM is less busy, paste:")
        print("force " + " ".join(still))
    else:
        print("All of them are now in original quality.")


def reorder_targets(text: str, cfg: Optional[AppConfig] = None) -> Optional[list[str]]:
    """Folder names to re-order together for `--reorder` (None = not understood)."""
    cfg = cfg or load_config()
    parsed = parse_profiles(text, cfg)
    if not parsed or not parsed[0]:
        return None
    accounts, group = parsed
    folders = list(accounts)
    if group:
        folders.insert(0, group)
    return folders
