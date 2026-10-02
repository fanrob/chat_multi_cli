"""Правая часть для пункта «Входящие заявки»: непринятые заявки списком."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from chat_multi_cli.ui.mock import MockTicket
from chat_multi_cli.ui.widgets import Placeholder


class IncomingCard(QFrame):
    """Карточка непринятой заявки с превью сообщений клиента."""

    accept_requested = Signal(str)
    decline_requested = Signal(str)

    def __init__(self, ticket: MockTicket) -> None:
        super().__init__()
        self.ticket = ticket
        self.setObjectName("incomingCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)

        title = QLabel(ticket.subject)
        title.setObjectName("cardSubject")
        top.addWidget(title)
        top.addStretch(1)

        time_label = QLabel(ticket.updated)
        time_label.setObjectName("cardTime")
        top.addWidget(time_label)
        layout.addLayout(top)

        meta = QLabel(f"{ticket.client_name} · {ticket.workshop}")
        meta.setObjectName("cardMeta")
        layout.addWidget(meta)

        preview = QLabel(_preview(ticket))
        preview.setObjectName("cardPreview")
        preview.setWordWrap(True)
        layout.addWidget(preview)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        accept = QPushButton("Принять заявку")
        accept.setObjectName("primaryButton")
        accept.clicked.connect(lambda: self.accept_requested.emit(ticket.id))
        buttons.addWidget(accept)

        decline = QPushButton("Скрыть")
        decline.setObjectName("secondaryButton")
        decline.setToolTip("Убрать из своей ленты, на других мастеров не влияет")
        decline.clicked.connect(lambda: self.decline_requested.emit(ticket.id))
        buttons.addWidget(decline)

        buttons.addStretch(1)

        if ticket.unread:
            unread = QLabel(f"{ticket.unread} новых")
            unread.setObjectName("cardUnread")
            buttons.addWidget(unread)

        layout.addLayout(buttons)


class IncomingView(QWidget):
    """Список непринятых заявок."""

    accept_requested = Signal(str)
    decline_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("incomingView")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("ticketHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 20, 16)
        header_layout.setSpacing(4)

        title = QLabel("Входящие заявки")
        title.setObjectName("chatSubject")
        header_layout.addWidget(title)

        self.hint = QLabel()
        self.hint.setObjectName("chatMeta")
        self.hint.setWordWrap(True)
        header_layout.addWidget(self.hint)
        layout.addWidget(header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("incomingScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll_area, 1)

        self.placeholder = Placeholder(
            "Новых заявок нет",
            "Когда клиент оставит заявку вашему цеху, она появится здесь.",
        )
        self.scroll_area.setWidget(self.placeholder)
        self._cards_host: QWidget | None = None

    def set_tickets(self, tickets: list[MockTicket]) -> None:
        if self._cards_host is not None:
            self._cards_host.deleteLater()
            self._cards_host = None

        self.hint.setText(
            f"Заявок, которые никто ещё не принял: {len(tickets)}. "
            "Кто первый принял — тот и работает."
            if tickets
            else "Все заявки разобраны."
        )

        if not tickets:
            self.scroll_area.setWidget(self.placeholder)
            return

        host = QWidget()
        host.setObjectName("incomingBackground")
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(20, 16, 20, 20)
        host_layout.setSpacing(12)

        for ticket in tickets:
            card = IncomingCard(ticket)
            card.accept_requested.connect(self.accept_requested.emit)
            card.decline_requested.connect(self.decline_requested.emit)
            host_layout.addWidget(card)

        host_layout.addStretch(1)
        self._cards_host = host
        self.scroll_area.setWidget(host)
        self.scroll_area.verticalScrollBar().setValue(0)

    def card_widgets(self) -> list[IncomingCard]:
        if self._cards_host is None:
            return []
        return self._cards_host.findChildren(IncomingCard)


def _preview(ticket: MockTicket) -> str:
    if not ticket.messages:
        return "Нет сообщений"
    first = ticket.messages[0].text
    if ticket.messages[0].attachments:
        first += f"  [{len(ticket.messages[0].attachments)} вложение]"
    if len(ticket.messages) > 1:
        first += f"  …  ещё {len(ticket.messages) - 1}"
    return first
