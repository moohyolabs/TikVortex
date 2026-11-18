"""TikTok highlights: list them via a third-party viewer, preview, pick, and
download each one in ORIGINAL quality through the normal TikWM route.

The TikTok website has no highlights, and the app can't copy their links, so the
list (with each highlight's post ID) comes from omar-thing.site's story viewer.
Only the list comes from there - the files themselves come from TikWM's
Original Downloader, like any other post.
"""
from __future__ import annotations

import base64
from datetime import datetime
import html
import json
import os
import random
import re
import time
import webbrowser
from typing import Optional

import requests

from .config import AppConfig, load_config
from .download import find_all_downloads

VIEWER_SITE = "https://omar-thing.site/tiktok_story_viewer"
VIEWER_API = "https://dev.omar-thing.site/api/site"
_Q_CONST = "xK9#mQ2$vL7@nR4&jW8*pT6!cF3"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/141.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Origin": "https://omar-thing.site",
    "Referer": "https://omar-thing.site/",
}


class HighlightError(Exception):
    pass


def _q_byte(b: int, i: int, seed: int, back: bool) -> int:
    s = ((seed + i) % 7) + 1
    k = ord(_Q_CONST[i % len(_Q_CONST)]) ^ 0xAA
    x = (i * 37 + 13) & 0xFF
    if back:
        b = ((b - (seed % 97)) & 0xFF) ^ k
        return (((b >> s) | (b << (8 - s))) & 0xFF) ^ x
    b = b ^ x
    b = (((b << s) | (b >> (8 - s))) & 0xFF) ^ k
    return (b + (seed % 97)) & 0xFF


def _q_seal(value: str, seal_id: int) -> str:
    raw = value.encode("utf-8")
    seed = seal_id % 10000
    enc = bytes(_q_byte(b, i, seed, False) for i, b in enumerate(raw))
    return base64.b64encode(enc).decode("ascii").replace("+", "-").replace("/", "_")


def _q_open(sealed: str, resp_id: int) -> str:
    b64 = sealed.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    raw = base64.b64decode(b64)
    seed = resp_id % 10000
    dec = bytes(_q_byte(b, i, seed, True) for i, b in enumerate(raw))
    return dec.decode("utf-8")


def fetch_highlights(username: str, cfg: Optional[AppConfig] = None) -> list[dict]:
    """Returns one dict per highlight story, in the viewer's collection order."""
    cfg = cfg or load_config()
    username = username.strip().lstrip("@")
    timeout = max(cfg.request_timeout, 60)  # the viewer can take a while on big profiles

    seal_id = random.randint(100000, 999999)
    raw_payload = json.dumps({"t": int(time.time() * 1000), "username": username}, separators=(",", ":"))
    sealed_payload = _q_seal(raw_payload, seal_id)

    try:
        r = requests.post(
            f"{VIEWER_API}/highlights",
            json={"sealed": sealed_payload, "seal_id": seal_id},
            headers=HEADERS,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise HighlightError(f"couldn't reach the viewer site ({e.__class__.__name__})") from e

    if r.status_code == 404:
        raise HighlightError(f"@{username} not found")
    if r.status_code == 429:
        raise HighlightError("the viewer site is rate-limiting - wait a minute and try again")
    if r.status_code != 200:
        raise HighlightError(f"viewer site error (HTTP {r.status_code})")

    try:
        envelope = r.json()
        data = json.loads(_q_open(envelope["s"], int(envelope["id"])))
    except (ValueError, KeyError, TypeError) as e:
        raise HighlightError("the viewer site's response changed - this feature may need updating") from e

    items = []
    for col in data.get("collections") or []:
        for st in col.get("stories") or []:
            pid = str(st.get("aweme_id") or "").strip()
            if not re.fullmatch(r"\d{15,20}", pid):
                continue
            images = st.get("image_urls") or []
            created = st.get("create_time_formatted") or ""
            if not created and st.get("create_time"):
                try:
                    created = datetime.fromtimestamp(int(st["create_time"])).strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError, OSError):
                    created = ""
            items.append({
                "collection": (col.get("title") or "").strip() or "(untitled)",
                "post_id": pid,
                "created": created,
                "desc": (st.get("desc") or "").strip(),
                "is_photo": bool(images),
                "images": len(images),
                "cover": st.get("cover_url") or (images[0] if images else ""),
                "video": st.get("video_url") or "",
            })
    return items


def post_url(username: str, item: dict) -> str:
    return f"https://www.tiktok.com/@{username}/{'photo' if item['is_photo'] else 'video'}/{item['post_id']}"


def write_preview(username: str, items: list[dict], saved: set, path: str) -> str:
    """A local page with each highlight's cover and number, grouped by collection."""
    cards, last_col = [], None
    for i, it in enumerate(items, 1):
        if it["collection"] != last_col:
            if last_col is not None:
                cards.append("</div>")
            cards.append(f'<h2>{html.escape(it["collection"])}</h2><div class="grid">')
            last_col = it["collection"]
        kind = f'{it["images"]} photos' if it["is_photo"] else "video"
        media = (f'<video src="{html.escape(it["video"])}" poster="{html.escape(it["cover"])}" preload="none" controls '
                 f'playsinline referrerpolicy="no-referrer"></video>' if it["video"] else
                 f'<img src="{html.escape(it["cover"])}" referrerpolicy="no-referrer" alt="">')
        cards.append(
            f'<div class="card{" saved" if it["post_id"] in saved else ""}"><div class="n">{i}</div>{media}'
            f'<div class="meta"><b>{html.escape(it["created"])}</b> · {kind}'
            f'{" · <span class=s>saved</span>" if it["post_id"] in saved else ""}'
            f'<p>{html.escape(it["desc"][:140])}</p></div></div>')
    if last_col is not None:
        cards.append("</div>")
    page = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Highlights @{html.escape(username)}</title>
