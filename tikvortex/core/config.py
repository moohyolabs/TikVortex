from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

CONFIG_FILENAME = "config.json"
SESSIONID_FILENAME = "sessionid.txt"
COOKIES_FILENAME = "cookies.txt"

DEFAULTS = {
    "output_dir": "profiles",
    "cache_file": "link_cache.json",
    "batch_delay_sec": 2,
    "naming_template": "{date} - {profile_uid} - {username} - {video_id}{ext}",
    "request_timeout": 25,
    "poll_interval": 1.0,
    "poll_attempts": 60,
    "original_wait_sec": 180,
    "retry_compressed_at_end": True,
    "clipboard_monitor": False,
    "clipboard_poll_sec": 1.5,
    "clipboard_dedupe_sec": 30,
    "allow_compressed_fallback": True,
    "accept_expired_tokcdn_cert": False,
    "download_workers": 3,
    "media_dates": "downloaded",
    "profile_order": "oldest",
    "creators": {},
    "creator_folders": True,
    "folder_layout": "group",
    "speed_limit_kbps": 0,
    "date_format": "%y-%m-%d",
    "gallery_dl_fallback": True,
}


@dataclass
class AppConfig:
    output_dir: str = "profiles"
    cache_file: str = "link_cache.json"
    batch_delay_sec: float = 2
    naming_template: str = "{date} - {profile_uid} - {username} - {video_id}{ext}"
    request_timeout: int = 25
    poll_interval: float = 1.0
    poll_attempts: int = 60
    original_wait_sec: float = 180
    retry_compressed_at_end: bool = True
    clipboard_monitor: bool = False
    clipboard_poll_sec: float = 1.5
    clipboard_dedupe_sec: float = 30
    allow_compressed_fallback: bool = True
    accept_expired_tokcdn_cert: bool = False
    download_workers: int = 3
    media_dates: str = "downloaded"
    profile_order: str = "oldest"
    creators: dict = field(default_factory=dict)
    creator_folders: bool = True
    folder_layout: str = "group"      # group | account | single
    speed_limit_kbps: int = 0         # 0 = unlimited
    date_format: str = "%y-%m-%d"     # the {date} part of file names
    gallery_dl_fallback: bool = True  # try gallery-dl when TikWM and yt-dlp can't list a profile
    base_dir: str = field(default_factory=os.getcwd)

    @property
    def output_path(self) -> str:
        p = self.output_dir
        if os.path.isabs(p):
            return p
        return os.path.join(self.base_dir, p)

    @property
    def cache_path(self) -> str:
        p = self.cache_file
        if os.path.isabs(p):
            return p
        return os.path.join(self.base_dir, p)

    @property
    def sessionid_path(self) -> str:
        return os.path.join(self.base_dir, SESSIONID_FILENAME)

    @property
    def cookies_path(self) -> str:
        return os.path.join(self.base_dir, COOKIES_FILENAME)


_config: Optional[AppConfig] = None


def _parse_creators(raw) -> dict:
    """{"jane": ["@jane", "jane_backup"]} -> {"jane": ["jane", "jane_backup"]}"""
    out = {}
    if isinstance(raw, dict):
        for name, accounts in raw.items():
            if isinstance(accounts, str):
                accounts = [accounts]
            if isinstance(accounts, list):
                accs = [str(a).strip().lstrip("@") for a in accounts if str(a).strip().lstrip("@")]
                if accs:
                    out[str(name).strip()] = list(dict.fromkeys(accs))
    return out


def _layout(data: dict) -> str:
    """folder_layout wins; older configs only have creator_folders (true/false)."""
    lay = str(data.get("folder_layout") or "").lower()
    if data.get("_has_layout") and lay in ("group", "account", "single"):
        return lay
    return "group" if bool(data.get("creator_folders", True)) else "account"


def load_config(base_dir: Optional[str] = None) -> AppConfig:
    global _config
    if _config is not None and (base_dir is None or base_dir == _config.base_dir):
        return _config

    root = base_dir or os.environ.get("EZTIKTOK_DATA") or os.getcwd()
    data = dict(DEFAULTS)
    cfg_path = os.path.join(root, CONFIG_FILENAME)
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data.update(loaded)
                data["_has_layout"] = "folder_layout" in loaded
        except Exception:
            pass

    _config = AppConfig(
        output_dir=str(data["output_dir"]),
        cache_file=str(data["cache_file"]),
        batch_delay_sec=float(data["batch_delay_sec"]),
        naming_template=str(data["naming_template"]),
        request_timeout=int(data["request_timeout"]),
        poll_interval=float(data["poll_interval"]),
        poll_attempts=int(data["poll_attempts"]),
        original_wait_sec=float(data["original_wait_sec"]),
        retry_compressed_at_end=bool(data["retry_compressed_at_end"]),
        clipboard_monitor=bool(data["clipboard_monitor"]),
        clipboard_poll_sec=float(data["clipboard_poll_sec"]),
        clipboard_dedupe_sec=float(data["clipboard_dedupe_sec"]),
        allow_compressed_fallback=bool(data["allow_compressed_fallback"]),
        accept_expired_tokcdn_cert=bool(data["accept_expired_tokcdn_cert"]),
        download_workers=max(1, int(data["download_workers"])),
        media_dates=str(data["media_dates"]).lower() if str(data["media_dates"]).lower() in ("downloaded", "posted", "off") else "downloaded",
        profile_order="newest" if str(data["profile_order"]).lower() == "newest" else "oldest",
        creators=_parse_creators(data.get("creators")),
        creator_folders=_layout(data) == "group",
        folder_layout=_layout(data),
        speed_limit_kbps=max(0, int(data.get("speed_limit_kbps") or 0)),
        date_format=str(data.get("date_format") or "%y-%m-%d"),
        gallery_dl_fallback=bool(data.get("gallery_dl_fallback", True)),
        base_dir=root,
    )
    return _config


def reset_config() -> None:
    global _config
    _config = None
