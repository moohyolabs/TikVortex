"""Reusable painted widgets: wordmark text, backdrop, pills, switches, tiles and toasts."""
from __future__ import annotations

import ctypes
import glob
import math
import os
import sys
import time
from typing import Callable, Optional

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QDialog, QFrame, QGraphicsOpacityEffect,
                               QCalendarWidget, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import ASSETS
from .theme import BAD, GREY, INFO, OK, WARN, Palette

TITLE_FAMILY = "Inter Display"


def load_fonts() -> None:
    for f in sorted(glob.glob(os.path.join(ASSETS, "fonts", "*.[ot]tf"))):
        QFontDatabase.addApplicationFont(f)


# ---------------------------------------------------------------- icons
def _qta():
    import qtawesome
    return qtawesome


def icon(name: str, color: str) -> QIcon:
    return _qta().icon(name, color=color)


_CENTRED: dict = {}


def centred_pixmap(name: str, color: str, px: int, dpr: float = 2.0, dy: float = 0.0) -> QPixmap:
    """Icon rendered so its visual weight (alpha centre of mass) sits in the middle of the box."""
    key = (name, color, px, dpr, dy)
    if key in _CENTRED:
        return _CENTRED[key]
    S = int(px * dpr * 3)
    src = _qta().icon(name, color=color).pixmap(S, S).toImage().convertToFormat(QImage.Format_ARGB32)
    src.setDevicePixelRatio(1.0)
    w, h, bpl = src.width(), src.height(), src.bytesPerLine()
    raw = bytes(src.constBits())[: bpl * h]
    tot = sx = sy = 0
    for y in range(h):
        row = raw[y * bpl + 3: y * bpl + w * 4: 4]
        rs = sum(row)
        if rs:
            tot += rs; sy += rs * y
            sx += sum(a * x for x, a in enumerate(row) if a)
    out = QImage(w, h, QImage.Format_ARGB32); out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawImage(QPointF(w / 2 - (sx / tot if tot else w / 2) - 0.5, h / 2 - (sy / tot if tot else h / 2) - 0.5 + dy * h / px), src)
    p.end()
    out.setDevicePixelRatio(1.0)
    pm = QPixmap.fromImage(out.scaled(int(px * dpr), int(px * dpr), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    pm.setDevicePixelRatio(dpr)
    _CENTRED[key] = pm
    return pm


class IconLabel(QLabel):
    def __init__(self, name: str, color: str, px: int):
        super().__init__()
        self.setFixedSize(px, px); self.setAlignment(Qt.AlignCenter)
        self.set_icon(name, color, px)

    def set_icon(self, name: str, color: str, px: int, dy: float = 0.0) -> None:
        self.setPixmap(centred_pixmap(name, color, px, dy=dy))


# ---------------------------------------------------------------- wide title lettering
def wordmark(text: str, px: float, colors, weight=QFont.Black, stretch: float = 1.30, spacing: float = 0.5,
             dpr: float = 2.0) -> QPixmap:
    """The wide TikVortex lettering (Inter Display, horizontally stretched). colors: one colour or a (start, end) gradient."""
    f = QFont(TITLE_FAMILY); f.setWeight(weight); f.setPixelSize(max(1, int(round(px * dpr))))
    f.setLetterSpacing(QFont.AbsoluteSpacing, spacing * dpr)
    path = QPainterPath(); path.addText(0, 0, f, text)
    path = QTransform().scale(stretch, 1).map(path)
    br = path.boundingRect()
    h = int(px * dpr * 1.35); w = int(br.width() + 6 * dpr)
    path.translate(2 * dpr - br.left(), h / 2 - br.center().y())
    img = QImage(max(1, w), max(1, h), QImage.Format_ARGB32_Premultiplied); img.fill(Qt.transparent)
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
    if isinstance(colors, (tuple, list)):
        g = QLinearGradient(path.boundingRect().topLeft(), path.boundingRect().topRight())
        g.setColorAt(0, QColor(colors[0])); g.setColorAt(1, QColor(colors[1])); p.fillPath(path, g)
    else:
        p.fillPath(path, QColor(colors))
    p.end()
    pm = QPixmap.fromImage(img); pm.setDevicePixelRatio(dpr)
    return pm


class Wordmark(QLabel):
    def __init__(self, text: str, px: float, colors, weight=QFont.Black):
        super().__init__()
        self._args = (text, px, colors, weight)
        self.render_now()

    def set_colors(self, colors) -> None:
        t, px, _c, w = self._args; self._args = (t, px, colors, w); self.render_now()

    def set_text(self, text: str) -> None:
        _t, px, c, w = self._args; self._args = (text, px, c, w); self.render_now()

    def render_now(self) -> None:
        pm = wordmark(*self._args); self.setPixmap(pm)
        self.setFixedSize(int(pm.width() / pm.devicePixelRatio()), int(pm.height() / pm.devicePixelRatio()))


def app_icon_pixmap(px: int, dpr: float = 2.0) -> QPixmap:
    img = QImage(os.path.join(ASSETS, "icon.png"))
    pm = QPixmap.fromImage(img.scaled(int(px * dpr), int(px * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    pm.setDevicePixelRatio(dpr)
    return pm


# ---------------------------------------------------------------- backdrop
class Backdrop(QWidget):
    """Gradient window background with soft colour blobs that drift very slowly."""
    edgePressed = Signal(object)
    TICK_MS = 1000
    SCALE = 4

    def __init__(self, pal: Palette, radius: int = 0):
        super().__init__()
        self.pal = pal; self.radius = radius
        self.motion = True
        self._t0 = time.monotonic(); self._img = None; self._key = None
        self._timer = QTimer(self); self._timer.timeout.connect(self.update); self._timer.start(self.TICK_MS)
        if not radius:
            self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMouseTracking(True)

    def set_palette(self, pal: Palette) -> None:
        self.pal = pal; self._key = None; self.update()

    def set_motion(self, on: bool) -> None:
        self.motion = on
        if on and not self._timer.isActive():
            self._timer.start(self.TICK_MS)
        elif not on:
            self._timer.stop(); self.update()

    def pause(self, paused: bool) -> None:
        if paused:
            self._timer.stop()
        elif self.motion:
            self._timer.start(self.TICK_MS)

    def _render(self, w: int, h: int, t: float) -> QImage:
        sw, sh = max(1, w // self.SCALE + 1), max(1, h // self.SCALE + 1)
        img = QImage(sw, sh, QImage.Format_ARGB32_Premultiplied)
        pal = self.pal; p = QPainter(img); r = img.rect()
        g = QLinearGradient(0, 0, sw, sh); g.setColorAt(0, QColor(pal.bg0)); g.setColorAt(1, QColor(pal.bg1)); p.fillRect(r, g)
        alpha = (150, 120, 110)
        for i, (fx, fy, rad, col) in enumerate(((0.15, 0.1, 0.45, pal.blobA), (0.95, 0.25, 0.4, pal.blobB), (0.55, 1.05, 0.5, pal.blobC))):
            dx = 0.04 * math.sin(t / (23 + i * 7) + i * 2.1); dy = 0.035 * math.cos(t / (29 + i * 5) + i)
            c = QColor(col); c.setAlpha(alpha[i])
            rg = QRadialGradient(QPointF(sw * (fx + dx), sh * (fy + dy)), sw * rad)
            rg.setColorAt(0, c); c2 = QColor(c); c2.setAlpha(0); rg.setColorAt(1, c2)
            p.fillRect(r, rg)
        p.end()
        return img

    def paintEvent(self, e):
        r = self.rect()
        t = round(time.monotonic() - self._t0) if self.motion else 0
        dpr = self.devicePixelRatioF()
        key = (r.width(), r.height(), dpr, t, self.pal.bg0, self.pal.blobA, self.pal.blobB, self.pal.blobC)
        if key != self._key or self._img is None:
            small = self._render(r.width(), r.height(), float(t))
            small = small.copy(0, 0, max(1, math.ceil(r.width() / self.SCALE)), max(1, math.ceil(r.height() / self.SCALE)))
            self._img = QPixmap.fromImage(small.scaled(max(1, round(r.width() * dpr)), max(1, round(r.height() * dpr)),
                                                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            self._img.setDevicePixelRatio(dpr); self._key = key
        p = QPainter(self)
        if self.radius:
            p.setRenderHint(QPainter.Antialiasing)
            clip = QPainterPath(); clip.addRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius); p.setClipPath(clip)
        p.drawPixmap(0, 0, self._img)
        if self.radius:
            p.setClipping(False)
            p.setPen(QPen(QColor(255, 255, 255, 40 if self.pal.dark else 90), 1)); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)


# ---------------------------------------------------------------- Windows popup shadow removal & Apple animations
def _strip_win_popup_frame(widget: QWidget) -> None:
    """Prevent Windows DWM from drawing a rectangular shadow/border behind rounded translucent popups."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        dwmapi = ctypes.windll.dwmapi
        policy = ctypes.c_int(1)      # DWMWA_NCRENDERING_POLICY (2) = DWMNCRP_DISABLED (1)
        dwmapi.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(policy), ctypes.sizeof(policy))
        corner = ctypes.c_int(1)      # DWMWA_WINDOW_CORNER_PREFERENCE (33) = DWMWCP_DONOTROUND (1)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    except Exception:
        pass


def animate_popup_in(widget: QWidget, ms: int = 165) -> None:
    """Apple-style smooth opacity dissolve when a popup, menu, or tooltip appears."""
    if not MOTION[0]:
        widget.setWindowOpacity(1.0)
        return
    old = getattr(widget, "_pop_anim", None)
    if old is not None:
        old.stop()
    widget.setWindowOpacity(0.0)
    anim = QVariantAnimation(widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(apple_curve())
    anim.valueChanged.connect(lambda v, w=widget: w.setWindowOpacity(float(v)))
    widget._pop_anim = anim
    anim.start()


POPUP_FLAGS = Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
TEXT = ["#ffffff"]          # current text colour (menu icons follow it); set by the window on theme changes


POPUP_COLORS = {"bg": QColor("#1c1a36"), "stroke": QColor(255, 255, 255, 36)}   # set by the window per theme


class _PopupPainter(QObject):
    """See-through windows don't get their stylesheet background painted (Qt skips it for translucent
    top-level windows), which left bare text or a black box. This paints the rounded card first."""
    def eventFilter(self, obj, e):
        if e.type() == QEvent.Paint and isinstance(obj, QWidget):
            p = QPainter(obj); p.setRenderHint(QPainter.Antialiasing)
            rad = obj.property("tv_radius") or 14
            r = QRectF(obj.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(POPUP_COLORS["stroke"], 1)); p.setBrush(POPUP_COLORS["bg"]); p.drawRoundedRect(r, rad, rad)
            p.end()
        return False


_POPUP_PAINTER: list = []


def paint_popup_background(w: QWidget, radius: int = 14) -> None:
    if not _POPUP_PAINTER:
        _POPUP_PAINTER.append(_PopupPainter(QApplication.instance()))
    w.setProperty("tv_radius", radius); w.installEventFilter(_POPUP_PAINTER[0])


def set_popup_colors(pal: Palette) -> None:
    POPUP_COLORS["bg"] = QColor("#1c1a36" if pal.dark else "#ffffff")
    POPUP_COLORS["stroke"] = QColor(255, 255, 255, 40) if pal.dark else QColor(120, 110, 170, 70)
    POPUP_COLORS["mute"] = pal.mute


def prepare_popup(w: QWidget, name: str = "", radius: int = 14) -> QWidget:
    """Make a popup window rounded and see-through. Must run BEFORE it is first shown: changing window
    flags on a visible window hides it (that made tooltips vanish and dropdowns flash black)."""
    w.setWindowFlags(w.windowFlags() | POPUP_FLAGS)
    w.setAttribute(Qt.WA_TranslucentBackground)
    if name:
        w.setObjectName(name); w.style().unpolish(w); w.style().polish(w)
    paint_popup_background(w, radius)
    return w


class TipBubble(QLabel):
    """The app's own tooltip: a rounded bubble that fades in (replaces Qt's square tooltip window)."""
    def __init__(self):
        super().__init__(None, Qt.ToolTip | POPUP_FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("tipbubble"); self.setWordWrap(False); self.setTextFormat(Qt.PlainText)
        paint_popup_background(self, 10)
        self._hide = QTimer(self); self._hide.setSingleShot(True); self._hide.timeout.connect(self.hide)
        self.owner = None

    def show_tip(self, text: str, gpos: QPoint, owner) -> None:
        if not text:
            self.hide(); return
        same = self.isVisible() and self.owner is owner and self.text() == text
        self.owner = owner; self.setText(text); self.adjustSize()
        scr = QApplication.screenAt(gpos) or QApplication.primaryScreen(); g = scr.availableGeometry()
        x = min(max(g.left() + 4, gpos.x() + 12), g.right() - self.width() - 4)
        y = gpos.y() + 22
        if y + self.height() > g.bottom():
            y = gpos.y() - self.height() - 10
        self.move(x, y)
        if not same:
            self.show(); _strip_win_popup_frame(self); animate_popup_in(self, ms=140)
        self._hide.start(10000)


_EDIT_ICONS = {"undo": "ph.arrow-counter-clockwise-bold", "redo": "ph.arrow-clockwise-bold", "cut": "ph.scissors-fill",
               "copy": "ph.copy-fill", "paste": "ph.clipboard-text-fill", "delete": "ph.trash-fill",
               "select all": "ph.selection-all-fill", "clear": "ph.eraser-fill"}


def add_action(menu: QMenu, text: str, icon_name: str, fn=None):
    """Menu entry with a solid icon in the menu's text colour."""
    # dy: the text's visual middle (its capitals) sits a little above the row's middle, so the icon does too
    a = menu.addAction(QIcon(centred_pixmap(icon_name, TEXT[0], 16, dy=-1.0)), text)
    if fn is not None:
        a.triggered.connect(lambda *_: fn())
    return a


class _PopupFilter(QObject):
    """App-wide: rounded tooltips, rounded edit-field menus (with icons), DWM frame removal on menus."""
    def __init__(self, app):
        super().__init__(app); self.tip = TipBubble()

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.ToolTip and isinstance(obj, QWidget):
            text = ""
            from PySide6.QtWidgets import QAbstractItemView
            view = obj.parentWidget() if obj.parentWidget() is not None else None
            if isinstance(view, QAbstractItemView) and obj is view.viewport():
                ix = view.indexAt(e.pos()); text = str(ix.data(Qt.ToolTipRole) or "") if ix.isValid() else ""
            else:
                w = obj
                while w is not None and not w.toolTip() and not w.isWindow():
                    w = w.parentWidget()
                text = w.toolTip() if w is not None else ""
            self.tip.show_tip(text, e.globalPos(), obj)
            return True
        if t in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.Wheel, QEvent.KeyPress, QEvent.Hide) and self.tip.isVisible():
            if t != QEvent.Leave or obj is self.tip.owner:
                self.tip.hide()
        # middle-click and drag scrolls any list (the further you drag, the further it goes)
        if t == QEvent.MouseButtonPress and e.button() == Qt.MiddleButton and isinstance(obj, QWidget):
            from PySide6.QtWidgets import QAbstractScrollArea
            w = obj
            while w is not None and not (isinstance(w, QAbstractScrollArea) and w.verticalScrollBar().maximum() > 0):
                w = w.parentWidget()
            if w is not None:
                self._pan = (w, e.globalPosition().y(), w.verticalScrollBar().value())
                QApplication.setOverrideCursor(Qt.SizeVerCursor); return True
        if t == QEvent.MouseMove and getattr(self, "_pan", None) is not None:
            w, y0, v0 = self._pan
            try:
                w.verticalScrollBar().setValue(int(v0 + (e.globalPosition().y() - y0) * 2))
            except RuntimeError:
                self._pan = None
            return True
        if t == QEvent.MouseButtonRelease and e.button() == Qt.MiddleButton and getattr(self, "_pan", None) is not None:
            self._pan = None; QApplication.restoreOverrideCursor(); return True
        if t == QEvent.Polish:
            from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit
            if isinstance(obj, QLineEdit) and not obj.property("noclear") and not getattr(obj, "_tv_clear", None) \
                    and not isinstance(obj.parentWidget(), QAbstractSpinBox) and not obj.isReadOnly():
                add_clear_button(obj)
        if t == QEvent.Enter and isinstance(obj, QWidget) and not obj.testAttribute(Qt.WA_SetCursor):
            from PySide6.QtWidgets import QAbstractButton, QComboBox, QSlider, QTabBar
            if isinstance(obj, (QAbstractButton, QComboBox, QSlider, QTabBar)):   # everything clickable shows the hand
                obj.setCursor(Qt.PointingHandCursor)
        if t in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.Leave) and isinstance(obj, QFrame) \
                and obj.objectName() in ("hpill", "nowcard", "grow", "cmd", "opt"):
            down = t == QEvent.MouseButtonPress
            if bool(obj.property("down")) != down:
                obj.setProperty("down", down); obj.style().unpolish(obj); obj.style().polish(obj); obj.update()
        if t == QEvent.ContextMenu:
            from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit
            if isinstance(obj, QAbstractButton) and obj.contextMenuPolicy() == Qt.DefaultContextMenu:
                return True                       # a plain button (e.g. the calendar button in the date box): no menu
            par = obj.parentWidget() if isinstance(obj, QWidget) else None
            if isinstance(obj, QAbstractSpinBox) or isinstance(par, QAbstractSpinBox):
                return True                       # date/time boxes have pickers; their "Step up/down" menu isn't useful
            target = obj
            if not isinstance(target, (QLineEdit, QPlainTextEdit, QTextEdit)):
                par = obj.parentWidget() if isinstance(obj, QWidget) else None      # text edits get it on their viewport
                target = par if isinstance(par, (QPlainTextEdit, QTextEdit)) else None
            if target is not None:
                std = target.createStandardContextMenu()
                m = styled_menu(target)
                for a in std.actions():
                    if a.isSeparator():
                        m.addSeparator(); continue
                    name = a.text().replace("&", "").split("\t")[0].strip()
                    key = name.lower()
                    act = add_action(m, name, _EDIT_ICONS.get(key, "ph.dot-outline-fill"))
                    act.setEnabled(a.isEnabled()); act.triggered.connect(a.trigger)
                    if a.shortcut().toString():
                        act.setShortcut(a.shortcut())
                m.exec(e.globalPos()); std.deleteLater(); m.deleteLater()
                return True
        return False


class _ClearBtn(QObject):
    """Keeps a text box's clear button the same distance from the right edge as the text is from the left."""
    def __init__(self, edit, btn):
        super().__init__(edit); self.edit, self.btn = edit, btn; edit.installEventFilter(self)

    def place(self) -> None:
        from PySide6.QtWidgets import QStyle, QStyleOptionFrame
        e = self.edit; opt = QStyleOptionFrame(); e.initStyleOption(opt)
        inner = e.style().subElementRect(QStyle.SE_LineEditContents, opt, e)
        left = max(6, inner.left() + e.textMargins().left())      # where the text starts on the left
        b = self.btn
        b.move(e.width() - left - b.width() + 2, (e.height() - b.height()) // 2)

    def eventFilter(self, o, ev):
        if ev.type() in (QEvent.Resize, QEvent.Show, QEvent.StyleChange):
            self.place()
        return False


def add_clear_button(edit) -> None:
    """A small × at the end of a text box that empties it; only shown while there's text. It sits as far
    from the right edge as the text starts from the left edge."""
    from PySide6.QtWidgets import QToolButton
    b = QToolButton(edit); b.setCursor(Qt.PointingHandCursor); b.setToolTip("Clear"); b.setFocusPolicy(Qt.NoFocus)
    b.setIcon(QIcon(centred_pixmap("ph.x-circle-fill", POPUP_COLORS.get("mute", "#9b98b8"), 16)))
    b.setIconSize(QSize(16, 16)); b.setFixedSize(20, 20)
    b.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0; }")
    edit._tv_clear = b; placer = _ClearBtn(edit, b)
    m = edit.textMargins(); edit.setTextMargins(m.left(), m.top(), m.right() + 24, m.bottom())   # text never runs under it

    def clear():
        edit.clear(); edit.setFocus()
        try:
            edit.textEdited.emit("")
        except RuntimeError:
            pass
    b.clicked.connect(clear)
    b.setVisible(bool(edit.text())); edit.textChanged.connect(lambda t: (b.setVisible(bool(t)), placer.place()))


def install_apple_tooltips(app: QApplication) -> None:
    app.setEffectEnabled(Qt.UI_AnimateTooltip, False)
    app.setEffectEnabled(Qt.UI_FadeTooltip, False)
    app.setEffectEnabled(Qt.UI_AnimateMenu, False)
    app.setEffectEnabled(Qt.UI_FadeMenu, False)
    app.setEffectEnabled(Qt.UI_AnimateCombo, False)
    if getattr(app, "_tv_tip_filter", None) is None:
        flt = _PopupFilter(app)
        app._tv_tip_filter = flt
        app.installEventFilter(flt)
        app.aboutToQuit.connect(lambda: app.removeEventFilter(flt))     # quiet shutdown


class _RoundedMenu(QMenu):
    def showEvent(self, e):
        super().showEvent(e)
        _strip_win_popup_frame(self)
        animate_popup_in(self, ms=150)

    def addMenu(self, *a):                              # sub-menus get the same rounded window
        if len(a) == 1 and isinstance(a[0], str):
            sub = styled_menu(self, a[0]); super().addMenu(sub); return sub
        return super().addMenu(*a)


def styled_menu(parent: Optional[QWidget] = None, title: str = "") -> QMenu:
    """Rounded, translucent popup menu without Windows' rectangular DWM shadow box."""
    m = _RoundedMenu(title, parent)
    m.setWindowFlags(Qt.Popup | POPUP_FLAGS)
    m.setAttribute(Qt.WA_TranslucentBackground)
    f = QFont(QApplication.instance().font()); f.setPixelSize(13); f.setWeight(QFont.DemiBold)
    f.setHintingPreference(QFont.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy(QFont.PreferAntialias | QFont.NoSubpixelAntialias | QFont.PreferQuality))
    m.setFont(f)
    return m


class StyledComboBox(QComboBox):
    """ComboBox whose list opens as the app's rounded menu (the native drop-down window kept painting a
    square panel behind its rounded card on Windows)."""
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(40)
        self._menu: Optional[QMenu] = None

    def mousePressEvent(self, e):
        """Open/close our own menu directly (QComboBox's built-in toggle doesn't know about it)."""
        if e.button() == Qt.LeftButton and self.isEnabled():
            if self._menu is not None and self._menu.isVisible():
                self._menu.close()
            else:
                self.showPopup()
            e.accept(); return
        super().mousePressEvent(e)

    def showPopup(self) -> None:
        if popup_recently_closed(self):             # clicking the box while its list is open closes it
            return
        m = styled_menu(self); m.setObjectName("combomenu"); track_popup(self, m)
        m.setMinimumWidth(self.width())
        for i in range(self.count()):
            a = m.addAction(self.itemIcon(i), self.itemText(i))
            a.setCheckable(True); a.setChecked(i == self.currentIndex())
            a.triggered.connect(lambda _=False, i=i: self.setCurrentIndex(i))
        acts = m.actions()
        m.aboutToHide.connect(lambda: QTimer.singleShot(0, self.hidePopup))
        self._menu = m
        below = self.mapToGlobal(QPoint(0, self.height() + 6))
        m.popup(below, acts[self.currentIndex()] if 0 <= self.currentIndex() < len(acts) and False else None)

    def hidePopup(self) -> None:
        if self._menu is not None and self._menu.isVisible():
            self._menu.close()
        self._menu = None
        super().hidePopup()


# ---------------------------------------------------------------- small pieces
def glass(obj: str = "glass") -> QFrame:
    f = QFrame(); f.setObjectName(obj); return f


def label(text: str, obj: str = "", size: Optional[int] = None, weight: Optional[int] = None) -> QLabel:
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    css = ""
    if size: css += f"font-size: {size}px;"
    if weight: css += f"font-weight: {weight};"
    if css: l.setStyleSheet(css)
    return l


def button(text: str, obj: str = "ghost", icon_name: str = "", pal: Optional[Palette] = None, checkable=False) -> QPushButton:
    b = QPushButton(text); b.setObjectName(obj); b.setCursor(Qt.PointingHandCursor); b.setCheckable(checkable)
    if icon_name and pal is not None:
        b.setProperty("tv_icon", icon_name)
        col = "white" if obj in ("primary", "danger") else pal.icon
        b.setIcon(QIcon(centred_pixmap(icon_name, col, 15))); b.setIconSize(QSize(15, 15))
        if text:
            b.setText(" " + text)
    return b


def refresh_icons(root: QWidget, pal: Palette) -> None:
    """Re-tint every button icon after a theme change."""
    for b in root.findChildren(QPushButton):
        name = b.property("tv_icon")
        if name:
            white = b.objectName() == "primary" or (b.isCheckable() and b.isChecked())
            b.setIcon(QIcon(centred_pixmap(name, "white" if white else pal.icon, 15)))


STATUS_COLORS = {"Downloading": INFO, "Waiting for TikWM": INFO, "Queued": GREY, "Original": OK, "Compressed": WARN,
                 "Failed": BAD, "Already saved": OK, "Skipped": GREY, "Listing posts": INFO, "Paused": WARN, "Deleted": GREY}


class Pill(QLabel):
    def __init__(self, text: str, color: str, width: Optional[int] = None, height: int = 22):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter); self.setFixedHeight(height)
        if width:
            self.setFixedWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.set_color(color)

    def set_color(self, color: str) -> None:
        c = QColor(color)
        f = QFont(self.font()); f.setPixelSize(11); f.setWeight(QFont.ExtraBold)
        lift = max(0, round(2 * cap_offset(f)))          # capitals centred in the pill, not the text box
        self.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},0.16); color: {color};"
                           f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.45); border-radius: {self.height() // 2}px;"
                           f"padding: 0 10px {lift}px 10px; font-size: 11px; font-weight: 800;")


