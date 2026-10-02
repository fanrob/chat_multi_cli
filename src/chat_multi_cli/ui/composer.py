"""Область ввода сообщения с действиями по заявке."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from chat_multi_cli.ui.widgets import clear_layout


class ComposerInput(QTextEdit):
    """Многострочное поле: Enter — отправить, Shift+Enter — перенос строки."""

    send_pressed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("composerInput")
        self.setPlaceholderText("Сообщение клиенту…  (Enter — отправить)")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.document().setDocumentMargin(8)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMinimumHeight(44)
        self.setMaximumHeight(140)
        self.textChanged.connect(self._autosize)

    def _autosize(self) -> None:
        doc_height = self.document().size().height()
        self.setMaximumHeight(max(44, min(140, int(doc_height) + 16)))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        enter = Qt.Key.Key_Enter, Qt.Key.Key_Return
        if event.key() in enter and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send_pressed.emit()
            return
        super().keyPressEvent(event)


class AttachmentStrip(QWidget):
    """Превью файлов, приготовленных к отправке."""

    remove_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("attachmentStrip")
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 0)
        layout.setSpacing(8)
        self._row = layout
        self._names: list[str] = []

    def set_files(self, names: list[str]) -> None:
        clear_layout(self._row)
        self._names = list(names)
        for index, name in enumerate(self._names):
            chip = QPushButton(f"✕  {name}")
            chip.setObjectName("attachmentChip")
            chip.setToolTip("Убрать файл")
            chip.clicked.connect(lambda _=False, i=index: self._remove(i))
            self._row.addWidget(chip)
        self._row.addStretch(1)
        self.setVisible(bool(self._names))

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def _remove(self, index: int) -> None:
        if 0 <= index < len(self._names):
            del self._names[index]
            self.set_files(self._names)


class Composer(QFrame):
    """Нижний блок: вложения, ввод, отправка и действия по заявке."""

    send_requested = Signal(str)
    attach_requested = Signal()
    close_requested = Signal()
    add_master_requested = Signal()
    back_requested = Signal()
    reopen_requested = Signal()
    more_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("composer")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(0)

        self.attachments = AttachmentStrip()
        root.addWidget(self.attachments)

        row = QHBoxLayout()
        row.setContentsMargins(12, 8, 12, 12)
        row.setSpacing(8)

        self.attach_button = QPushButton("📎")
        self.attach_button.setObjectName("iconButton")
        self.attach_button.setToolTip("Прикрепить фото или файл")
        self.attach_button.setFixedSize(40, 40)
        self.attach_button.clicked.connect(self.attach_requested.emit)
        row.addWidget(self.attach_button)

        self.input = ComposerInput()
        self.input.send_pressed.connect(self.send_message)
        row.addWidget(self.input, 1)

        self.send_button = QPushButton("Отправить")
        self.send_button.setObjectName("primaryButton")
        self.send_button.setMinimumHeight(40)
        self.send_button.clicked.connect(self.send_message)
        row.addWidget(self.send_button)

        root.addLayout(row)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("actionBar")
        actions_layout = QHBoxLayout(self.action_bar)
        actions_layout.setContentsMargins(12, 6, 12, 8)
        actions_layout.setSpacing(8)

        self.back_button = QPushButton("← К заявкам")
        self.back_button.setObjectName("linkButton")
        self.back_button.clicked.connect(self.back_requested.emit)
        actions_layout.addWidget(self.back_button)

        self.close_button = QPushButton("Закрыть заявку")
        self.close_button.setObjectName("dangerButton")
        self.close_button.clicked.connect(self.close_requested.emit)
        actions_layout.addWidget(self.close_button)

        self.reopen_button = QPushButton("Открыть заново")
        self.reopen_button.setObjectName("secondaryButton")
        self.reopen_button.clicked.connect(self.reopen_requested.emit)
        self.reopen_button.setVisible(False)
        actions_layout.addWidget(self.reopen_button)

        self.add_master_button = QPushButton("+ Мастер")
        self.add_master_button.setObjectName("secondaryButton")
        self.add_master_button.setToolTip("Подключить второго мастера к заявке")
        self.add_master_button.clicked.connect(self.add_master_requested.emit)
        actions_layout.addWidget(self.add_master_button)

        actions_layout.addStretch(1)

        self.more_button = QPushButton("Ещё ▾")
        self.more_button.setObjectName("linkButton")
        self.more_button.setToolTip("Действия с заявкой")
        self.more_button.clicked.connect(self.more_requested.emit)
        actions_layout.addWidget(self.more_button)

        root.addWidget(self.action_bar)

    def send_message(self) -> None:
        text = self.input.toPlainText().strip()
        if text:
            self.send_requested.emit(text)
            self.input.clear()

    def set_enabled_state(self, is_closed: bool) -> None:
        """Закрытую заявку нельзя писать, но можно переоткрыть."""
        for widget in (self.input, self.send_button, self.attach_button):
            widget.setEnabled(not is_closed)
        self.input.setPlaceholderText(
            "Заявка закрыта" if is_closed else "Сообщение клиенту…  (Enter — отправить)"
        )
        self.close_button.setVisible(not is_closed)
        self.reopen_button.setVisible(is_closed)
        self.add_master_button.setEnabled(not is_closed)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
