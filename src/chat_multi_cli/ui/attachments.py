"""Просмотр и открытие вложений.

Фото показываем прямо в окне (PhotoViewer): так не зависим от ассоциаций
файлов Windows и не оставляем следов на диске ради одного взгляда. Всё
остальное (документы, архивы) отдаём системе — писать свой просмотрщик
для .docx или .zip смысла нет.

Скачанные байты держим только в памяти; на диск пишем лишь когда файл надо
передать внешнему приложению (кнопка «Открыть в системе» или не-фото). Такие
файлы кладём во временный каталог: после просмотра их можно удалить.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

#: Предел предпросмотра: больше — и окно не помещается на скромных экранах.
MAX_WIDTH = 960
MAX_HEIGHT = 720

#: Подкаталог временной папки для скачанных вложений.
TEMP_DIRNAME = "chat_multi_cli"


def write_temp_file(filename: str, data: bytes) -> Path:
    """Кладёт байты во временный файл и возвращает путь к нему."""
    directory = Path(tempfile.gettempdir()) / TEMP_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / Path(filename).name
    path.write_bytes(data)
    return path


def open_in_system(path: Path) -> None:
    """Открывает файл приложением по умолчанию (Windows).

    На других платформах os.startfile нет — сообщаем ошибкой, чтобы UI
    показал предупреждение, а не упал с невнятным AttributeError.
    """
    opener = getattr(os, "startfile", None)
    if opener is None:
        raise OSError("Открытие системным приложением доступно только в Windows")
    opener(str(path))


class PhotoViewer(QDialog):
    """Картинка вложения в окне: масштаб по размеру, сохранение, открытие."""

    def __init__(
        self,
        parent: QWidget | None,
        filename: str,
        data: bytes,
    ) -> None:
        super().__init__(parent)
        self._filename = filename
        self._data = data
        self.setWindowTitle(filename)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        self.image = QLabel()
        self.image.setObjectName("photoViewerImage")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(240, 180)
        self._set_pixmap()
        layout.addWidget(self.image, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        save = QPushButton("Сохранить как…")
        save.setObjectName("secondaryButton")
        save.clicked.connect(self._save_as)
        buttons.addWidget(save)

        open_button = QPushButton("Открыть в системе")
        open_button.setObjectName("secondaryButton")
        open_button.clicked.connect(self._open_external)
        buttons.addWidget(open_button)

        buttons.addStretch(1)

        close = QPushButton("Закрыть")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)

        layout.addLayout(buttons)

    def _set_pixmap(self) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(self._data) or pixmap.isNull():
            self.image.setText("Не удалось показать изображение")
            return
        scaled = pixmap.scaled(
            MAX_WIDTH,
            MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image.setPixmap(scaled)

    def _save_as(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "Сохранить файл", self._filename)
        if not target:
            return
        try:
            Path(target).write_bytes(self._data)
        except OSError as exc:
            QMessageBox.warning(self, "Не сохранилось", str(exc))

    def _open_external(self) -> None:
        try:
            open_in_system(write_temp_file(self._filename, self._data))
        except OSError as exc:
            QMessageBox.warning(self, "Не открылось", str(exc))