class StatusPill(Pill):
    def __init__(self, state: str = "Queued"):
        super().__init__(state, STATUS_COLORS.get(state, GREY), width=138)

    def set_state(self, state: str) -> None:
        self.setText(state); self.set_color(STATUS_COLORS.get(state, GREY))


class Badge(QWidget):
    """Gradient circle with the number centred on the digit's own shape."""
    def __init__(self, n: str, accent: tuple):
        super().__init__(); self.n, self.accent = n, accent; self.setFixedSize(20, 20)

    def set_text(self, n: str) -> None:
        self.n = n; self.setVisible(bool(n) and n != "0"); self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 20, 20); g.setColorAt(0, QColor(self.accent[0])); g.setColorAt(1, QColor(self.accent[1]))
        p.setPen(Qt.NoPen); p.setBrush(g); p.drawEllipse(QRectF(0, 0, 20, 20))
        f = QFont("Geist"); f.setHintingPreference(QFont.PreferNoHinting); f.setPixelSize(11 if len(self.n) < 3 else 9); f.setWeight(QFont.Bold)
        path = QPainterPath(); path.addText(0, 0, f, self.n); br = path.boundingRect()
        path.translate(10 - br.center().x(), 10 - br.center().y()); p.fillPath(path, QColor("white"))


class Switch(QAbstractButton):
    """Toggle whose knob glides across on the iOS-style curve."""
    def __init__(self, on: bool = False, accent: tuple = ("#8b5cf6", "#ec4899")):
        super().__init__(); self.setCheckable(True); self.setChecked(on); self.accent = accent
        self.setFixedSize(40, 22); self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if on else 0.0
        self.anim = QVariantAnimation(self); self.anim.setDuration(300); self.anim.setEasingCurve(apple_curve())
        self.anim.valueChanged.connect(lambda v: (setattr(self, "_pos", float(v)), self.update()))
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        if not (MOTION[0] and self.isVisible()):
            self._pos = 1.0 if on else 0.0; self.update(); return
        self.anim.stop(); self.anim.setStartValue(self._pos); self.anim.setEndValue(1.0 if on else 0.0); self.anim.start()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath(); path.addRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        p.fillPath(path, QColor(150, 150, 180, 80))
        if self._pos > 0:
            g = QLinearGradient(0, 0, 40, 0); g.setColorAt(0, QColor(self.accent[0])); g.setColorAt(1, QColor(self.accent[1]))
            p.setOpacity(self._pos); p.fillPath(path, g); p.setOpacity(1.0)
        p.setPen(Qt.NoPen); p.setBrush(QColor("white")); p.drawEllipse(QPointF(11 + 18 * self._pos, 11), 8, 8)

    def sizeHint(self):
        return QSize(40, 22)