<style>
:root{{--bg:#f6f6f7;--fg:#151517;--card:#fff;--mute:#6b6b75;--acc:#e8336d;--ok:#1f9d55}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121214;--fg:#ececf1;--card:#1d1d21;--mute:#9a9aa6}}}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}}
h1{{margin:0 0 4px}} .hint{{color:var(--mute);margin:0 0 20px}} h2{{margin:28px 0 10px;font-size:18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px}}
.card{{position:relative;background:var(--card);border-radius:12px;overflow:hidden}}
.card.saved{{opacity:.55}}
.card img,.card video{{display:block;width:100%;aspect-ratio:9/16;object-fit:cover;background:#000}}
.n{{position:absolute;top:8px;left:8px;z-index:1;background:var(--acc);color:#fff;font-weight:700;min-width:28px;
   text-align:center;padding:3px 8px;border-radius:999px}}
.meta{{padding:8px 10px 10px;font-size:13px}} .meta p{{margin:4px 0 0;color:var(--mute)}} .s{{color:var(--ok);font-weight:600}}
</style>
<h1>@{html.escape(username)} — {len(items)} highlight(s)</h1>
<p class="hint">Pick by number back in the app: <b>all</b>, <b>3</b>, <b>1,4,6</b> or <b>2-5</b>. Faded = already downloaded. Press play to preview (low quality - the download is the original).</p>
{"".join(cards)}"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def parse_selection(text: str, count: int) -> Optional[list[int]]:
    """'all', '3', '1,4,6', '2-5', '1-3 7' -> sorted 1-based numbers (None = invalid)."""
    t = text.strip().lower()
    if t in ("all", "a", "*"):
        return list(range(1, count + 1))
    picked = set()
    for part in re.split(r"[,\s]+", t):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a > b:
            a, b = b, a
        if a < 1 or b > count:
            return None
        picked.update(range(a, b + 1))
    return sorted(picked) or None


def creator_folder_for(username: str, cfg: AppConfig) -> Optional[str]:
    """If this account belongs to a creator group, save into the group's folder."""
    if not cfg.creator_folders:
        return None
    for group, accounts in cfg.creators.items():
        if username.lower() in (a.lower() for a in accounts):
            return group
    return None


def run_highlights(username: str, cache: dict, output_dir: str, cfg: Optional[AppConfig] = None,
                   progress_callback=None, ask=None) -> int:
    from .bulk import process_many  # avoid import cycle

    cfg = cfg or load_config()
    ask = ask or (lambda prompt: input(prompt))
    username = username.strip().lstrip("@")
    if not username:
        print("Usage: highlights @username")
        return 0
    print(f"Looking up @{username}'s highlights (via omar-thing.site)...")
    try:
        items = fetch_highlights(username, cfg)
    except HighlightError as e:
        print(f"Highlights unavailable: {e}")
        return 0
    if not items:
        print(f"@{username} has no public highlights.")
        return 0

    saved = {it["post_id"] for it in items if find_all_downloads(output_dir, it["post_id"])}
    last_col = None
    for i, it in enumerate(items, 1):
        if it["collection"] != last_col:
            print(f"\n  [{it['collection']}]")
            last_col = it["collection"]
        kind = f"{it['images']} photos" if it["is_photo"] else "video"
        mark = "  (saved)" if it["post_id"] in saved else ""
        desc = (" - " + it["desc"][:50]) if it["desc"] else ""
        print(f"  {i:>3}. {it['created'] or '?':<19}  {kind:<9}{desc}{mark}")

    try:
        preview = write_preview(username, items, saved, os.path.join(cfg.base_dir, "highlights_preview.html"))
        webbrowser.open("file:///" + os.path.abspath(preview).replace(os.sep, "/"))
        print(f"\nOpened a preview with covers in your browser ({os.path.basename(preview)}).")
    except Exception:
        pass

    while True:
        try:
            choice = ask(f"Download which? all, 3, 1,4,6, 2-5 - or Enter to cancel: ").strip()
        except EOFError:
            choice = ""
        if not choice:
            print("Cancelled.")
            return 0
        picks = parse_selection(choice, len(items))
        if picks:
            break
        print(f"Didn't understand that - use numbers 1-{len(items)}, ranges like 2-5, or all.")

    chosen = sorted((items[n - 1] for n in picks), key=lambda it: int(it["post_id"]))  # oldest first
    todo = [it for it in chosen if it["post_id"] not in saved]
    if len(todo) < len(chosen):
        print(f"Skipping {len(chosen) - len(todo)} already downloaded.")
    if not todo:
        return 0
    folder = creator_folder_for(username, cfg)
    print(f"Downloading {len(todo)} highlight(s) in original quality, oldest first"
          + (f" into folder '{folder}'" if folder else "") + "...")
    ok, total = process_many([post_url(username, it) for it in todo], cache, output_dir, cfg,
                             progress_callback, folder_name=folder)
    print(f"Highlights: {ok}/{total} downloaded")
    return ok
