"""TikVortex - original-quality TikTok downloader (GUI).

Copyright (C) 2026 moohyo labs <https://github.com/moohyolabs>
Based on Ez-TikTok-Downloader by AlexRabbit (public domain / Unlicense).

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See LICENSE for details.
"""
import os
import sys

__version__ = "1.0.0"
APP_NAME = "TikVortex"

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(PKG_DIR)), "tikvortex", "assets") \
    if getattr(sys, "frozen", False) else os.path.join(PKG_DIR, "assets")


def app_dir() -> str:
    """Folder that holds config.json, the cache and downloads (next to the .exe when frozen).
    Set TIKVORTEX_HOME to keep them somewhere else."""
    if os.environ.get("TIKVORTEX_HOME"):
        return os.path.abspath(os.environ["TIKVORTEX_HOME"])
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(PKG_DIR)