ACCENT = ["#8b5cf6", "#ec4899"]
MOTION = [True]


def fade_in(w: QWidget, ms: int = 240, rise: int = 0) -> None:
    """The new content fades in (nothing is layered on top, so there's no flash)."""
    if not MOTION[0] or w is None or not w.isVisible():
        return
    from PySide6.QtCore import QEasingCurve, QPropertyAnimation
    from PySide6.QtWidgets import QGraphicsOpacityEffect
    old = getattr(w, "_fade_anim", None)
    if old is not None:
        old.stop()
    fx = QGraphicsOpacityEffect(w); fx.setOpacity(0.0); w.setGraphicsEffect(fx)
    a = QPropertyAnimation(fx, b"opacity", w); a.setDuration(ms); a.setStartValue(0.0); a.setEndValue(1.0)
    a.setEasingCurve(QEasingCurve.OutCubic)

    def done():
        try:
            if w.graphicsEffect() is fx:
                w.setGraphicsEffect(None)       # drop the effect afterwards: full-speed painting again
        except RuntimeError:
            pass
    a.finished.connect(done); w._fade_anim = a; a.start()


def popup_recently_closed(owner, within: float = 0.3) -> bool:
    """True if this owner's popup closed a moment ago - i.e. the click that closed it was on the owner,
    so the owner should not open it again (clicking an open dropdown's button closes it)."""
    import time as _t
    return _t.monotonic() - getattr(owner, "_popup_closed_at", 0.0) < within


