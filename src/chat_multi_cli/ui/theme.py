"""Цвета и метрики оформления."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

BG = QColor("#f4f5f7")
SIDEBAR_BG = QColor("#ffffff")
HEADER_BG = QColor("#ffffff")
CARD_BG = QColor("#ffffff")
BORDER = QColor("#e3e6ea")

TEXT = QColor("#1c2128")
TEXT_MUTED = QColor("#7a838f")
TEXT_DISABLED = QColor("#b3b9c2")
WHITE = QColor("#ffffff")

ACCENT = QColor("#2f6fed")
ACCENT_HOVER = QColor("#2560d8")
ACCENT_SOFT = QColor("#eaf1fe")
DANGER = QColor("#d64545")
DANGER_SOFT = QColor("#fdeeee")
SUCCESS = QColor("#2e9e5b")

BUBBLE_MASTER = QColor("#dbe8ff")
BUBBLE_CLIENT = QColor("#ffffff")
BUBBLE_SYSTEM = QColor("#eceff3")

TICKET_NEW = QColor("#ffffff")
TICKET_NEW_BORDER = QColor("#dfe3e8")

ROW_HEIGHT = 68
INCOMING_ROW_HEIGHT = 76
PANEL_WIDTH = 340


def light_palette() -> QPalette:
    """Светлая палитра: тёмная тема ОС не должна делать текст белым на белом."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, BG)
    palette.setColor(QPalette.ColorRole.WindowText, TEXT)
    palette.setColor(QPalette.ColorRole.Base, WHITE)
    palette.setColor(QPalette.ColorRole.AlternateBase, CARD_BG)
    palette.setColor(QPalette.ColorRole.Text, TEXT)
    palette.setColor(QPalette.ColorRole.Button, WHITE)
    palette.setColor(QPalette.ColorRole.ButtonText, TEXT)
    palette.setColor(QPalette.ColorRole.BrightText, WHITE)
    palette.setColor(QPalette.ColorRole.Link, ACCENT)
    palette.setColor(QPalette.ColorRole.LinkVisited, ACCENT_HOVER)
    palette.setColor(QPalette.ColorRole.Highlight, ACCENT)
    palette.setColor(QPalette.ColorRole.HighlightedText, WHITE)
    palette.setColor(QPalette.ColorRole.Accent, ACCENT)
    palette.setColor(QPalette.ColorRole.ToolTipBase, WHITE)
    palette.setColor(QPalette.ColorRole.ToolTipText, TEXT)
    palette.setColor(QPalette.ColorRole.PlaceholderText, TEXT_MUTED)
    palette.setColor(QPalette.ColorRole.Light, WHITE)
    palette.setColor(QPalette.ColorRole.Midlight, BORDER)
    palette.setColor(QPalette.ColorRole.Dark, TEXT_DISABLED)
    palette.setColor(QPalette.ColorRole.Mid, TEXT_MUTED)
    palette.setColor(QPalette.ColorRole.Shadow, QColor("#000000"))

    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, QPalette.ColorRole.Text, TEXT_DISABLED)
    palette.setColor(disabled, QPalette.ColorRole.WindowText, TEXT_DISABLED)
    palette.setColor(disabled, QPalette.ColorRole.ButtonText, TEXT_DISABLED)
    palette.setColor(disabled, QPalette.ColorRole.Highlight, ACCENT_SOFT)
    palette.setColor(disabled, QPalette.ColorRole.HighlightedText, TEXT_MUTED)
    return palette
