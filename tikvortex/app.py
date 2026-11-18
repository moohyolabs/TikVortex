"""Start-up: single instance, paths, fonts, logging, then the main window."""
from __future__ import annotations

import os
import sys
import time


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    from . import APP_NAME, app_dir
    base = app_dir()
    os.chdir(base)
    os.environ.setdefault("EZTIKTOK_DATA", base)

    if sys.platform == "win32" and "QT_QPA_PLATFORM" not in os.environ:
        # FreeType renders the bundled Geist font smoothly (grey anti-aliasing, light hinting);
        # Windows' own engine snaps it to the pixel grid, which looks jagged at 125-150% scaling.
        os.environ["QT_QPA_PLATFORM"] = "windows:fontengine=freetype"

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont, QGuiApplication, QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME); app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TikVortex.App")
        except Exception:
            pass

    # One TikVortex at a time: a second launch hands its links to the first and exits.
    server_name = f"{APP_NAME}-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"
    links = " ".join(a for a in argv if not a.startswith("--"))
    stamp = _build_stamp()
    sock = QLocalSocket(); sock.connectToServer(server_name)
    if sock.waitForConnected(400):
        sock.write(f"SHOW {stamp}\n{links}".encode("utf-8")); sock.flush(); sock.waitForBytesWritten(1000)
        replaced = sock.waitForReadyRead(1500) and bytes(sock.readAll()).startswith(b"RESTART")
        sock.disconnectFromServer()
        if not replaced:
            return
        # The running copy is an older build (e.g. still in the tray after an update): it saves and
        # quits, and this one takes over.
        for _ in range(200):                               # up to ~1 min (it may ask about running downloads)
            probe = QLocalSocket(); probe.connectToServer(server_name)
            if not probe.waitForConnected(150):
                break
            probe.disconnectFromServer(); time.sleep(0.15)
        else:
            return                                         # the old copy is staying open
    QLocalServer.removeServer(server_name)
    server = QLocalServer(); server.listen(server_name)

    # Everything the engine prints goes to log.txt (and the window).
    from .engine import LogBridge, StdRouter
    bridge = LogBridge()
    log_path = os.path.join(base, "log.txt")
    old = os.path.join(base, "ez.log")
    if os.path.isfile(old) and not os.path.exists(log_path):   # the log used to be called ez.log
        try:
            os.replace(old, log_path)
        except OSError:
            pass
    router = StdRouter(log_path, bridge)
    sys.stdout = router; sys.stderr = router

    from . import ASSETS
    from .widgets import load_fonts
    load_fonts()
    app.setWindowIcon(QIcon(os.path.join(ASSETS, "icon.ico" if sys.platform == "win32" else "icon.png")))
    f = QFont("Geist"); f.setPixelSize(13)
    f.setHintingPreference(QFont.PreferNoHinting)            # true letter shapes and spacing (no grid snapping)
    # greyscale antialiasing everywhere: subpixel (ClearType-style) text through FreeType spaces letters unevenly
    f.setStyleStrategy(QFont.StyleStrategy(QFont.PreferAntialias | QFont.NoSubpixelAntialias | QFont.PreferQuality))
    app.setFont(f)

    from tikvortex.core.cache import load_cache
    from .store import Store
    from .window import MainWindow
    store = Store(base)
    cfg = store.engine_config()
    cache = load_cache(cfg.cache_path)
    win = MainWindow(store, cfg, cache, base)

    def on_conn():
        c = server.nextPendingConnection()
        if c is None:
            return
        c.waitForReadyRead(500)
        msg = bytes(c.readAll()).decode("utf-8", "replace")
        head, _, rest = msg.partition("\n")
        if head.startswith("SHOW ") and head[5:].strip() != stamp:
            c.write(b"RESTART"); c.flush(); c.waitForBytesWritten(500); c.disconnectFromServer()
            QTimer.singleShot(100, win.quit_app)
            return
        win.bring_to_front()
        rest = rest.strip()
        if rest:
            win.qm.submit(rest); win.show_page("Download")
        c.disconnectFromServer()
    server.newConnection.connect(on_conn)

    if "--tray" in argv and win.tray:
        pass                                          # started hidden in the tray
    elif "--minimized" in argv:
        win.showMinimized()                           # started minimised to the taskbar
    else:
        win.show()
    if links:
        win.qm.submit(links)
    sys.exit(app.exec())


def _build_stamp() -> str:
    """Changes whenever the app's code changes, so a newly updated launch can replace an old copy."""
    if getattr(sys, "frozen", False):
        try:
            return str(int(os.path.getmtime(sys.executable)))
        except OSError:
            return "frozen"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    latest, count = 0.0, 0
    for pkg in ("tikvortex",):
        for dirpath, _dirs, files in os.walk(os.path.join(root, pkg)):
            for f in files:
                if f.endswith(".py"):
                    count += 1
                    try:
                        latest = max(latest, os.path.getmtime(os.path.join(dirpath, f)))
                    except OSError:
                        pass
    return f"{int(latest)}-{count}"


if __name__ == "__main__":
    main()