def track_popup(owner, popup) -> None:
    """Remember when `popup` (a QMenu or popup window) closes, for popup_recently_closed(owner)."""
    import time as _t

    def mark(*_):
        # Only when it closed because the owner itself was clicked (not after picking an option),
        # otherwise the next click on the owner would be swallowed and need a second click.
        from PySide6.QtGui import QCursor
        try:
            if QApplication.mouseButtons() & Qt.LeftButton and owner.rect().contains(owner.mapFromGlobal(QCursor.pos())):
                owner._popup_closed_at = _t.monotonic()
            else:
                owner._popup_closed_at = 0.0
        except RuntimeError:
            pass
    if isinstance(popup, QMenu):
        popup.aboutToHide.connect(mark); return

    class _F(QObject):
        def eventFilter(self, o, e):
            if e.type() == QEvent.Hide:
                mark()
            return False
    f = _F(popup); popup.installEventFilter(f); popup._track_filter = f


def crossfade(w: QWidget, ms: int = 260, rise: int = 10) -> None:
    """Soft hand-over when a widget's content changes: a snapshot of the old content fades out
    (drifting up a little) over the new one. Call it just before changing the content."""
    if not MOTION[0] or w is None or not w.isVisible() or w.width() < 2:
        return
    from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation
    from PySide6.QtWidgets import QGraphicsOpacityEffect
    old = getattr(w, "_xfade", None)
    if old is not None:
        try:
            old.deleteLater()
        except RuntimeError:
            pass
    snap = QLabel(w); snap.setPixmap(w.grab()); snap.setGeometry(w.rect()); snap.setAttribute(Qt.WA_TransparentForMouseEvents)
    fx = QGraphicsOpacityEffect(snap); fx.setOpacity(1.0); snap.setGraphicsEffect(fx); snap.show(); snap.raise_()
    w._xfade = snap
    g = QParallelAnimationGroup(snap)
    a = QPropertyAnimation(fx, b"opacity", g); a.setDuration(ms); a.setStartValue(1.0); a.setEndValue(0.0); a.setEasingCurve(QEasingCurve.OutCubic)
    m = QPropertyAnimation(snap, b"pos", g); m.setDuration(ms); m.setStartValue(QPoint(0, 0)); m.setEndValue(QPoint(0, -rise)); m.setEasingCurve(QEasingCurve.OutCubic)
    g.addAnimation(a); g.addAnimation(m); g.finished.connect(snap.deleteLater); g.start()


def apple_curve() -> QEasingCurve:
    """iOS-style ease-out: quick to start, long gentle settle (cubic-bezier 0.32, 0.72, 0, 1)."""
    c = QEasingCurve(QEasingCurve.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.32, 0.72), QPointF(0.0, 1.0), QPointF(1.0, 1.0))
    return c


class _SegCount(QWidget):
    """The count circle inside a segmented-control option."""
    H = 18

    def __init__(self, btn, group):
        super().__init__(btn); self.btn, self.group, self.text = btn, group, ""
        self.setAttribute(Qt.WA_TransparentForMouseEvents); btn.installEventFilter(self)

    def set_text(self, t: str) -> None:
        f = QFont("Geist"); f.setPixelSize(10); f.setWeight(QFont.ExtraBold)
        from PySide6.QtGui import QFontMetricsF
        self.text = t; self.setFixedSize(int(max(self.H, QFontMetricsF(f).horizontalAdvance(t) + 10)), self.H); self.place(); self.show(); self.update()

    def place(self) -> None:
        self.move(self.btn.width() - self.width() - 10, (self.btn.height() - self.height()) // 2)

    def eventFilter(self, o, e):
        if e.type() == QEvent.Resize:
            self.place()
        return False

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        on = self.group.value == next((k for k, b in self.group.buttons.items() if b is self.btn), None)
        a = QColor(ACCENT[0])
        if on:
            bg, fg = QColor(255, 255, 255, 60), QColor("white")
        else:
            dark = QColor(TEXT[0]).lightness() > 128
            bg = QColor(a); bg.setAlpha(70 if dark else 40); fg = a.lighter(150) if dark else a.darker(115)
        r = QRectF(self.rect()); path = QPainterPath(); path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, bg)
        f = QFont("Geist"); f.setHintingPreference(QFont.PreferNoHinting); f.setPixelSize(10); f.setWeight(QFont.ExtraBold)
        p.setFont(f); p.setPen(fg); p.drawText(r, Qt.AlignCenter, self.text)


class SegGroup(QFrame):
    """Segmented control with smooth gradient highlight."""
    changed = Signal(str)
    DURATION = 360

    def __init__(self, options, selected: str = ""):
        super().__init__(); self.setObjectName("seggroup")
        self.h = QHBoxLayout(self); self.h.setContentsMargins(4, 4, 4, 4); self.h.setSpacing(2)
        self.buttons: dict[str, QPushButton] = {}
        for o in options:
            b = QPushButton(o); b.setObjectName("seg"); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, o=o: self.select(o, True))
            self.h.addWidget(b); self.buttons[o] = b
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._rect = QRectF()
        self._from = QRectF(); self._to = QRectF()
        self.anim = QVariantAnimation(self); self.anim.setStartValue(0.0); self.anim.setEndValue(1.0)
        self.anim.setDuration(self.DURATION); self.anim.setEasingCurve(apple_curve())
        self.anim.valueChanged.connect(self._step)
        self.value = ""
        self.select(selected or (options[0] if options else ""), False)

    def _target(self) -> QRectF:
        b = self.buttons.get(self.value)
        return QRectF(b.geometry()) if b is not None else QRectF()

    def _step(self, t) -> None:
        t = float(t); a, b = self._from, self._to
        self._rect = QRectF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t,
                            a.width() + (b.width() - a.width()) * t, a.height() + (b.height() - a.height()) * t)
        self.update()

    def _place(self, animate: bool) -> None:
        t = self._target()
        if t.width() <= 0:
            return
        if self.anim.state() == QVariantAnimation.Running and not animate:
            self._to = t
            return
        if animate and MOTION[0] and self.isVisible() and self._rect.width() > 0:
            self.anim.stop(); self._from = QRectF(self._rect); self._to = t; self.anim.start()
        else:
            self.anim.stop(); self._rect = t; self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._rect.width() <= 0:
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(self._rect.topLeft(), self._rect.topRight())
        g.setColorAt(0, QColor(ACCENT[0])); g.setColorAt(1, QColor(ACCENT[1]))
        path = QPainterPath(); path.addRoundedRect(self._rect, 9, 9); p.fillPath(path, g)

    def select(self, o: str, emit: bool = True) -> None:
        if o not in self.buttons:
            return
        changed = o != self.value
        self.value = o
        for k, b in self.buttons.items():
            b.setProperty("on", k == o); b.style().unpolish(b); b.style().polish(b)
            if getattr(b, "_count", None) is not None:
                b._count.update()
        self._place(animate=changed)
        if emit:
            self.changed.emit(o)

    def set_count(self, key: str, n: int) -> None:
        """A number in a circle after the option's name (like the creator chips)."""
        b = self.buttons.get(key)
        if b is None:
            return
        badge = getattr(b, "_count", None)
        if badge is None:
            badge = _SegCount(b, self); b._count = badge
        if badge.text != str(n) or not badge.isVisible():
            badge.set_text(str(n))
            b.setStyleSheet(f"padding-right: {badge.width() + 18}px;")
            QTimer.singleShot(0, lambda: (badge.place(), self._place(False)))

    def set_label(self, key: str, text: str) -> None:
        if key in self.buttons and self.buttons[key].text() != text:
            self.buttons[key].setText(text)
            QTimer.singleShot(0, lambda: self._place(False))

    def resizeEvent(self, e):
        super().resizeEvent(e); QTimer.singleShot(0, lambda: self._place(False))

    def showEvent(self, e):
        super().showEvent(e); QTimer.singleShot(0, lambda: self._place(False))


