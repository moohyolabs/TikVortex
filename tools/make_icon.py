"""Render the TikVortex app icon (tikvortex/assets/icon.png + icon.ico).

Three-arm white glass vortex on a violet -> pink tile, rotated so the empty
space in the four corners is balanced. Run:  python tools/make_icon.py
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)

PHASE = 2.563495408493621   # rotation that balances the corner gaps
RADIUS = 0.33               # vortex reach, as a fraction of the icon
WEIGHT = 1.0                # arm thickness multiplier

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tikvortex", "assets")


def _lerp(stops, t):
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if p0 <= t <= p1:
            k = (t - p0) / (p1 - p0 or 1)
            return QColor(*(int(a + (b - a) * k) for a, b in zip(c0.getRgb(), c1.getRgb())))
    return stops[-1][1]


def _arm(p, c, s, phase, turns, r0, r1, w0, w1, stops, steps=220):
    prev = None
    for i in range(steps + 1):
        t = i / steps
        ang = phase + t * turns * 2 * math.pi
        rad = r0 + (r1 - r0) * (t ** 1.15)
        pt = QPointF(c.x() + math.cos(ang) * rad * s, c.y() + math.sin(ang) * rad * s)
        if prev is not None:
            p.setPen(QPen(_lerp(stops, t), (w0 + (w1 - w0) * t) * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(prev, pt)
        prev = pt


def paint(p: QPainter, s: float) -> None:
    r = QRectF(s * 0.06, s * 0.06, s * 0.88, s * 0.88)
    path = QPainterPath(); path.addRoundedRect(r, s * 0.23, s * 0.23)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#7c3aed")); g.setColorAt(0.5, QColor("#a855f7")); g.setColorAt(1, QColor("#ec4899"))
    p.fillPath(path, g)
    p.save(); p.setClipPath(path); c = r.center()
    white = [(0, QColor(255, 255, 255, 255)), (0.8, QColor(255, 255, 255, 240)), (1, QColor(255, 255, 255, 0))]
    k = RADIUS / 0.40
    for a in range(3):
        _arm(p, c, s, a * 2 * math.pi / 3 + PHASE, 0.75, 0.02, RADIUS, 0.085 * WEIGHT, 0.03 * WEIGHT, white)
    gr = 0.12 * k
    glow = QRadialGradient(c, s * gr); glow.setColorAt(0, QColor(255, 255, 255, 230)); glow.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.NoPen); p.setBrush(glow); p.drawEllipse(c, s * gr, s * gr)
    p.restore()
    top = QPainterPath(); top.addRoundedRect(QRectF(r.left(), r.top(), r.width(), r.height() * 0.5), s * 0.23, s * 0.23)
    sh = QLinearGradient(0, r.top(), 0, r.top() + r.height() * 0.5)
    sh.setColorAt(0, QColor(255, 255, 255, 100)); sh.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(top.intersected(path), sh)
    p.setPen(QPen(QColor(255, 255, 255, 130), max(1.0, s * 0.012))); p.setBrush(Qt.NoBrush); p.drawPath(path)


def render(size: int) -> QImage:
    big = size * 4
    img = QImage(big, big, QImage.Format_ARGB32); img.fill(Qt.transparent)
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing); paint(p, big); p.end()
    return img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    os.makedirs(ASSETS, exist_ok=True)
    render(512).save(os.path.join(ASSETS, "icon.png"))
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    tmp = []
    for n in sizes:
        fn = os.path.join(ASSETS, f"_icon_{n}.png"); render(n).save(fn); tmp.append(fn)
    try:
        from PIL import Image
        imgs = [Image.open(f) for f in tmp]
        imgs[-1].save(os.path.join(ASSETS, "icon.ico"), sizes=[(n, n) for n in sizes], append_images=imgs[:-1])
    finally:
        for f in tmp:
            os.remove(f)
    print("wrote", os.path.join(ASSETS, "icon.png"), "and icon.ico")


if __name__ == "__main__":
    main()
