# PyInstaller spec for TikVortex.  Build with:  pyinstaller TikVortex.spec --noconfirm
# Produces dist/TikVortex/TikVortex.exe.
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("tikvortex/assets", "tikvortex/assets")] + collect_data_files("qtawesome")
hidden = ["truststore", "qtawesome", "yt_dlp"]
_cc = collect_all("curl_cffi")                     # browser impersonation for yt-dlp (native library + data)
datas += _cc[0]; hidden += _cc[2]
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
            "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtBluetooth",
            "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSql", "PySide6.QtTest", "numpy"]
icon = "tikvortex/assets/icon.ico" if sys.platform == "win32" else None

gui = Analysis(["TikVortex.pyw"], datas=datas, binaries=_cc[1], hiddenimports=hidden, excludes=excludes)

# Qt pieces TikVortex never uses (pulled in by plugins) - dropping them saves ~60 MB.
UNUSED = ("Qt6Quick", "Qt6Qml", "Qt6VirtualKeyboard", "Qt6Pdf", "Qt6WebEngine", "Qt6Designer", "Qt6Charts",
          "Qt6DataVisualization", "Qt63D", "qtvirtualkeyboard", "Qt6Wayland", "Qt6WlShell", "opengl32sw")
gui.binaries = [b for b in gui.binaries if not any(u.lower() in b[0].lower() for u in UNUSED)]
gui.datas = [d for d in gui.datas if not any(u.lower() in d[0].lower() for u in UNUSED) and "/translations/" not in d[0].replace("\\", "/")]

gui_pyz = PYZ(gui.pure)

gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name="TikVortex", console=False, icon=icon,
              version="version_info.txt" if sys.platform == "win32" else None)

coll = COLLECT(gui_exe, gui.binaries, gui.datas, name="TikVortex")