class Thumb(QWidget):
    """Rounded gradient tile with initials, or a cover image once one is loaded."""
    def __init__(self, seed: str, text: str, w: int = 46, h: int = 58, radius: int = 10):
        super().__init__()
        self.setFixedSize(w, h); self.text, self.radius = text, radius; self.pix: Optional[QPixmap] = None
        self.colors = tile_colors(seed)

    def set_pixmap(self, pm: Optional[QPixmap]) -> None:
        self.pix = pm; self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath(); path.addRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        p.setClipPath(path)
        if self.pix and not self.pix.isNull():
            s = self.pix.size().scaled(self.size(), Qt.KeepAspectRatioByExpanding)
            p.drawPixmap(QRectF((self.width() - s.width()) / 2, (self.height() - s.height()) / 2, s.width(), s.height()),
                         self.pix, QRectF(self.pix.rect()))
            return
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor(self.colors[0])); g.setColorAt(1, QColor(self.colors[1])); p.fillPath(path, g)
        shine = QLinearGradient(0, 0, 0, self.height())
        shine.setColorAt(0, QColor(255, 255, 255, 70)); shine.setColorAt(0.5, QColor(255, 255, 255, 0)); p.fillPath(path, shine)
        p.setPen(QColor(255, 255, 255, 235)); f = QFont("Geist"); f.setHintingPreference(QFont.PreferNoHinting); f.setPixelSize(max(9, int(min(self.width(), self.height()) * 0.3)))
        f.setWeight(QFont.Bold); p.setFont(f); p.drawText(self.rect(), Qt.AlignCenter, self.text)


TILE_PAIRS = [("#f472b6", "#8b5cf6"), ("#38bdf8", "#6366f1"), ("#fb923c", "#ec4899"), ("#34d399", "#06b6d4"),
              ("#a78bfa", "#f472b6"), ("#fbbf24", "#ec4899"), ("#22c55e", "#06b6d4"), ("#fb923c", "#f43f5e")]


def tile_colors(seed: str) -> tuple:
    return TILE_PAIRS[sum(map(ord, seed or "x")) % len(TILE_PAIRS)]


def initials(name: str) -> str:
    n = (name or "?").lstrip("@_.")
    parts = [x for x in n.replace(".", " ").replace("_", " ").split() if x]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return n[:2].upper() or "?"


class HeaderPill(QFrame):
    clicked = Signal()
    hovered = Signal(bool)

    def __init__(self, icon_name: str, text: str, pal: Palette, dot: Optional[str] = None):
        super().__init__(); self.setObjectName("hpill"); self.setFixedHeight(30); self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        h = QHBoxLayout(self); h.setContentsMargins(13, 0, 13, 0); h.setSpacing(7); h.addStretch(1)   # content stays centred
        self.ic = None
        self.dot = None
        if icon_name:
            self.ic = IconLabel(icon_name, pal.icon, 15); h.addWidget(self.ic, 0, Qt.AlignVCenter)
        if dot:
            self.dot = QLabel(); self.dot.setFixedSize(8, 8); h.addWidget(self.dot, 0, Qt.AlignVCenter); self.set_dot(dot)
        self.lbl = QLabel(text); self.lbl.setStyleSheet("font-weight: 800; font-size: 12px;"); h.addWidget(self.lbl, 0, Qt.AlignVCenter)
        h.addStretch(1)
        self.icon_name = icon_name

    def set_dot(self, color: str) -> None:
        if self.dot:
            self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def restyle(self, pal: Palette) -> None:
        if self.ic:
            self.ic.set_icon(self.icon_name, pal.icon, 15)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def enterEvent(self, e):
        self.hovered.emit(True)

    def leaveEvent(self, e):
        self.hovered.emit(False)


class StatusCard(QFrame):
    """Glass hover card under the Online pill with Apple-style opacity dissolve."""
    def __init__(self, parent):
        super().__init__(parent); self.setObjectName("tip"); self.hide()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._eff = QGraphicsOpacityEffect(self)
        self._eff.setOpacity(0.0)
        self.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"opacity", self)
        self._anim.setEasingCurve(apple_curve())
        self._anim.finished.connect(self._on_anim_done)
        self._showing = False

        v = QVBoxLayout(self); v.setContentsMargins(14, 10, 14, 12); v.setSpacing(3)
        h = QHBoxLayout(); h.setSpacing(8)
        self.dot = QLabel(); self.dot.setFixedSize(8, 8); h.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.head = QLabel("Checking TikWM…"); self.head.setStyleSheet("font-weight: 800; font-size: 12px;"); h.addWidget(self.head); h.addStretch()
        v.addLayout(h)
        self.body = QVBoxLayout(); self.body.setSpacing(3); v.addLayout(self.body)
        self.set_lines("Checking TikWM…", ["Looking for TikWM - this takes a few seconds"], GREY)

    def reveal(self) -> None:
        self._showing = True
        self._anim.stop()
        if not MOTION[0]:
            self._eff.setOpacity(1.0)
            self.show()
            self.raise_()
            return
        self.show()
        self.raise_()
        self._anim.setDuration(190)
        self._anim.setStartValue(self._eff.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def conceal(self) -> None:
        self._showing = False
        self._anim.stop()
        if not MOTION[0] or not self.isVisible():
            self._eff.setOpacity(0.0)
            self.hide()
            return
        self._anim.setDuration(140)
        self._anim.setStartValue(self._eff.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_done(self) -> None:
        if not self._showing:
            self.hide()

    def set_lines(self, head: str, lines: list, color: str) -> None:
        self.head.setText(head); self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        while self.body.count():
            w = self.body.takeAt(0).widget()
            if w:
                w.deleteLater()
        for t in lines:
            self.body.addWidget(label(t, "mute", 11))
        self.adjustSize()


class Toast(QFrame):
    """Floating card in the bottom-right corner."""
    MAX_TEXT = 440

    def __init__(self, parent: QWidget, pal: Palette):
        super().__init__(parent); self.setObjectName("toast"); self.hide()
        h = QHBoxLayout(self); h.setContentsMargins(14, 12, 14, 12); h.setSpacing(12)
        ic = QLabel(); ic.setPixmap(app_icon_pixmap(32)); ic.setFixedSize(32, 32); h.addWidget(ic)
        v = QVBoxLayout(); v.setSpacing(2)
        self.title = QLabel(); self.title.setStyleSheet("font-weight: 800; font-size: 13px;"); v.addWidget(self.title)
        self.body = label("", "mute", 11); self.body.setWordWrap(True); v.addWidget(self.body); h.addLayout(v)
        h.addSpacing(8)
        self.action = button("", "ghost"); h.addWidget(self.action, 0, Qt.AlignVCenter)
        self.close_btn = button("", "ghost"); self.close_btn.setFixedSize(30, 30); self.close_btn.setToolTip("Dismiss")
        self.close_btn.setIcon(QIcon(centred_pixmap("ph.x-bold", pal.text, 14))); self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.clicked.connect(self.dismiss)
        h.addWidget(self.close_btn, 0, Qt.AlignVCenter)
        self._cb: Optional[Callable] = None
        self.action.clicked.connect(self._act)
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.timeout.connect(self.dismiss)
        self.anchor: Optional[QWidget] = None
        self._fx = QGraphicsOpacityEffect(self); self._fx.setOpacity(0.999); self.setGraphicsEffect(self._fx)
        self._anim = QVariantAnimation(self); self._anim.setEasingCurve(apple_curve())
        self._anim.valueChanged.connect(self._step); self._anim.finished.connect(self._done)
        self._leaving = False; self._home = QPoint()

    # arrive: slide in from the right and fade in (ease out); leave: fade out in place (ease in)
    def _step(self, v) -> None:
        v = float(v); self._fx.setOpacity(min(v, 0.999))
        dx = 0 if self._leaving else round((1 - v) * 48)
        self.move(self._home.x() + dx, self._home.y())

    def _done(self) -> None:
        if self._leaving:
            self._leaving = False; QFrame.hide(self); self._fx.setOpacity(0.999)

    def _run(self, start: float, end: float, ms: int) -> None:
        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.InCubic if end < start else QEasingCurve.OutCubic)
        if not MOTION[0]:
            self._step(end); self._done(); return
        self._anim.setDuration(ms); self._anim.setStartValue(start); self._anim.setEndValue(end); self._anim.start()

    def dismiss(self) -> None:
        if not self.isVisible() or self._leaving:
            return
        self._timer.stop(); self._leaving = True; self._run(self._fx.opacity(), 0.0, 220)

    def set_pal(self, pal: Palette) -> None:
        self.close_btn.setIcon(QIcon(centred_pixmap("ph.x-bold", pal.text, 14)))

    def show_message(self, title: str, body: str, action: str = "", cb: Optional[Callable] = None, ms: int = 7000) -> None:
        self.title.setText(title); self.body.setText(body)
        self.action.setVisible(bool(action)); self.action.setText(action); self._cb = cb
        self.ensurePolished()
        for w in (self.title, self.body, self.action, self.close_btn):
            w.ensurePolished()
        for l in (self.title, self.body):
            widest = max((l.fontMetrics().horizontalAdvance(t) for t in (l.text() or " ").split("\n")), default=0)
            l.setFixedWidth(min(widest + 6, self.MAX_TEXT))
        self.setMinimumSize(0, 0); self.layout().invalidate(); self.layout().activate()
        self.resize(self.layout().sizeHint()); was = self.isVisible() and not self._leaving
        self._leaving = False; self.place(); self.show(); self.raise_()
        if not was:
            self._run(0.0, 1.0, 320)
        else:
            self._anim.stop(); self._step(1.0)
        if ms:
            self._timer.start(ms)

    def _act(self) -> None:
        cb = self._cb; self.dismiss()
        if cb:
            cb()

    def place(self) -> None:
        par = self.parentWidget()
        if not par:
            return
        if self.anchor is not None and self.anchor.isVisible():
            br = self.anchor.mapTo(par, self.anchor.rect().bottomRight())
            x, y = br.x() - self.width() - 20, br.y() - self.height() - 18
        else:
            x, y = par.width() - self.width() - 38, par.height() - self.height() - 36
        self._home = QPoint(max(20, x), max(20, y)); self.move(self._home)


class Card(QFrame):
    """Glass card with a vertical layout (and standard padding)."""
    def __init__(self, obj: str = "glass", margins=(20, 16, 20, 16), spacing: int = 12):
        super().__init__(); self.setObjectName(obj)
        self.v = QVBoxLayout(self); self.v.setContentsMargins(*margins); self.v.setSpacing(spacing)


class EmptyBox(QWidget):
    """Empty-state placeholder: centred text inside a smooth dashed rounded outline."""
    clicked = Signal()

    def __init__(self, text: str, pal: Palette, accent: bool = False, size: int = 11):
        super().__init__(); self.text, self.pal, self.accent, self.size = text, pal, accent, size
        self._hover = self._down = False
        self.inset = 0          # vertical inset of the outline inside the widget

    def _clickable(self) -> bool:
        try:
            return self.isSignalConnected(self.metaObject().method(self.metaObject().indexOfSignal("clicked()")))
        except Exception:
            return True

    def enterEvent(self, e):
        if self._clickable():
            self._hover = True; self.setCursor(Qt.PointingHandCursor); self.update()

    def leaveEvent(self, e):
        self._hover = self._down = False; self.update()

    def mouseReleaseEvent(self, e):
        self._down = False; self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.75, 0.75 + self.inset, -0.75, -0.75 - self.inset)
        pen = QPen(QColor(self.pal.mute), 2.0 if self.accent else 1.2); pen.setDashPattern([4, 3]); pen.setCapStyle(Qt.RoundCap)
        c = QColor(ACCENT[0] if self.accent else self.pal.mute); c.setAlpha(230 if self.accent else 110); pen.setColor(c)
        if self.accent:
            r = r.adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover:                                    # clickable boxes turn purple under the cursor
            c = QColor(ACCENT[0]).lighter(135) if self.pal.dark else QColor(ACCENT[0]); pen.setColor(c)
            fill = QColor(ACCENT[0]); fill.setAlpha(70 if self._down else 34); p.setBrush(fill)
        else:
            p.setBrush(Qt.NoBrush)
        p.setPen(pen); p.drawRoundedRect(r, 14 if self.accent else 12, 14 if self.accent else 12)
        f = QFont(self.font()); f.setPixelSize(self.size); f.setWeight(QFont.Bold if self.accent else QFont.DemiBold); p.setFont(f)
        hov = QColor(ACCENT[0]).lighter(135) if self.pal.dark else QColor(ACCENT[0])
        p.setPen(hov if self._hover else QColor(self.pal.mute))
        p.drawText(self.rect().adjusted(8, 4, -8, -4), Qt.AlignCenter | Qt.TextWordWrap, self.text)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._down = True; self.update(); self.clicked.emit()


