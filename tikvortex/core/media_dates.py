"""Embed a capture date in downloaded files so Photos apps (iCloud Photos, Google
Photos, ...) sort them correctly.

- MP4: QuickTime creation/modification time in mvhd/tkhd/mdhd (what Photos reads
  as the video's date), patched in place - no re-encode, no ffmpeg needed.
- JPEG: EXIF DateTimeOriginal / CreateDate (+ time-zone offset).
- Everything (incl. PNG/WebP, where only this applies): file modified time.

Modes (config "media_dates"):
  "downloaded" - strictly increasing timestamps in the order posts are queued,
                 at least 1 s apart, so nothing ties - even with parallel downloads.
  "posted"     - the TikTok post time.
  "off"        - leave files alone.
"""
from __future__ import annotations

import os
import re
import struct
import threading
import time
from typing import Iterable, Optional

MAC_EPOCH_OFFSET = 2082844800  # seconds between 1904-01-01 and 1970-01-01

_lock = threading.Lock()
_last_stamp = 0


def next_download_stamp(count: int = 1) -> int:
    """Reserve `count` consecutive whole seconds, always after the previous reservation."""
    global _last_stamp
    with _lock:
        start = max(int(time.time()), _last_stamp + 1)
        _last_stamp = start + max(1, count) - 1
        return start


def stamp_for(entry: dict, mode: str, count: int = 1) -> Optional[int]:
    """Timestamp for the first file of a post (file i gets stamp + i)."""
    if mode == "off":
        return None
    if mode == "posted":
        try:
            ct = int(entry.get("create_time"))
            if ct > 0:
                return ct
        except (TypeError, ValueError):
            pass
    return next_download_stamp(count)


# ---------------------------------------------------------------- MP4 ----

