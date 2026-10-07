"""Пустые состояния и мелкие переиспользуемые виджеты."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QLayout, QVBoxLayout, QWidget


def clear_layout(layout: QLayout) -> None:
    """Убирает все элементы layout, удаляя виджеты."""
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class Placeholder(QWidget):
    """Заглушка на пустое состояние."""

    def __init__(self, title: str, hint: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        self.title = QLabel(title)
        self.title.setObjectName("placeholderTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)

        self.hint = QLabel(hint)
        self.hint.setObjectName("placeholderHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(hint))
        layout.addWidget(self.hint)


class StatusDot(QLabel):
    """Индикатор связи в шапке."""

    def __init__(self, text: str = "на связи", color: QColor | None = None) -> None:
        super().__init__(f"● {text}")
        self.setObjectName("statusDot")
        self.set_color(color or QColor("#2e9e5b"))

    def set_text(self, text: str) -> None:
        self.setText(f"● {text}")

    def set_color(self, color: QColor) -> None:
        self.setStyleSheet(f"color: {color.name()};")