def optical_centre(lab: QLabel) -> None:
    """Shift a label so the middle of its capitals (not its text box) sits on the row's centre line."""
    lab.ensurePolished()
    d = cap_offset(lab.font())
    lab.setContentsMargins(0, max(0, round(-2 * d)), 0, max(0, round(2 * d)))


def cap_offset(font: QFont) -> float:
    """How far below a text box's centre the middle of its capital letters sits (negative = above)."""
    from PySide6.QtGui import QFontMetricsF
    fm = QFontMetricsF(font)
    return (fm.ascent() - fm.capHeight() - fm.descent()) / 2


def hline() -> QFrame:
    f = QFrame(); f.setObjectName("sep"); f.setFixedHeight(1); return f


# ---------------------------------------------------------------- glass modal confirmation dialog
class ConfirmDialog(QDialog):
    """Frameless glass confirmation / notice dialog matching TikVortex's theme."""

    def __init__(self, parent: Optional[QWidget], title: str, body: str, pal: Palette,
                 confirm_text: str = "Yes", cancel_text: str = "No", danger: bool = False):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self._drag_pos: Optional[QPoint] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        bg = Backdrop(pal, radius=18)
        bg.set_motion(False)
        outer.addWidget(bg)

        v = QVBoxLayout(bg)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(app_icon_pixmap(24))
        ic.setFixedSize(24, 24)
        top.addWidget(ic, 0, Qt.AlignVCenter)
        ttl = QLabel(title)
        ttl.setStyleSheet("font-size: 14px; font-weight: 900;")
        top.addWidget(ttl, 1, Qt.AlignVCenter)
        v.addLayout(top)

        msg = label(body, "sub", 12)
        msg.setWordWrap(True)
        msg.setMinimumWidth(340)
        msg.setMaximumWidth(440)
        v.addWidget(msg)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        if cancel_text:
            no_btn = button(cancel_text, "ghost")
            no_btn.setMinimumHeight(34)
            no_btn.clicked.connect(self.reject)
            btns.addWidget(no_btn)
        yes_btn = button(confirm_text, "danger" if danger else "primary")
        yes_btn.setMinimumHeight(34)
        yes_btn.setDefault(True)
        yes_btn.clicked.connect(self.accept)
        btns.addWidget(yes_btn)
        v.addLayout(btns)

        self.adjustSize()

    def showEvent(self, e):
        super().showEvent(e)
        _strip_win_popup_frame(self)
        animate_popup_in(self, ms=165)
        par = self.parentWidget().window() if self.parentWidget() else None
        if par is not None and par.isVisible():
            g = par.frameGeometry()
            self.move(g.center() - self.rect().center())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    @classmethod
    def ask(cls, parent: Optional[QWidget], title: str, body: str, pal: Palette,
            confirm_text: str = "Yes", cancel_text: str = "No", danger: bool = False) -> bool:
        dlg = cls(parent, title, body, pal, confirm_text=confirm_text, cancel_text=cancel_text, danger=danger)
        return dlg.exec() == QDialog.Accepted

    @classmethod
    def info(cls, parent: Optional[QWidget], title: str, body: str, pal: Palette, ok_text: str = "OK") -> None:
        dlg = cls(parent, title, body, pal, confirm_text=ok_text, cancel_text="", danger=False)
        dlg.exec()


# ---------------------------------------------------------------- option boxes, date & time picker
def _repolish(w: QWidget) -> None:
    w.style().unpolish(w); w.style().polish(w); w.update()


class _Dot(QWidget):
    """Round selector: an empty ring, or a gradient disc with a white centre when chosen."""
    def __init__(self):
        super().__init__(); self.setFixedSize(20, 20); self.on = False; self.ring = "#9aa0c3"

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1.5, 1.5, 17, 17)
        if self.on:
            g = QLinearGradient(r.topLeft(), r.bottomRight()); g.setColorAt(0, QColor(ACCENT[0])); g.setColorAt(1, QColor(ACCENT[1]))
            p.setPen(Qt.NoPen); p.setBrush(g); p.drawEllipse(r)
            p.setBrush(QColor("white")); p.drawEllipse(QPointF(10, 10), 3.6, 3.6)
        else:
            c = QColor(self.ring); c.setAlpha(170); p.setPen(QPen(c, 1.6)); p.setBrush(Qt.NoBrush); p.drawEllipse(r)


class OptionCard(QFrame):
    """One of a set of selectable boxes: selector dot, title, description and optional controls (self.body)."""
    picked = Signal()

    def __init__(self, title: str, sub: str, pal: Palette, inline: bool = False):
        super().__init__(); self.setObjectName("opt"); self.setCursor(Qt.PointingHandCursor)
        self.v = QVBoxLayout(self); self.v.setContentsMargins(14, 13, 14, 14); self.v.setSpacing(10)
        self.v.addStretch(1)
        top = QHBoxLayout(); top.setSpacing(11)
        self.dot = _Dot(); self.dot.ring = pal.mute; top.addWidget(self.dot, 0, Qt.AlignTop)
        col = QVBoxLayout(); col.setSpacing(3)
        t = QLabel(title); t.setStyleSheet("font-weight: 800; font-size: 13px;"); col.addWidget(t)
        s = label(sub, "mute", 11); s.setWordWrap(True); col.addWidget(s)
        top.addLayout(col, 1); self.v.addLayout(top)
        if inline:
            self.body = QHBoxLayout(); self.body.setContentsMargins(12, 0, 0, 0); self.body.setSpacing(8); top.addLayout(self.body)
            self.dot.setParent(None); top.insertWidget(0, self.dot, 0, Qt.AlignVCenter)
        else:
            self.body = QVBoxLayout(); self.body.setContentsMargins(31, 0, 0, 0); self.body.setSpacing(8); self.v.addLayout(self.body)
        self.v.addStretch(1)
        self.selected = False

    def set_selected(self, on: bool) -> None:
        self.selected = on; self.setProperty("on", on); self.dot.on = on; self.dot.update(); _repolish(self)

    def restyle(self, pal: Palette) -> None:
        self.dot.ring = pal.mute; self.dot.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.picked.emit()


class OptionSet:
    """Keeps a row of OptionCards mutually exclusive."""
    def __init__(self, cards: dict, selected: str, on_change: Optional[Callable] = None):
        self.cards, self.on_change, self.value = cards, on_change, None
        for k, c in cards.items():
            c.picked.connect(lambda k=k: self.select(k))
        self.select(selected, False)

    def select(self, key: str, notify: bool = True) -> None:
        if key == self.value:
            return
        self.value = key
        for k, c in self.cards.items():
            c.set_selected(k == key)
        if notify and self.on_change:
            self.on_change(key)


