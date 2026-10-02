"""Запуск GUI: QApplication, стили, главное окно."""

from __future__ import annotations

import sys

from chat_multi_cli.ui.main_window import MainWindow
from chat_multi_cli.ui.style import QSS


def run() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("chat_multi_cli")
    app.setOrganizationName("STO")
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)

    window = MainWindow()
    window.show()
    return app.exec()