def _iter_boxes(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        size, typ = struct.unpack(">I4s", hdr)
        hlen = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hlen = 16
        elif size == 0:
            size = end - pos
        if size < hlen:
            return
        yield typ, pos, hlen, size
        pos += size


def set_mp4_dates(path: str, ts: int) -> bool:
    qt = int(ts) + MAC_EPOCH_OFFSET
    targets: list[int] = []
    with open(path, "r+b") as f:
        f.seek(0, 2)
        end = f.tell()

        def walk(s: int, e: int, depth: int) -> None:
            for typ, pos, hlen, size in list(_iter_boxes(f, s, e)):
                if typ in (b"moov", b"trak", b"mdia") and depth < 4:
                    walk(pos + hlen, pos + size, depth + 1)
                elif typ in (b"mvhd", b"tkhd", b"mdhd") and depth > 0:
                    targets.append(pos + hlen)

        walk(0, end, 0)
        for off in targets:
            f.seek(off)
            version = f.read(1)[0]
            f.seek(off + 4)  # skip version + flags
            if version == 1:
                f.write(struct.pack(">QQ", qt, qt))
            else:
                f.write(struct.pack(">II", qt & 0xFFFFFFFF, qt & 0xFFFFFFFF))
    return bool(targets)


# --------------------------------------------------------------- JPEG ----

def _ifd(entries, base: int) -> bytes:
    data_off = base + 2 + 12 * len(entries) + 4
    head = struct.pack(">H", len(entries))
    data = b""
    for tag, typ, cnt, val in entries:
        if len(val) <= 4:
            head += struct.pack(">HHI", tag, typ, cnt) + val.ljust(4, b"\0")
        else:
            head += struct.pack(">HHII", tag, typ, cnt, data_off + len(data))
            data += val + (b"\0" if len(val) % 2 else b"")
    return head + b"\0\0\0\0" + data


def _date_strings(ts: int) -> tuple[bytes, bytes]:
    lt = time.localtime(ts)
    dt = time.strftime("%Y:%m:%d %H:%M:%S", lt).encode() + b"\0"
    gmtoff = lt.tm_gmtoff or 0
    sign = "+" if gmtoff >= 0 else "-"
    off = f"{sign}{abs(gmtoff) // 3600:02d}:{abs(gmtoff) % 3600 // 60:02d}".encode() + b"\0"
    return dt, off


def _exif_app1(ts: int, orientation: Optional[int] = None) -> bytes:
    dt, off = _date_strings(ts)

    def ifd0(exif_ptr: int) -> bytes:
        entries = []
        if orientation:
            entries.append((0x0112, 3, 1, struct.pack(">H", orientation)))
        entries += [(0x0132, 2, 20, dt), (0x8769, 4, 1, struct.pack(">I", exif_ptr))]
        return _ifd(entries, 8)

    exif_ptr = 8 + len(ifd0(0))
    exif = _ifd([
        (0x9003, 2, 20, dt), (0x9004, 2, 20, dt),
        (0x9010, 2, 7, off), (0x9011, 2, 7, off), (0x9012, 2, 7, off),
    ], exif_ptr)
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + ifd0(exif_ptr) + exif
    payload = b"Exif\0\0" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def _patch_existing_exif(tiff: bytearray, ts: int) -> tuple[bool, Optional[int]]:
    """Overwrite date tags inside an existing EXIF block (e.g. one Photoshop or
    an earlier run wrote). Returns (DateTimeOriginal was set, orientation)."""
    dt, off = _date_strings(ts)
    if tiff[:2] == b"II":
        e = "<"
    elif tiff[:2] == b"MM":
        e = ">"
    else:
        return False, None
    got_original = False
    orientation = None

    def walk(ifd_off: int, depth: int) -> None:
        nonlocal got_original, orientation
        if depth > 2 or ifd_off + 2 > len(tiff):
            return
        (n,) = struct.unpack_from(e + "H", tiff, ifd_off)
        for k in range(n):
            ent = ifd_off + 2 + 12 * k
            if ent + 12 > len(tiff):
                return
            tag, typ, cnt = struct.unpack_from(e + "HHI", tiff, ent)
            if tag == 0x0112 and typ == 3:
                orientation = struct.unpack_from(e + "H", tiff, ent + 8)[0]
            elif tag == 0x8769:
                walk(struct.unpack_from(e + "I", tiff, ent + 8)[0], depth + 1)
            elif typ == 2 and tag in (0x0132, 0x9003, 0x9004) and cnt == 20:
                (voff,) = struct.unpack_from(e + "I", tiff, ent + 8)
                if voff + 20 <= len(tiff):
                    tiff[voff:voff + 20] = dt
                    got_original |= tag == 0x9003
            elif typ == 2 and tag in (0x9010, 0x9011, 0x9012) and cnt == 7:
                (voff,) = struct.unpack_from(e + "I", tiff, ent + 8)
                if voff + 7 <= len(tiff):
                    tiff[voff:voff + 7] = off

    walk(struct.unpack_from(e + "I", tiff, 4)[0], 0)
    return got_original, orientation


def set_jpeg_dates(path: str, ts: int) -> bool:
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"\xff\xd8"):
        return False
    pos = 2
    insert_at = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        seg_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\0\0":
            # Existing EXIF (from Photoshop, a camera, or an earlier run):
            # update its dates in place so other metadata is kept.
            seg_end = pos + 2 + seg_len
            tiff = bytearray(data[pos + 10:seg_end])
            got_original, orientation = _patch_existing_exif(tiff, ts)
            if got_original:
                new = data[:pos + 10] + bytes(tiff) + data[seg_end:]
            else:
                # No "date taken" tag to update: replace the block with ours,
                # keeping the rotation so the photo still displays upright.
                new = data[:pos] + _exif_app1(ts, orientation) + data[seg_end:]
            break
        if marker == 0xE0 and insert_at == pos:
            insert_at = pos + 2 + seg_len
        if marker == 0xDA:  # start of scan - no more headers
            new = data[:insert_at] + _exif_app1(ts) + data[insert_at:]
            break
        pos += 2 + seg_len
    else:
        new = data[:insert_at] + _exif_app1(ts) + data[insert_at:]
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(new)
    os.replace(tmp, path)
    return True


# -------------------------------------------------------------- apply ----

def apply_date(path: str, ts: Optional[int]) -> None:
    """Best effort - never raises."""
    if ts is None or not os.path.isfile(path):
        return
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        if head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide"):
            set_mp4_dates(path, ts)
        elif head.startswith(b"\xff\xd8"):
            set_jpeg_dates(path, ts)
    except Exception as e:
        print(f"Note: couldn't write date metadata to {os.path.basename(path)}: {e}")
    try:
        os.utime(path, (ts, ts))
    except OSError:
        pass