class WheelColumn(QWidget):
    """iOS-style drum: scroll, drag or click a row; it glides and settles on the middle row."""
    valueChanged = Signal(int)
    ROW, ROWS = 36, 5

    def __init__(self, count: int, value: int, labels: Optional[list[str]] = None, wrap: bool = True, width: int = 66):
        super().__init__()
        self.count = count
        self.labels = labels
        self.wrap = wrap
        self.pos = float(value)
        self._drag = None
        self._moved = False
        self.setFixedSize(width, self.ROW * self.ROWS)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.WheelFocus)
        self.anim = QVariantAnimation(self)
        self.anim.setEasingCurve(apple_curve())
        self.anim.setDuration(260)
        self.anim.valueChanged.connect(self._at)
        self.color = "#eef0ff"

    def _clamp(self, v: float) -> float:
        return v if self.wrap else max(0.0, min(float(self.count - 1), v))

    def value(self) -> int:
        idx = int(round(self.pos))
        return (idx % self.count) if self.wrap else max(0, min(self.count - 1, idx))

    def set_value(self, v: int) -> None:
        self.anim.stop(); self.pos = self._clamp(float(v)); self.update()

    def _at(self, v) -> None:
        old = self.value(); self.pos = self._clamp(float(v)); self.update()
        if self.value() != old:
            self.valueChanged.emit(self.value())

    def _glide(self, target: float) -> None:
        self.anim.stop(); self.anim.setStartValue(self.pos); self.anim.setEndValue(self._clamp(float(target))); self.anim.start()

    def wheelEvent(self, e):
        steps = -e.angleDelta().y() / 120.0
        base = self.anim.endValue() if self.anim.state() == QVariantAnimation.Running else self.pos
        self._glide(round(float(base) + (1 if steps > 0 else -1) * max(1, round(abs(steps)))))
        e.accept()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Up, Qt.Key_Down):
            self._glide(round(self.pos) + (-1 if e.key() == Qt.Key_Up else 1))
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        self.anim.stop(); self._drag = (e.position().y(), self.pos); self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag:
            dy = e.position().y() - self._drag[0]
            if abs(dy) > 3:
                self._moved = True
            self._at(self._drag[1] - dy / self.ROW)

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        self._drag = None
        if self._moved:
            self._glide(round(self.pos))
        else:
            self._glide(round(self.pos) + round((e.position().y() - self.height() / 2) / self.ROW))

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.TextAntialiasing)
        mid = self.height() / 2.0; base = math.floor(self.pos); frac = self.pos - base
        f = QFont(self.font()); f.setFamily("Geist"); f.setWeight(QFont.Bold)
        for k in range(-3, 4):
            raw_idx = base + k
            if not self.wrap and (raw_idx < 0 or raw_idx >= self.count):
                continue
            y = mid + (k - frac) * self.ROW; d = abs(y - mid) / (self.ROW * 2.5)
            if d >= 1:
                continue
            idx = raw_idx % self.count
            text = self.labels[idx] if self.labels else f"{idx:02d}"
            f.setPixelSize(int((18 if self.labels and not text.isdigit() else 20) - 5 * d)); p.setFont(f)
            c = QColor(self.color); c.setAlphaF(max(0.0, 1.0 - d * 0.85)); p.setPen(c)
            p.drawText(QRectF(0, y - self.ROW / 2, self.width(), self.ROW), Qt.AlignCenter, text)


class TimeWheelPopup(QFrame):
    """Pop-up with 12-hour, minute, and AM/PM drums plus an explicit Confirm button."""
    confirmed = Signal(int, int)

    def __init__(self, h24: int, m: int, pal: Palette):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground); self.pal = pal

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(10)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        hours_12 = ["12"] + [f"{i:02d}" for i in range(1, 12)]
        self.h = WheelColumn(12, h24 % 12, labels=hours_12, wrap=True, width=62)
        self.m = WheelColumn(60, m, wrap=True, width=62)
        self.ampm = WheelColumn(2, 1 if h24 >= 12 else 0, labels=["AM", "PM"], wrap=False, width=58)
        for w in (self.h, self.m, self.ampm):
            w.color = pal.text

        colon = QLabel(":")
        colon.setStyleSheet("font-weight: 900; font-size: 20px; background: transparent;")
        row.addWidget(self.h)
        row.addWidget(colon)
        row.addWidget(self.m)
        row.addSpacing(4)
        row.addWidget(self.ampm)
        outer.addLayout(row)

        ok = button("Confirm", "primary")
        ok.setMinimumHeight(34)
        ok.clicked.connect(self._confirm)
        outer.addWidget(ok)

    def _confirm(self) -> None:
        h24 = (self.h.value() % 12) + (12 if self.ampm.value() == 1 else 0)
        self.confirmed.emit(h24, self.m.value())
        self.close()

    def showEvent(self, e):
        super().showEvent(e)
        _strip_win_popup_frame(self)
        animate_popup_in(self, ms=160)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 40) if self.pal.dark else QColor(120, 110, 170, 60), 1))
        p.setBrush(QColor("#1a1833" if self.pal.dark else "#ffffff")); p.drawRoundedRect(r, 16, 16)
        band_y = self.h.geometry().center().y() - WheelColumn.ROW / 2.0
        band = QRectF(10, band_y, self.width() - 20, WheelColumn.ROW)
        c = QColor(ACCENT[0]); c.setAlpha(60); p.setPen(Qt.NoPen); p.setBrush(c); p.drawRoundedRect(band, 10, 10)


class TimeField(QPushButton):
    """Shows 12-hour HH:MM AM/PM like a text field; clicking opens the drums."""
    changed = Signal()

    def __init__(self, h: int, m: int, pal: Palette):
        super().__init__(); self.setObjectName("timefield"); self.setCursor(Qt.PointingHandCursor); self.pal = pal
        self.h, self.m = h, m; self.setFixedWidth(138); self.setToolTip("Click to pick a time, or scroll to change the minutes")
        self._pop: Optional[TimeWheelPopup] = None
        self.clicked.connect(self._open); self._sync()

    def set_time(self, h: int, m: int) -> None:
        if (h, m) != (self.h, self.m):
            self.h, self.m = h % 24, m % 60; self._sync(); self.changed.emit()

    def _sync(self) -> None:
        h12 = (self.h % 12) or 12
        ampm = "PM" if self.h >= 12 else "AM"
        self.setText(f"{h12:02d} : {self.m:02d} {ampm}"); self.setIcon(QIcon())

    def wheelEvent(self, e):
        step = 1 if e.angleDelta().y() < 0 else -1
        total = (self.h * 60 + self.m + step) % (24 * 60); self.set_time(total // 60, total % 60); e.accept()

    def _open(self) -> None:
        if popup_recently_closed(self):
            return
        if self._pop is not None:
            self._pop.close()
        pop = TimeWheelPopup(self.h, self.m, self.pal); track_popup(self, pop)
        pop.confirmed.connect(self.set_time)
        pop.adjustSize()
        g = self.mapToGlobal(self.rect().bottomLeft())
        pop.move(g.x() + (self.width() - pop.width()) // 2, g.y() + 6)
        self._pop = pop
        pop.show()
        pop.h.setFocus()


class RoundCalendar(QCalendarWidget):
    """The picked date is a rounded square, the same shape as the hover highlight."""
    def paintCell(self, p, rect, date):
        if date != self.selectedDate():
            return super().paintCell(p, rect, date)
        p.save(); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(rect).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen); p.setBrush(QColor(ACCENT[0])); p.drawRoundedRect(r, 9, 9)
        f = QFont(self.font()); f.setWeight(QFont.Black); p.setFont(f); p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignCenter, str(date.day())); p.restore()


class YearWheelPopup(QFrame):
    """The calendar's year picker: one drum, like the time wheel."""
    confirmed = Signal(int)
    FIRST, LAST = 2006, 2040

    def __init__(self, year: int, pal: Palette):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground); self.pal = pal
        v = QVBoxLayout(self); v.setContentsMargins(16, 12, 16, 14); v.setSpacing(10)
        years = list(range(self.FIRST, self.LAST + 1))
        self.y = WheelColumn(len(years), min(max(year, self.FIRST), self.LAST) - self.FIRST,
                             labels=[str(y) for y in years], wrap=False, width=120)
        self.y.color = pal.text; v.addWidget(self.y, 0, Qt.AlignHCenter)
        ok = button("Confirm", "primary"); ok.setMinimumHeight(34); ok.clicked.connect(self._confirm); v.addWidget(ok)

    def _confirm(self) -> None:
        self.confirmed.emit(self.FIRST + self.y.value()); self.close()

    def showEvent(self, e):
        super().showEvent(e); _strip_win_popup_frame(self); animate_popup_in(self, ms=160)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 40) if self.pal.dark else QColor(120, 110, 170, 60), 1))
        p.setBrush(QColor("#1a1833" if self.pal.dark else "#ffffff")); p.drawRoundedRect(r, 16, 16)
        band = QRectF(10, self.y.geometry().center().y() - WheelColumn.ROW / 2.0, self.width() - 20, WheelColumn.ROW)
        c = QColor(ACCENT[0]); c.setAlpha(60); p.setPen(Qt.NoPen); p.setBrush(c); p.drawRoundedRect(band, 10, 10)


class CalendarPopup(QFrame):
    """Rounded calendar popup (set up as a see-through window before it's ever shown)."""
    picked = Signal(object)

    def __init__(self, cal):
        super().__init__(None, Qt.Popup | POPUP_FLAGS); self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("calpopup"); paint_popup_background(self, 16)
        v = QVBoxLayout(self); v.setContentsMargins(10, 10, 10, 10); v.addWidget(cal); self.cal = cal
        cal.clicked.connect(lambda d: (self.picked.emit(d), self.close()))
        cal.activated.connect(lambda d: (self.picked.emit(d), self.close()))

    def showEvent(self, e):
        super().showEvent(e); _strip_win_popup_frame(self); animate_popup_in(self, ms=160)


