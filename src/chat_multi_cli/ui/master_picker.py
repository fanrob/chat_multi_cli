"""Диалог выбора мастера, которого подключают к заявке."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

ID_ROLE = Qt.ItemDataRole.UserRole + 1


class MasterPicker(QDialog):
    """Поиск по справочнику мастеров (в API это GET /masters)."""

    def __init__(
        self,
        masters: list[tuple[str, str]],
        already: list[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Подключить мастера")
        self.setMinimumSize(420, 420)
        self._all = masters
        self._already = {mid for mid, _ in already}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Кого подключаем к заявке?")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self.search = QLineEdit()
        self.search.setObjectName("searchInput")
        self.search.setPlaceholderText("Поиск по ФИО")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Подключить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill("")

    def _fill(self, query: str) -> None:
        self.list.clear()
        needle = query.strip().lower()
        for master_id, name in self._all:
            if needle and needle not in name.lower():
                continue
            item = QListWidgetItem(name)
            item.setData(ID_ROLE, master_id)
            if master_id in self._already:
                item.setText(f"{name}  — уже в заявке")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)

    def _filter(self, text: str) -> None:
        self._fill(text)

    def selected_id(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        master_id = item.data(ID_ROLE)
        return str(master_id) if master_id else None
