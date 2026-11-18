"""Finds a TikTok link in pasted or copied text."""
from __future__ import annotations

import re
from typing import Optional

TIKTOK_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?(?:tiktok\.com|vt\.tiktok\.com|vm\.tiktok\.com)/[^\s]+",
    re.IGNORECASE,
)


def extract_tiktok_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = TIKTOK_URL_RE.search(text.strip())
    return m.group(0) if m else None