def apply_dates(paths: Iterable[str], start_ts: Optional[int]) -> None:
    if start_ts is None:
        return
    for i, p in enumerate(paths):
        apply_date(p, start_ts + i)


# ------------------------------------------------- fix existing files ----

MEDIA_RE = re.compile(r"\.(mp4|mov|m4v|jpe?g|png|webp)$", re.IGNORECASE)
POST_ID_RE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")
IMG_INDEX_RE = re.compile(r"^(?:_img_|_)(\d{1,3})(?=[ .])")
TIKWM_IMG_RE = re.compile(r"_img_(\d{1,3})\.")


def split_media_name(name: str, known_ids=()) -> tuple[Optional[str], int]:
    """(post_id, image_index) from a filename. The default template also contains
    the account's numeric user ID, so prefer an ID known from the cache, else the
    last long number (the post ID comes last in the template)."""
    m_img = TIKWM_IMG_RE.search(name)
    head = name[:m_img.start()] if m_img else name
    cands = list(POST_ID_RE.finditer(head))
    if not cands:
        return None, 0
    pick = next((c for c in cands if c.group(1) in known_ids), None)
    if pick is None:
        # gallery-dl names start with the post ID ("{id}_01 title.jpg")
        pick = cands[0] if IMG_INDEX_RE.match(head[cands[0].end():]) else cands[-1]
    if m_img:
        idx = int(m_img.group(1))
    else:
        m = IMG_INDEX_RE.match(head[pick.end():])
        idx = int(m.group(1)) if m else 0
    return pick.group(1), idx


def fix_existing(output_dir: str, cache: dict, mode: str) -> None:
    """Stamp files that were downloaded before this feature existed."""
    if mode == "off":
        print('media_dates is "off" in config.json - nothing to do.')
        return
    files = []
    for root, _dirs, names in os.walk(output_dir):
        for n in names:
            if MEDIA_RE.search(n):
                pid, idx = split_media_name(n, cache)
                files.append((os.path.join(root, n), pid, idx))
    if not files:
        print(f"No media files found in {output_dir}")
        return

    done = skipped = 0
    if mode == "posted":
        for p, pid, idx in files:
            ct = (cache.get(pid) or {}).get("create_time") if pid else None
            try:
                ts = int(ct)
            except (TypeError, ValueError):
                skipped += 1
                continue
            apply_date(p, ts + max(idx - 1, 0))
            done += 1
    else:
        # Keep the existing download order, but make every timestamp unique
        # and keep each slideshow's images together and in order.
        files.sort(key=lambda t: (os.path.getmtime(t[0]), t[1] or "", t[2]))
        last = 0
        for p, _pid, _idx in files:
            ts = max(int(os.path.getmtime(p)), last + 1)
            apply_date(p, ts)
            last = ts
            done += 1
    print(f"Dated {done} file(s)" + (f", skipped {skipped} with no post time in cache" if skipped else ""))


def _post_files(folder: str, known_ids=()) -> list[tuple[str, int, int]]:
    out = []
    for root, _dirs, names in os.walk(folder):
        for n in names:
            if not MEDIA_RE.search(n):
                continue
            pid, idx = split_media_name(n, known_ids)
            if pid:
                out.append((os.path.join(root, n), int(pid), idx))
    return out


def parse_start(text: str, count: int) -> int:
    """Start time for a re-order block, in local time:
       "now"               the block ends at the current time
       "21:10"             today at 21:10
       "2026-10-07 21:10"  that date and time"""
    from datetime import datetime
    t = (text or "").strip().strip('"')
    if t.lower() == "now":
        return int(time.time()) - max(count, 1) + 1
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            dt = datetime.now().replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
        return int(dt.timestamp())
    raise ValueError(f'Can\'t read start time "{text}". Use now, 21:10, or 2026-10-07 21:10')


def _fmt(ts: float) -> str:
    return time.strftime("%a %d %b %Y %I:%M:%S %p", time.localtime(ts))


