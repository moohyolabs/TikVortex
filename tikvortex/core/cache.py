from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
ENTRY_FIELDS = frozenset({
    "play_url", "images", "username", "video_id", "create_time", "profile_uid", "content_type",
    "quality", "source", "local_files",
})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_entry(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        value.get("play_url")
        or (value.get("images") and len(value.get("images", [])) > 0)
        or value.get("local_files")  # slideshow saved by the gallery-dl fallback
    )


def _normalize_entry(entry: dict) -> dict:
    out = dict(entry)
    if "content_type" not in out:
        out["content_type"] = "video"
    return out


def _parse_raw(data: Any) -> dict[str, dict]:
    """Accept legacy flat map or schema v1 wrapper."""
    if not isinstance(data, dict):
        return {}

    if "schema_version" in data and "entries" in data:
        entries = data.get("entries") or {}
        if isinstance(entries, dict):
            return {k: _normalize_entry(v) for k, v in entries.items() if _is_entry(v)}
        return {}

    # Legacy flat: video_id -> entry
    if any(_is_entry(v) for v in data.values() if isinstance(v, dict)):
        return {k: _normalize_entry(v) for k, v in data.items() if isinstance(v, dict) and _is_entry(v)}

    return {}


def load_cache(path: str) -> dict[str, dict]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _parse_raw(json.load(f))
    except Exception:
        return {}


def save_cache(entries: dict[str, dict], path: str) -> None:
    wrapper = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _utc_now_iso(),
        "app": "ez-tiktok-downloader",
        "entries": {k: _normalize_entry(v) for k, v in entries.items()},
    }
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wrapper, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save cache: {e}")

