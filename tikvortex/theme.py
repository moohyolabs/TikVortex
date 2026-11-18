"""Colour themes, light/dark palettes and the application stylesheet."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

OK, WARN, BAD, INFO, GREY = "#22c55e", "#f59e0b", "#ef4444", "#38bdf8", "#94a3b8"

# name -> (dark overrides, light overrides, accent pair dark, accent pair light)
COLOUR_THEMES = {
    "Vortex": (dict(bg0="#0b0d17", bg1="#141a33", blobA="#7c3aed", blobB="#ec4899", blobC="#06b6d4"),
               dict(bg0="#d9d8e5", bg1="#e4e1ec", blobA="#b3a7e3", blobB="#dcb4cc", blobC="#a3cbd6"),
               ("#8b5cf6", "#ec4899"), ("#8b5cf6", "#ec4899")),
    "Aurora": (dict(bg0="#06131a", bg1="#0b1f2a", blobA="#10b981", blobB="#06b6d4", blobC="#8b5cf6"),
               dict(bg0="#ecfbf6", bg1="#eef9fb", blobA="#6ee7b7", blobB="#67e8f9", blobC="#c4b5fd"),
               ("#10b981", "#06b6d4"), ("#059669", "#0891b2")),
    "Sunset": (dict(bg0="#1a0b10", bg1="#2a1218", blobA="#f97316", blobB="#ec4899", blobC="#facc15"),
               dict(bg0="#fff4ec", bg1="#fff0f4", blobA="#fdba74", blobB="#f9a8d4", blobC="#fde68a"),
               ("#f97316", "#ec4899"), ("#ea580c", "#db2777")),
    "Ocean": (dict(bg0="#07101f", bg1="#0b1a33", blobA="#2563eb", blobB="#06b6d4", blobC="#6366f1"),
              dict(bg0="#edf3ff", bg1="#eefaff", blobA="#93c5fd", blobB="#67e8f9", blobC="#a5b4fc"),
              ("#3b82f6", "#06b6d4"), ("#2563eb", "#0891b2")),
    "Midnight": (dict(bg0="#08080c", bg1="#111118", blobA="#3f3f46", blobB="#52525b", blobC="#27272a"),
                 dict(bg0="#f3f4f6", bg1="#fafafa", blobA="#d4d4d8", blobB="#e4e4e7", blobC="#d4d4d8"),
                 ("#52525b", "#71717a"), ("#3f3f46", "#71717a")),
    "Sakura": (dict(bg0="#1a0f17", bg1="#24121f", blobA="#f472b6", blobB="#fda4af", blobC="#c084fc"),
               dict(bg0="#fff0f6", bg1="#fbf0ff", blobA="#f9a8d4", blobB="#fecdd3", blobC="#e9d5ff"),
               ("#f472b6", "#c084fc"), ("#db2777", "#9333ea")),
}

DARK_BASE = dict(glass=(255, 255, 255, 0.07), glass2=(255, 255, 255, 0.11), stroke=(255, 255, 255, 0.14),
                 text="#eef0ff", mute="#9aa0c3", sub="#b6bbd8", field=(10, 12, 28, 0.55), track=(255, 255, 255, 0.10),
                 navsel=(255, 255, 255, 0.12), toast=(30, 28, 60, 0.94), tip=(24, 22, 48, 0.96), faded=(238, 240, 255, 0.32))
LIGHT_BASE = dict(glass=(255, 255, 255, 0.36), glass2=(255, 255, 255, 0.48), stroke=(70, 60, 120, 0.16),
                  text="#15172a", mute="#555b7a", sub="#454a66", field=(255, 255, 255, 0.55), track=(23, 26, 44, 0.08),
                  navsel=(255, 255, 255, 0.62), toast=(255, 255, 255, 0.94), tip=(255, 255, 255, 0.97), faded=(23, 26, 44, 0.38))

GLASS_STRENGTH = {"Strong": 1.0, "Subtle": 0.7, "Off": None}


def _rgba(c, mult=1.0, opaque_over=None):
    r, g, b, a = c
    if mult is None:
        base = QColor(opaque_over)
        a = min(1.0, a * 2.2)
        return QColor(int(r * a + base.red() * (1 - a)), int(g * a + base.green() * (1 - a)),
                      int(b * a + base.blue() * (1 - a))).name()
    return f"rgba({r},{g},{b},{min(1.0, a * mult):.3f})"


@dataclass
class Palette:
    name: str
    dark: bool
    accent: tuple
    bg0: str
    bg1: str
    blobA: str
    blobB: str
    blobC: str
    glass: str
    glass2: str
    stroke: str
    text: str
    mute: str
    sub: str
    field: str
    track: str
    navsel: str
    toast: str
    tip: str
    faded: str

    @property
    def icon(self) -> str:
        return self.text if self.dark else self.accent[0]

    @property
    def icon_dim(self) -> str:
        if self.dark:
            return self.mute
        c = QColor(self.accent[0])
        return QColor(int(c.red() + (255 - c.red()) * 0.3), int(c.green() + (255 - c.green()) * 0.3),
                      int(c.blue() + (255 - c.blue()) * 0.3)).name()

    @property
    def nav_icon(self) -> str:
        base = QColor(COLOUR_THEMES.get(self.name, COLOUR_THEMES["Vortex"])[3][0])
        if not self.dark:
            return base.name()
        h, s, l, _ = base.getHslF()
        base.setHslF(max(h, 0.0), min(1.0, s * 1.15), l + (1 - l) * 0.62)
        return base.name()

    @property
    def nav_text(self) -> str:
        return self.mute

    @property
    def link(self) -> str:
        return "#c4b5fd" if self.dark else self.accent[0]

    @property
    def wordmark(self) -> tuple:
        return ("#c4b5fd", "#f0abfc") if self.dark else ("#7c3aed", "#db2777")


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return True


def make_palette(colour: str, mode: str, glass: str = "Strong") -> Palette:
    dark = system_is_dark() if mode == "Auto" else mode == "Dark"
    dk, lt, acc_d, acc_l = COLOUR_THEMES.get(colour, COLOUR_THEMES["Vortex"])
    base = DARK_BASE if dark else LIGHT_BASE
    over = dk if dark else lt
    mult = GLASS_STRENGTH.get(glass, 1.0)
    bg = over["bg0"]
    return Palette(
        name=colour, dark=dark, accent=acc_d if dark else acc_l, **over,
        glass=_rgba(base["glass"], mult, bg), glass2=_rgba(base["glass2"], mult, bg),
        stroke=_rgba(base["stroke"]), text=base["text"], mute=base["mute"], sub=base["sub"],
        field=_rgba(base["field"], 1.0 if mult else None, bg), track=_rgba(base["track"]),
        navsel=_rgba(base["navsel"], mult, bg), toast=_rgba(base["toast"]), tip=_rgba(base["tip"]),
        faded=_rgba(base["faded"]),
    )


def _qss_icon(name: str, color: str, px: int = 14) -> str:
    """Render an icon to a small PNG (once per colour) so the stylesheet can use it for arrows."""
    import os
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "tikvortex-ui")
    path = os.path.join(folder, f"{name.replace('.', '_')}_{color.strip('#')}_{px}.png")
    if not os.path.isfile(path):
        try:
            import qtawesome
            os.makedirs(folder, exist_ok=True)
            qtawesome.icon(name, color=color).pixmap(px * 2, px * 2).save(path)
        except Exception:
            return ""
    return path.replace("\\", "/")


def stylesheet(p: Palette) -> str:
    a, b = p.accent
    up, down = _qss_icon("ph.caret-up-bold", p.mute), _qss_icon("ph.caret-down-bold", p.mute)
    prev, nxt = _qss_icon("ph.caret-left-bold", p.text), _qss_icon("ph.caret-right-bold", p.text)
    sel = QColor(a); sel.setAlphaF(0.14 if p.dark else 0.10)
    press = QColor(a); press.setAlphaF(0.30 if p.dark else 0.20)      # pressed buttons flash the accent
    solid = "#1a1833" if p.dark else "#ffffff"
    def _rgba(c, al):
        q = QColor(c); return f"rgba({q.red()}, {q.green()}, {q.blue()}, {al})"
    navbar = "#262350" if p.dark else "#f1edfc"                          # the month/year bar stands out from the grid
    nav_hover, day_hover = _rgba(a, 0.30 if p.dark else 0.16), _rgba(a, 0.22 if p.dark else 0.12)
    return f"""
    * {{ font-family: 'Geist'; color: {p.text}; outline: none; }}
    QWidget {{ font-size: 13px; }}
    QToolTip {{ background: {p.tip}; color: {p.text}; border: 1px solid {p.stroke}; border-radius: 9px; padding: 6px 10px; font-weight: 600; }}
    QLabel#tipbubble {{ background: {p.tip}; color: {p.text}; border: 1px solid {p.stroke}; border-radius: 10px; padding: 6px 11px; font-size: 12px; font-weight: 600; }}
    QLabel {{ background: transparent; }}
    QLabel#h2 {{ font-size: 15px; font-weight: 900; letter-spacing: 0.6px; }}
    QLabel#mute {{ color: {p.mute}; font-size: 12px; font-weight: 500; }}
    QLabel#sub {{ color: {p.sub}; font-size: 11px; font-weight: 500; }}
    QLabel#subtitle {{ color: {p.mute}; font-size: 12px; font-weight: 600; }}
    QLabel#heading {{ color: {p.mute}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
    QPushButton#maker {{ color: {p.mute}; font-size: 12px; font-weight: 800; letter-spacing: 0.3px; background: transparent;
        border: none; border-radius: 8px; padding: 4px 8px; }}
    QPushButton#maker:hover {{ color: {p.text}; background: {p.glass2}; }}
    QPushButton#maker:pressed {{ background: {press.name(QColor.HexArgb)}; }}
    QLabel#link {{ color: {p.link}; font-size: 10px; font-weight: 700; }}
    QLabel#emptybox {{ color: {p.mute}; font-size: 11px; font-weight: 600; border: 1px dashed {p.stroke}; border-radius: 12px; padding: 6px; }}
    QLabel#empty {{ color: {p.mute}; font-size: 13px; font-weight: 600; }}
    QFrame#glass {{ background: {p.glass}; border: 1px solid {p.stroke}; border-radius: 18px; }}
    QFrame#row {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 14px; }}
    QFrame#row[sel="true"] {{ border: 2px solid {a}; background: {nav_hover}; }}
    QFrame#nowcard {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 14px; }}
    QFrame#nowcard:hover {{ background: {sel.name(QColor.HexArgb)}; border: 1px solid {a}; }}
    QFrame#side {{ background: {p.glass}; border: 1px solid {p.stroke}; border-radius: 22px; }}
    QFrame#field {{ background: {p.field}; border: 1px solid {p.stroke}; border-radius: 12px; }}
    QFrame#toast {{ background: {p.toast}; border: 1px solid {p.stroke}; border-radius: 14px; }}
    QFrame#tip {{ background: {p.tip}; border: 1px solid {p.stroke}; border-radius: 12px; }}
    QFrame#cmd, QFrame#hpill {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 17px; }}
    QFrame#cmd:hover, QFrame#hpill:hover {{ border: 1px solid {a}; }}
    QFrame#hpill {{ border-radius: 15px; }}
    QFrame#seggroup {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 12px; }}
    QFrame#sep {{ background: {p.stroke}; }}
    QLineEdit, QPlainTextEdit, QSpinBox, QComboBox, QDateTimeEdit {{
        background: {p.field}; border: 1px solid {p.stroke}; border-radius: 12px; padding: 9px 12px;
        font-size: 13px; selection-background-color: {a}; selection-color: white; }}
    QLineEdit#bare {{ background: transparent; border: none; padding: 0 6px; font-size: 14px; }}
    QComboBox {{ padding: 9px 34px 9px 14px; font-weight: 600; }}
    QComboBox:hover {{ border: 1px solid {a}; }}
    QComboBox::drop-down {{ border: none; width: 32px; subcontrol-position: center right; }}
    QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
    QFrame#combopopup {{ background: transparent; border: none; padding: 0; }}
    QComboBoxPrivateScroller {{ background: transparent; border: none; }}
    QComboBox QAbstractItemView {{ background: transparent; border: none; outline: none; padding: 6px;
        selection-background-color: {a}; selection-color: white; }}
    QComboBox QAbstractItemView::item {{ padding: 8px 12px; border-radius: 8px; min-height: 22px; }}
    QAbstractSpinBox {{ padding-right: 26px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 24px;
        subcontrol-origin: border; margin-right: 4px; }}
    QAbstractSpinBox::up-button {{ subcontrol-position: top right; margin-top: 5px; }}
    QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 5px; }}
    QAbstractSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
    QAbstractSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
    QAbstractSpinBox::up-arrow:hover, QAbstractSpinBox::down-arrow:hover {{ width: 12px; height: 12px; }}
    QDateEdit {{ padding-right: 12px; font-weight: 600; }}
    QDateEdit:hover {{ border: 1px solid {a}; }}
    QDateEdit::drop-down {{ border: none; width: 0px; }}
    QDateEdit::down-arrow {{ image: none; width: 0px; }}
    QDateEdit#datefield::up-button, QDateEdit#datefield::down-button, QDateEdit#datefield::up-arrow, QDateEdit#datefield::down-arrow {{ width: 0px; height: 0px; image: none; }}
    QPushButton#calbtn {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 9px; padding: 0; }}
    QPushButton#calbtn:hover {{ border: 1px solid {a}; background: {a}; }}
    QPushButton#calbtn:pressed {{ background: {p.track}; }}
    QFrame#calpopup {{ background: {solid}; border: 1px solid {p.stroke}; border-radius: 16px; padding: 6px; }}
    QCalendarWidget, QCalendarWidget QWidget#qt_calendar_calendarview {{ background: transparent; border-radius: 12px; }}
    QCalendarWidget QWidget {{ alternate-background-color: transparent; }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {navbar}; border: 1px solid {p.stroke}; padding: 6px 4px; border-radius: 11px; }}
    QCalendarWidget QToolButton#qt_calendar_monthbutton, QCalendarWidget QToolButton#qt_calendar_yearbutton {{ color: {p.text}; background: transparent; }}
    QCalendarWidget QToolButton#qt_calendar_monthbutton:hover, QCalendarWidget QToolButton#qt_calendar_yearbutton:hover {{ background: {nav_hover}; }}
    QCalendarWidget QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 5px 9px;
        font-size: 13px; font-weight: 800; color: {p.text}; }}
    QCalendarWidget QToolButton:hover {{ background: {nav_hover}; }}
    QCalendarWidget QToolButton::menu-indicator {{ image: none; width: 0; }}
    QCalendarWidget QToolButton#qt_calendar_prevmonth {{ qproperty-icon: url({prev}); }}
    QCalendarWidget QToolButton#qt_calendar_nextmonth {{ qproperty-icon: url({nxt}); }}
    QCalendarWidget QSpinBox {{ padding: 3px 26px 3px 8px; border-radius: 8px; font-weight: 800; }}
    QCalendarWidget QAbstractItemView {{ background: transparent; border: none; outline: none; font-size: 12px; font-weight: 600;
        selection-background-color: transparent; selection-color: white; }}
    QCalendarWidget QAbstractItemView:disabled {{ color: {p.faded}; }}
    QCalendarWidget QAbstractItemView::item {{ border-radius: 9px; margin: 1px; }}
    QCalendarWidget QAbstractItemView::item:hover {{ background: {day_hover}; }}
    QCalendarWidget QAbstractItemView::item:selected {{ background: transparent; color: white; }}
    QDateEdit#datefield {{ font-size: 14px; font-weight: 700; }}
    QPushButton#timefield {{ background: {p.field}; border: 1px solid {p.stroke}; border-radius: 12px; padding: 9px 12px;
        font-size: 14px; font-weight: 700; text-align: center; }}
    QPushButton#timefield:hover {{ border: 1px solid {a}; }}
    QFrame#opt {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 14px; }}
    QFrame#opt:hover {{ border: 1px solid {QColor(a).name()}; }}
    QFrame#opt[on="true"] {{ background: {sel.name(QColor.HexArgb)}; border: 2px solid {a}; }}
    QFrame#grow {{ background: transparent; border: 1px solid transparent; border-radius: 12px; }}
    QFrame#grow:hover {{ background: {p.glass2}; }}
    QFrame#grow[on="true"] {{ background: {sel.name(QColor.HexArgb)}; border: 1px solid {a}; }}
    QPushButton {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 10px;
        padding: 6px 14px; font-size: 12px; font-weight: 700; color: {p.text}; }}
    QPushButton:hover {{ border: 1px solid {a}; }}
    QPushButton:disabled {{ color: {p.mute}; border: 1px solid {p.track}; background: {p.glass}; }}
    QPushButton#primary {{ border: none; border-radius: 12px; padding: 11px 22px; font-size: 13px; font-weight: 800; color: white;
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {a}, stop:1 {b}); }}
    QPushButton#primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {QColor(a).lighter(112).name()}, stop:1 {QColor(b).lighter(112).name()}); }}
    QPushButton#primary:disabled {{ background: {p.track}; color: {p.mute}; }}
    QPushButton#primary:pressed {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {QColor(a).darker(118).name()}, stop:1 {QColor(b).darker(118).name()}); }}
    QPushButton#ghost {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 10px;
        padding: 6px 12px; font-size: 12px; font-weight: 700; color: {p.text}; }}
    QPushButton#ghost:hover {{ border: 1px solid {a}; }}
    QPushButton#ghost:pressed, QPushButton#danger:pressed, QPushButton#chip:pressed {{ background: {press.name(QColor.HexArgb)}; }}
    QPushButton#ghost:disabled {{ color: {p.mute}; border: 1px solid {p.track}; background: {p.glass}; }}
    QPushButton#ghost:checked {{ border: 1px solid transparent; color: white;
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a}, stop:1 {b}); }}
    QPushButton#danger {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 10px;
        padding: 6px 12px; font-size: 12px; font-weight: 700; color: {BAD}; }}
    QPushButton#danger:hover {{ border: 1px solid {BAD}; }}
    QPushButton#danger:disabled {{ color: {p.mute}; border: 1px solid {p.track}; background: {p.glass}; }}
    QPushButton#nav {{ border: none; border-radius: 12px; background: transparent; }}
    QPushButton#nav:hover {{ background: {p.glass2}; }}
    QPushButton#nav:pressed {{ background: {p.navsel}; }}
    QPushButton#win:pressed {{ background: {p.track}; }}
    QPushButton#seg:pressed {{ color: {p.text}; }}
    QPushButton#navsel {{ border-radius: 12px; background: {p.navsel}; border: 1px solid {p.stroke}; }}
    QPushButton#seg {{ background: transparent; border: none; border-radius: 9px; padding: 5px 12px; font-size: 12px;
        color: {p.mute}; font-weight: 800; }}
    QPushButton#seg:hover {{ color: {p.text}; }}
    QPushButton#seg[on="true"] {{ color: white; }}
    QPushButton#win {{ background: transparent; border: none; border-radius: 0; }}
    QPushButton#win:hover {{ background: {p.glass2}; }}
    QPushButton#winclose:hover {{ background: #e81123; }}
    QPushButton#winclose {{ background: transparent; border: none; border-radius: 0; }}
    QPushButton#chip {{ background: {p.glass2}; border: 1px solid {p.stroke}; border-radius: 13px; padding: 4px 12px;
        font-size: 12px; font-weight: 700; }}
    QPushButton#chip:checked {{ border: 1px solid transparent; border-radius: 13px; color: white;
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a}, stop:1 {b}); }}
    QProgressBar {{ background: {p.track}; border: none; border-radius: 3px; }}
    QProgressBar::chunk {{ border-radius: 3px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a}, stop:1 {b}); }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {p.stroke}; border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {a}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
    QScrollBar::handle:horizontal {{ background: {p.stroke}; border-radius: 3px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
    QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {p.track}; }}
    QSlider::sub-page:horizontal {{ border-radius: 3px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {a}, stop:1 {b}); }}
    QSlider::handle:horizontal {{ background: white; width: 16px; margin: -5px 0; border-radius: 8px; border: 1px solid {p.stroke}; }}
    QListView, QListWidget {{ background: transparent; border: none; }}
    QMenu {{ background: {p.tip}; border: 1px solid {p.stroke}; border-radius: 12px; padding: 8px; }}
    QMenu::item {{ background: transparent; padding: 8px 26px 8px 14px; border-radius: 8px; font-size: 13px; font-weight: 600; }}
    QMenu::icon {{ padding-left: 12px; }}
    QMenu::right-arrow {{ image: url({nxt}); width: 10px; height: 10px; margin-right: 8px; }}
    QMenu::item:selected {{ background: {a}; color: white; }}
    QMenu#combomenu::indicator {{ width: 0; height: 0; }}
    QMenu#combomenu::item:checked {{ font-weight: 800; }}
    QMenu::separator {{ height: 1px; background: {p.stroke}; margin: 4px 6px; }}
    QFrame#hpill[down="true"], QFrame#nowcard[down="true"], QFrame#grow[down="true"], QFrame#cmd[down="true"], QFrame#opt[down="true"] {{
        background: {press.name(QColor.HexArgb)}; }}
    QCheckBox {{ font-size: 12px; font-weight: 600; spacing: 8px; }}
    QDialog#palette {{ background: {p.tip}; border: 1px solid {p.stroke}; border-radius: 16px; }}
    QMessageBox {{ background: {p.bg1}; }}
    """