def _collect_group(output_dir: str, names: list[str], known_ids) -> list[tuple[str, int, int]]:
    # Keyed by the file on disk, not the path: on Windows/macOS "Jane" and
    # "jane" are the same folder, and each file must be counted once.
    files: dict[tuple[int, int], tuple[str, int, int]] = {}
    wanted = {n.lower() for n in names}
    for name in sorted(os.listdir(output_dir)):  # match folder names ignoring case
        folder = os.path.join(output_dir, name)
        if name.lower() in wanted and os.path.isdir(folder):
            for f in _post_files(folder, known_ids):
                st = os.stat(f[0])
                files.setdefault((st.st_dev, st.st_ino), f)
    return sorted(files.values(), key=lambda t: (t[1], t[2], t[0]))


def _overlaps(output_dir: str, names: list[str], lo: int, hi: int) -> dict[str, int]:
    """Files in OTHER folders already dated inside [lo, hi] - they'd be mixed in."""
    wanted = {n.lower() for n in names}
    hits: dict[str, int] = {}
    for d in os.listdir(output_dir):
        full = os.path.join(output_dir, d)
        if d.lower() in wanted or not os.path.isdir(full):
            continue
        for root, _dirs, fnames in os.walk(full):
            for n in fnames:
                if MEDIA_RE.search(n) and lo <= os.path.getmtime(os.path.join(root, n)) <= hi:
                    hits[d] = hits.get(d, 0) + 1
    return hits


def reorder_by_post(
    output_dir: str,
    folder_groups: Optional[list[list[str]]] = None,
    known_ids=(),
    start: Optional[str] = None,
    confirm: bool = True,
) -> None:
    """Re-date files so each group runs oldest post -> newest, one second apart,
    as if downloaded in that order.

    folder_groups: lists of folder names (under output_dir) to treat as ONE
    timeline - e.g. a creator group plus its accounts. None = every top-level
    folder on its own.
    start: when the oldest file should be dated (see parse_start). Without it
    each group starts at its earliest current file date, keeping its place.
    Shows a preview and asks first (confirm=False skips the question)."""
    if folder_groups is None:
        if start:
            print("Name a folder or creator group when giving a start time, e.g. reorder pinkie from 21:10")
            return
        folder_groups = [[d] for d in sorted(os.listdir(output_dir)) if os.path.isdir(os.path.join(output_dir, d))]

    now = int(time.time())
    plans = []
    for names in folder_groups:
        files = _collect_group(output_dir, names, known_ids)
        if not files:
            print(f"Nothing to re-order in: {', '.join(names)}")
            continue
        try:
            if start:
                first = parse_start(start, len(files))
            else:
                # Keep the group's place, but never run past the current time
                first = min(int(min(os.path.getmtime(p) for p, _, _ in files)), now - len(files) + 1)
        except ValueError as e:
            print(e)
            return
        last = first + len(files) - 1
        print(f"\n{', '.join(names)}: {len(files)} file(s), oldest post -> newest")
        print(f"  first: {_fmt(first)}   {os.path.basename(files[0][0])}")
        print(f"  last:  {_fmt(last)}   {os.path.basename(files[-1][0])}")
        if last > now:
            print(f"  STOPPED: the last file would be {last - now} s in the future - pick an earlier start.")
            continue
        clash = _overlaps(output_dir, names, first, last)
        if clash:
            print("  Warning: these folders already have files in this time window and would be mixed in: "
                  + ", ".join(f"{d} ({n})" for d, n in sorted(clash.items()))
                  + ". Pick a start after them to avoid that.")
        plans.append((names, files, first, last))

    if not plans:
        return
    if confirm:
        try:
            ans = input(f"\nApply these dates to {sum(len(p[1]) for p in plans)} file(s)? [y/N] ")
        except EOFError:
            ans = ""
        if ans.strip().lower() != "y":
            print("Nothing changed.")
            return
    for names, files, first, last in plans:
        for i, (p, _pid, _idx) in enumerate(files):
            apply_date(p, first + i)
        print(f"Re-ordered {len(files)} file(s): {', '.join(names)}  ({_fmt(first)} -> {_fmt(last)})")
