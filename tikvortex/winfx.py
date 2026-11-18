"""Windows-only touches for the frameless window: rounded corners and the drop shadow (Windows 11)."""
from __future__ import annotations

import sys


def apply_window_effects(widget) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    hwnd = int(widget.winId())
    dwm = ctypes.windll.dwmapi
    # Windows 11: ask for rounded corners (DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2)
    pref = ctypes.c_int(2)
    dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref))

    # A 1-pixel frame extension brings back the system drop shadow on a frameless window
    class MARGINS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]
    dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(MARGINS(1, 1, 1, 1)))

    # Clicking the app's taskbar button should minimise / restore it. Windows only does that for windows
    # that say they have a minimise box, which a frameless window doesn't - so add the style bits
    # (nothing is drawn: there's no caption bar for them to appear in).
    GWL_STYLE, WS_MINIMIZEBOX, WS_SYSMENU = -16, 0x00020000, 0x00080000
    user32 = ctypes.windll.user32
    get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    put = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get.restype = ctypes.c_ssize_t; put.restype = ctypes.c_ssize_t
    get.argtypes = [wintypes.HWND, ctypes.c_int]; put.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    style = get(wintypes.HWND(hwnd), GWL_STYLE)
    put(wintypes.HWND(hwnd), GWL_STYLE, style | WS_MINIMIZEBOX | WS_SYSMENU)
