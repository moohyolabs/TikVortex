"""Reading and writing config.json: engine settings at the top level, TikVortex's own under "ui"."""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from tikvortex.core.config import CONFIG_FILENAME, DEFAULTS, load_config, reset_config

UI_DEFAULTS: dict[str, Any] = {
    "theme_mode": "Auto",          # Light | Dark | Auto
    "colour": "Vortex",            # colour theme
    "glass": "Strong",             # Strong | Subtle | Off
    "reduce_motion": False,
    "moving_background": False,   # still background unless switched on (saves redraws)
    "check_updates": True,        # look for a newer release on GitHub at launch
    "library_per_row": 5,
    "library_sort": "posted_new",
    "group_pics": {},
    "finish_sound": True,
    "tray": True,                  # closing the window keeps TikVortex running in the tray
    "notify": True,
    "clipboard_on_launch": False,
    "start_with_windows": False,
    "start_mode": "open",          # how it starts with Windows: open, minimised (taskbar) or tray
    "failed_links": "Ask",         # Ask | Always | Never
    "geometry": None,
    "page": "Download",
    "queue_filter": "All",
    "library_tile": 150,
    "recent": [],                  # finished batches for the sidebar
}

_lock = threading.Lock()


class Store:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.path = os.path.join(base_dir, CONFIG_FILENAME)
        self.data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        data = dict(DEFAULTS)
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data.update(loaded)
                    if "folder_layout" not in loaded:      # older configs only had creator_folders
                        data["folder_layout"] = "group" if loaded.get("creator_folders", True) else "account"
            except Exception:
                pass
        ui = dict(UI_DEFAULTS)
        if isinstance(data.get("ui"), dict):
            ui.update(data["ui"])
        data["ui"] = ui
        self.data = data

    # engine settings -------------------------------------------------
    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        if key == "folder_layout":
            self.data["creator_folders"] = value == "group"
        self.save()

    # ui settings -----------------------------------------------------
    def ui(self, key: str):
        return self.data["ui"].get(key, UI_DEFAULTS.get(key))

    def set_ui(self, key: str, value) -> None:
        self.data["ui"][key] = value
        self.save()

    def save(self) -> None:
        with _lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)

    def engine_config(self):
        """A fresh AppConfig reflecting the current file."""
        reset_config()
        return load_config(self.base_dir)
