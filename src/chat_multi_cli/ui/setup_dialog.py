"""Диалог первого запуска: ФИО мастера и адрес сервера.

Показывается, пока не заполнены обязательные поля в config.json. Цех не
спрашиваем здесь: сервер может отдать пустой справочник цехов, и выбор тогда
бессмыслен — его предложим отдельно после успешного входа, если цеха есть.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from chat_multi_cli.config import AppConfig


class SetupDialog(QDialog):
    """ФИО + адрес сервера. Кнопка «Подключиться» ждёт непустое ФИО."""

    def __init__(
        self,
        config: AppConfig,
        *,
        error: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Подключение к серверу")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        title = QLabel("Войдите в систему")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        hint = QLabel(
            "Как представляться клиентам и на какой сервер подключаться. "
            "Адрес по умолчанию — локальный сервер разработки."
        )
        hint.setObjectName("dialogHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        if error:
            error_label = QLabel(error)
            error_label.setObjectName("dialogError")
            error_label.setWordWrap(True)
            layout.addWidget(error_label)

        layout.addSpacing(6)

        name_caption = QLabel("ФИО")
        name_caption.setObjectName("dialogField")
        layout.addWidget(name_caption)

        self.name_edit = QLineEdit(config.full_name)
        self.name_edit.setPlaceholderText("Например, Фёдор Семёнов")
        self.name_edit.setClearButtonEnabled(True)
        layout.addWidget(self.name_edit)

        url_caption = QLabel("Адрес сервера")
        url_caption.setObjectName("dialogField")
        layout.addWidget(url_caption)

        self.url_edit = QLineEdit(config.base_url)
        self.url_edit.setPlaceholderText("http://127.0.0.1:21000/v1")
        self.url_edit.setClearButtonEnabled(True)
        layout.addWidget(self.url_edit)

        layout.addSpacing(10)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.connect_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.connect_button.setText("Подключиться")
        self.connect_button.setObjectName("primaryButton")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Выйти")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.textChanged.connect(self._validate)
        self.url_edit.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        self.connect_button.setEnabled(bool(self.name_edit.text().strip()))

    @property
    def full_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def base_url(self) -> str:
        return self.url_edit.text().strip().rstrip("/") or "http://127.0.0.1:21000/v1"