class DateTimePicker(QWidget):
    """Date field (with a small glass calendar button inside it), 12-hour time field, and a Now button.
    All three are the same height."""
    changed = Signal()
    opened = Signal()            # the calendar or time picker was opened
    H = 42

    def __init__(self, pal: Palette):
        super().__init__()
        from PySide6.QtCore import QDate, QTime
        from PySide6.QtWidgets import QCalendarWidget, QDateEdit
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        self.date = QDateEdit(QDate.currentDate()); self.date.setCalendarPopup(False); self.date.setDisplayFormat("ddd d MMM yyyy")
        self.date.setObjectName("datefield")   # (NoButtons collapses the text area to 1 px; arrows are hidden in the stylesheet)
        self.date.setMinimumWidth(176); self.date.setFixedHeight(self.H)
        self.date.lineEdit().setTextMargins(12, 0, 40, 0)     # inset like other fields; room for the calendar button
        cal = RoundCalendar(); cal.setGridVisible(False); cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        cal.setHorizontalHeaderFormat(QCalendarWidget.SingleLetterDayNames); cal.setMinimumSize(300, 250)
        cal.setCursor(Qt.PointingHandCursor)
        self.cal = cal
        self.pop = CalendarPopup(cal); self.pop.picked.connect(self.date.setDate)

        # the calendar button lives inside the date field, on the right
        self.cal_btn = button("", "calbtn", "ph.calendar-blank-fill", pal)
        self.cal_btn.setParent(self.date); self.cal_btn.setFixedSize(32, 30); self.cal_btn.setCursor(Qt.PointingHandCursor)
        self.cal_btn.setToolTip("Open calendar"); self.cal_btn.clicked.connect(self._open_calendar)
        self.date.installEventFilter(self)

        now = QTime.currentTime()
        self.time = TimeField(now.hour(), now.minute(), pal); self.time.setFixedHeight(self.H)
        h.addWidget(self.date, 1); h.addWidget(self.time)
        self.now = button("Now", "ghost", "ph.clock-fill", pal); self.now.setToolTip("Use the current date and time")
        self.now.setStyleSheet("font-size: 13px; font-weight: 800;")          # same type as the gradient buttons
        self.now.setFixedHeight(self.H); self.now.setMinimumWidth(84)
        self.now.clicked.connect(self.set_now); h.addWidget(self.now)
        self.time.clicked.connect(self.opened.emit)
        for w in (self.date.dateChanged, self.time.changed):
            w.connect(lambda *_: self.changed.emit())
        self.pal = pal
        from PySide6.QtWidgets import QToolButton, QTableView
        self._yb = cal.findChild(QToolButton, "qt_calendar_yearbutton")
        if self._yb is not None:                   # the year opens a drum instead of Qt's spin box
            self._yb.installEventFilter(self); self._yb.setFocusPolicy(Qt.NoFocus)
        mb = cal.findChild(QToolButton, "qt_calendar_monthbutton")
        if mb is not None:
            mb.setFocusPolicy(Qt.NoFocus)
            if mb.menu() is not None:
                mb.menu().aboutToHide.connect(lambda b=mb: QTimer.singleShot(0, lambda: self._unhover(b)))
        for tv in cal.findChildren(QTableView):
            tv.setMouseTracking(True); tv.viewport().setAttribute(Qt.WA_Hover, True)
        bar = cal.findChild(QWidget, "qt_calendar_navigationbar")
        if bar is not None:
            bar.setAttribute(Qt.WA_StyledBackground, True)
        self.restyle(pal)

    def _unhover(self, b) -> None:
        if b is not None:
            b.setAttribute(Qt.WA_UnderMouse, False); b.setDown(False); b.clearFocus(); b.update()

    def _open_year(self) -> None:
        pop = YearWheelPopup(self.cal.yearShown(), self.pal)
        pop.setAttribute(Qt.WA_DeleteOnClose)
        pop.destroyed.connect(lambda *_, b=self._yb: self._unhover(b))
        pop.confirmed.connect(lambda y: self.cal.setCurrentPage(y, self.cal.monthShown()))
        pop.adjustSize(); g = self._yb.mapToGlobal(QPoint(self._yb.width() // 2, self._yb.height() + 4))
        pop.move(g.x() - pop.width() // 2, g.y()); self._ypop = pop; pop.show(); pop.y.setFocus()

    def _place_btn(self) -> None:
        b = self.cal_btn; self.cal_btn.move(self.date.width() - b.width() - 6, (self.date.height() - b.height()) // 2)

    def _open_calendar(self) -> None:
        if popup_recently_closed(self):
            return
        if not getattr(self, "_cal_tracked", False):
            track_popup(self, self.pop); self._cal_tracked = True
        self.opened.emit()
        self.cal.setSelectedDate(self.date.date()); self.pop.adjustSize()
        g = self.date.mapToGlobal(QPoint(0, self.date.height() + 6))
        self.pop.move(g.x() + self.date.width() - self.pop.width(), g.y()); self.pop.show()

    def eventFilter(self, obj, e):
        if obj is self.date and e.type() in (QEvent.Resize, QEvent.Show):
            self._place_btn()
        if obj is getattr(self, "_yb", None) and e.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.MouseButtonRelease):
            if e.type() == QEvent.MouseButtonRelease:
                self._open_year()
            return True
        return super().eventFilter(obj, e)

    def set_active(self, on: bool) -> None:
        eff = self.graphicsEffect()
        if eff is None:
            eff = QGraphicsOpacityEffect(self); self.setGraphicsEffect(eff)
        eff.setOpacity(1.0 if on else 0.45)

    def set_now(self) -> None:
        from PySide6.QtCore import QDateTime
        n = QDateTime.currentDateTime()
        self.date.setDate(n.date()); self.time.set_time(n.time().hour(), n.time().minute())

    def text(self) -> str:
        return f"{self.date.date().toString('yyyy-MM-dd')} {self.time.h:02d}:{self.time.m:02d}"

    def restyle(self, pal: Palette) -> None:
        from PySide6.QtGui import QTextCharFormat
        self.time.pal = pal; self.pal = pal; self.time._sync()
        for d in (Qt.Saturday, Qt.Sunday):                    # no red weekends; colour comes from the palette (so other months grey out)
            self.cal.setWeekdayTextFormat(d, QTextCharFormat())
        head = QTextCharFormat(); head.setForeground(QColor(pal.accent[1] if pal.dark else pal.accent[0])); head.setFontWeight(QFont.Black)
        self.cal.setHeaderTextFormat(head)
        today = QTextCharFormat(); today.setForeground(QColor(pal.accent[1] if pal.dark else pal.accent[0])); today.setFontWeight(QFont.Black)
        from PySide6.QtCore import QDate
        self.cal.setDateTextFormat(QDate(), QTextCharFormat()); self.cal.setDateTextFormat(QDate.currentDate(), today)
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QTableView
        for tv in self.cal.findChildren(QTableView):               # the day grid paints its own (solid) background
            pl = tv.palette(); c = QColor("#1c1a36" if pal.dark else "#ffffff")
            for role in (QPalette.Base, QPalette.Window, QPalette.AlternateBase):
                pl.setColor(role, c)
            pl.setColor(QPalette.Text, QColor(pal.text))
            grey = QColor(pal.text); grey.setAlphaF(0.32); pl.setColor(QPalette.Disabled, QPalette.Text, grey)
            tv.setPalette(pl)
            tv.viewport().setAutoFillBackground(True); tv.viewport().setPalette(pl)
        bar = self.cal.findChild(QWidget, "qt_calendar_navigationbar")
        if bar is not None:
            solid = QColor("#262350" if pal.dark else "#f1edfc")
            pl = bar.palette(); pl.setColor(QPalette.Highlight, solid); pl.setColor(QPalette.Window, solid)
            pl.setColor(QPalette.HighlightedText, QColor(pal.text)); pl.setColor(QPalette.ButtonText, QColor(pal.text))
            bar.setPalette(pl); bar.setAutoFillBackground(False)


class GroupRow(QFrame):
    """A creator group in the Tools editor: tile, name, accounts."""
    clicked = Signal()

    def __init__(self, name: str, accounts: list, pal: Palette):
        super().__init__(); self.setObjectName("grow"); self.setCursor(Qt.PointingHandCursor); self.name = name
        h = QHBoxLayout(self); h.setContentsMargins(10, 8, 12, 8); h.setSpacing(11)
        from .avatars import AvatarWidget
        h.addWidget(AvatarWidget("group", name, 36, accounts))
        v = QVBoxLayout(); v.setSpacing(1)
        t = QLabel(name); t.setStyleSheet("font-weight: 800; font-size: 13px;"); v.addWidget(t)
        n = len(accounts)
        shown = ", ".join("@" + a.lstrip("@") for a in accounts[:3]) + (f" +{n - 3}" if n > 3 else "")
        s = label("", "mute", 11); self._sub_full = f"{n} account{'s' if n != 1 else ''}  ·  {shown}"; self._sub = s
        s.setMinimumWidth(1); s.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred); v.addWidget(s); h.addLayout(v, 1)
        self.chev = IconLabel("ph.caret-right-bold", pal.mute, 12); h.addWidget(self.chev, 0, Qt.AlignVCenter)
        self.setToolTip("\n".join("@" + a.lstrip("@") for a in accounts))

    def resizeEvent(self, e):                     # long account lists end in "…" instead of being cut off
        super().resizeEvent(e)
        self._sub.setText(self._sub.fontMetrics().elidedText(self._sub_full, Qt.ElideRight, max(10, self._sub.width())))

    def set_selected(self, on: bool) -> None:
        self.setProperty("on", on); _repolish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()