"""Главное окно: панель заявок слева, чат/входящие справа."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
)

from chat_multi_cli.ui import mock, theme
from chat_multi_cli.ui.chat_view import ChatPage, ChatView
from chat_multi_cli.ui.incoming_view import IncomingView
from chat_multi_cli.ui.master_picker import MasterPicker
from chat_multi_cli.ui.ticket_list import TicketListPanel
from chat_multi_cli.ui.widgets import Placeholder, StatusDot

REFRESH_INTERVAL_MS = 20_000

MOCK_MASTERS = [
    ("m_17", "Фёдор Семёнов"),
    ("m_42", "Пётр Кузнецов"),
    ("m_08", "Антон Волков"),
    ("m_55", "Игорь Мельник"),
    ("m_63", "Дмитрий Орлов"),
]

MASTER_NAMES = dict(MOCK_MASTERS)
SELF_MASTER_ID = "m_17"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Заявки — мессенджер мастера")
        self.resize(1180, 760)

        self.tickets: list[mock.MockTicket] = list(mock.MOCK_TICKETS)
        self.incoming: list[mock.MockTicket] = list(mock.MOCK_INCOMING)
        self.masters: list[tuple[str, str]] = [MOCK_MASTERS[0], MOCK_MASTERS[1]]

        self.panel = TicketListPanel(self.tickets, self.incoming)
        self.panel.ticket_selected.connect(self._open_ticket)
        self.panel.incoming_selected.connect(self._open_incoming)

        self.stack = QStackedWidget()
        self.empty_page = Placeholder(
            "Заявка не выбрана",
            "Слева список заявок. Нажмите на заявку, чтобы открыть переписку, "
            "или на «Входящие заявки», чтобы взять свободную.",
        )
        self.chat = ChatView()
        self.chat_page = ChatPage(self.chat)
        self.chat_page.add_master_requested.connect(self._pick_master)
        self.chat_page.master_remove_requested.connect(self._remove_master)
        self.chat.send_requested.connect(self._send)
        self.chat.close_requested.connect(self._close_ticket)
        self.chat.reopen_requested.connect(self._reopen_ticket)
        self.chat.attach_requested.connect(self._attach)
        self.chat.more_requested.connect(self._more_actions)
        self.chat.back_requested.connect(self.panel.select_incoming)

        self.incoming_view = IncomingView()
        self.incoming_view.accept_requested.connect(self._accept)
        self.incoming_view.decline_requested.connect(self._decline)

        self.stack.addWidget(self.empty_page)
        self.stack.addWidget(self.chat_page)
        self.stack.addWidget(self.incoming_view)

        splitter = QSplitter()
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.panel)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([theme.PANEL_WIDTH, 840])
        self.setCentralWidget(splitter)

        status = QStatusBar()
        status.setObjectName("appStatusBar")
        self.setStatusBar(status)
        status.addWidget(QLabel("Эскиз UI · данные хардкод · сети нет"))
        status.addPermanentWidget(StatusDot("на связи"))

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(REFRESH_INTERVAL_MS)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start()

        self.panel.select_incoming()
        self._open_incoming()

    # --- навигация ---
    def _open_incoming(self) -> None:
        self.incoming_view.set_tickets(self.incoming)
        self.stack.setCurrentWidget(self.incoming_view)

    def _open_ticket(self, ticket_id: str) -> None:
        ticket = self._find(ticket_id)
        if ticket is None:
            return
        self.panel.select_ticket(ticket_id)
        self.chat.show_ticket(ticket)
        self.chat_page.set_masters(self.masters)
        self.stack.setCurrentWidget(self.chat_page)
        if ticket.unread:
            ticket.unread = 0
            self.panel.rebuild()

    # --- действия: пока без сервера, меняем только моки ---
    def _send(self, text: str) -> None:
        names = self.chat.composer.attachments.names
        self.chat.append_local_message(text, names)
        self.chat.composer.attachments.set_files([])
        self.panel.rebuild()

    def _accept(self, ticket_id: str) -> None:
        ticket = next((t for t in self.incoming if t.id == ticket_id), None)
        if ticket is None:
            return
        self.incoming = [t for t in self.incoming if t.id != ticket_id]
        ticket.status = "in_progress"
        ticket.unread = 0
        self.tickets = [ticket, *self.tickets]
        self.panel.rebuild()
        self.incoming_view.set_tickets(self.incoming)
        self._open_ticket(ticket_id)

    def _decline(self, ticket_id: str) -> None:
        self.incoming = [t for t in self.incoming if t.id != ticket_id]
        self.incoming_view.set_tickets(self.incoming)
        self.panel.rebuild()

    def _close_ticket(self) -> None:
        ticket = self.chat.ticket
        if ticket is None or ticket.is_closed:
            return
        ticket.status = "closed"
        self.chat.append_system_message("Заявка закрыта мастером Фёдор Семёнов")
        self.chat.show_ticket(ticket)
        self.panel.rebuild()

    def _reopen_ticket(self) -> None:
        ticket = self.chat.ticket
        if ticket is None:
            return
        ticket.status = "in_progress"
        self.chat.append_system_message("Заявка открыта заново")
        self.chat.show_ticket(ticket)
        self.panel.rebuild()

    def _pick_master(self) -> None:
        dialog = MasterPicker(MOCK_MASTERS, self.masters, self)
        if dialog.exec() != MasterPicker.DialogCode.Accepted:
            return
        self._pick_master_from((dialog.selected_id(),))

    def _pick_master_from(self, master_ids: tuple[str | None, ...]) -> None:
        """Подключает мастера без диалога — используется и в тестах."""
        for chosen in master_ids:
            if not chosen or chosen in {mid for mid, _ in self.masters}:
                continue
            name = MASTER_NAMES[chosen]
            self.masters.append((chosen, name))
            self.chat_page.set_masters(self.masters)
            self.chat.append_system_message(f"К заявке подключился мастер {name}")

    def _remove_master(self, master_id: str) -> None:
        name = MASTER_NAMES.get(master_id, "Мастер")
        self.masters = [(mid, n) for mid, n in self.masters if mid != master_id]
        self.chat_page.set_masters(self.masters)
        self.chat.append_system_message(f"{name} отключён от заявки")

    def _attach(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Прикрепить файлы",
            "",
            "Изображения (*.png *.jpg *.jpeg *.webp);;Все файлы (*)",
        )
        if not paths:
            return
        strip = self.chat.composer.attachments
        strip.set_files(strip.names + [_basename(p) for p in paths])

    def _more_actions(self) -> None:
        QMessageBox.information(
            self,
            "Ещё",
            "Заглушка меню действий:\n"
            "• вернуть заявку в общую ленту\n"
            "• отметить прочитанным\n"
            "• переслать другому мастеру\n"
            "• экспорт истории",
        )

    # --- имитация фонового обновления ---
    def _refresh(self) -> None:
        self.panel.rebuild()
        self.incoming_view.set_tickets(self.incoming)
        self.statusBar().showMessage(
            f"Эскиз UI · обновление списка каждые {REFRESH_INTERVAL_MS // 1000}с · сети нет"
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self.refresh_timer.stop()
        super().closeEvent(event)

    def _find(self, ticket_id: str) -> mock.MockTicket | None:
        return next((t for t in [*self.tickets, *self.incoming] if t.id == ticket_id), None)


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]
