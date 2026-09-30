"""Dark Fusion theme and shared colors."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

ACCENT = QColor("#3d7eff")
BG = QColor("#1f2124")
BASE = QColor("#16171a")
PANEL = QColor("#26282c")
TEXT = QColor("#e4e6eb")
MUTED = QColor("#9aa0a8")

TL_BG = QColor("#121316")
TL_GRID = QColor("#2a2d33")
TL_KEEP = QColor(63, 185, 80, 70)
TL_KEEP_EDGE = QColor(63, 185, 80, 200)
TL_OFF = QColor(140, 140, 140, 45)
TL_SCORE = QColor(111, 168, 255, 110)
TL_SCORE_EDGE = QColor(140, 190, 255)
TL_THRESHOLD = QColor("#ff5c5c")
TL_BLANK = QColor("#4c6ef5")
TL_PLAYHEAD = QColor("#ffd166")

OK = QColor("#3fb950")
WARN = QColor("#e3b341")
ERROR = QColor("#f85149")


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, BG)
    pal.setColor(QPalette.WindowText, TEXT)
    pal.setColor(QPalette.Base, BASE)
    pal.setColor(QPalette.AlternateBase, PANEL)
    pal.setColor(QPalette.ToolTipBase, PANEL)
    pal.setColor(QPalette.ToolTipText, TEXT)
    pal.setColor(QPalette.Text, TEXT)
    pal.setColor(QPalette.Button, PANEL)
    pal.setColor(QPalette.ButtonText, TEXT)
    pal.setColor(QPalette.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.Highlight, ACCENT)
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, ACCENT.lighter(130))
    pal.setColor(QPalette.PlaceholderText, MUTED)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#62666d"))
    app.setPalette(pal)
    app.setStyleSheet("""
        QToolTip { color: #e4e6eb; background: #26282c; border: 1px solid #3a3d43; padding: 4px; }
        QGroupBox { border: 1px solid #33363c; border-radius: 6px; margin-top: 14px; padding: 8px 8px 6px 8px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #c9ccd1; }
        QPushButton { padding: 5px 12px; border-radius: 5px; border: 1px solid #3a3d43; background: #2c2f34; }
        QPushButton:hover { background: #34383e; }
        QPushButton:pressed { background: #25282c; }
        QPushButton:disabled { color: #62666d; background: #24262a; }
        QPushButton#primary { background: #3d7eff; border: 1px solid #3d7eff; color: white; font-weight: 600; }
        QPushButton#primary:hover { background: #5a92ff; }
        QPushButton#primary:disabled { background: #2a3a5c; border-color: #2a3a5c; color: #8c98b3; }
        QListWidget { border: 1px solid #33363c; border-radius: 6px; padding: 2px; }
        QListWidget::item { padding: 6px 4px; border-bottom: 1px solid #25272b; }
        QListWidget::item:selected { background: #2b4f99; color: white; }
        QTableWidget { border: 1px solid #33363c; border-radius: 6px; gridline-color: #2a2d33; }
        QHeaderView::section { background: #26282c; color: #c9ccd1; border: none; border-bottom: 1px solid #33363c; padding: 4px; }
        QLabel#preview { background: #0d0e10; border: 1px solid #2a2d33; border-radius: 6px; color: #6d737b; }
        QLabel#title { font-size: 15px; font-weight: 600; }
        QLabel#muted { color: #9aa0a8; }
        QProgressBar { border: 1px solid #33363c; border-radius: 4px; background: #16171a; text-align: center; height: 14px; }
        QProgressBar::chunk { background: #3d7eff; border-radius: 3px; }
    """)


def app_icon() -> QIcon:
    """Painted icon: a strip of 'snow' with a clean green fragment cut out of it."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(paint_icon(size))
    return icon


def paint_icon(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = float(size)
    radius = s * 0.18
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1b1d21"))
    p.drawRoundedRect(QRectF(0, 0, s, s), radius, radius)
    # snow texture
    import random

    rnd = random.Random(7)
    cell = max(1.0, s / 16)
    y = s * 0.2
    while y < s * 0.8:
        x = s * 0.08
        while x < s * 0.92:
            g = rnd.randint(70, 230)
            p.setBrush(QColor(g, g, g))
            p.drawRect(QRectF(x, y, cell, cell))
            x += cell
        y += cell
    # clean fragment in the middle
    grad = QLinearGradient(QPointF(0, s * 0.2), QPointF(0, s * 0.8))
    grad.setColorAt(0.0, QColor("#6fd08c"))
    grad.setColorAt(1.0, QColor("#2e8b57"))
    p.setBrush(QBrush(grad))
    p.drawRect(QRectF(s * 0.36, s * 0.2, s * 0.28, s * 0.6))
    # scissor-cut marks
    pen = QPen(QColor("#ffd166"), max(1.0, s / 24))
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.36, s * 0.12), QPointF(s * 0.36, s * 0.88))
    p.drawLine(QPointF(s * 0.64, s * 0.12), QPointF(s * 0.64, s * 0.88))
    p.end()
    return pm
