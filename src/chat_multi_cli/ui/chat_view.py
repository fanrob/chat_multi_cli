"""Правый верх: переписка по заявке."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from chat_multi_cli.ui.composer import Composer
from chat_multi_cli.ui.mock import MockAttachment, MockMessage, MockTicket
from chat_multi_cli.ui.widgets import clear_layout

DELIVERY_MARKS = {
    "read": "✓✓ прочитано",
    "sent": "✓✓ отправлено",
    "sending": "✓ отправляется",
    "queued": "в очереди",
    "failed": "не отправлено",
}

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


def _scroll_to_bottom(area: QScrollArea) -> None:
    QTimer.singleShot(
        0,
        lambda: area.verticalScrollBar().setValue(area.verticalScrollBar().maximum()),
    )


class TicketHeader(QFrame):
    """Шапка чата: тема, клиент, цех, статус."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("ticketHeader")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 16, 14)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)

        self.subject = QLabel()
        self.subject.setObjectName("chatSubject")
        top.addWidget(self.subject)
        top.addStretch(1)

        self.status = QLabel()
        top.addWidget(self.status)
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        self.client = QLabel()
        self.client.setObjectName("chatMeta")
        bottom.addWidget(self.client)

        self.workshop = QLabel()
        self.workshop.setObjectName("chatMeta")
        bottom.addWidget(self.workshop)

        self.members = QLabel()
        self.members.setObjectName("chatMeta")
        bottom.addWidget(self.members)

        layout.addLayout(bottom)

    def set_ticket(self, ticket: MockTicket) -> None:
        self.subject.setText(ticket.subject)
        self.client.setText(ticket.client_name)
        self.workshop.setText(f"· {ticket.workshop}")
        self.status.setText(ticket.status_label)
        closed = ticket.is_closed
        self.status.setStyleSheet("color: #b3b9c2;" if closed else "color: #2e9e5b;")


class MessageBubble(QWidget):
    """Одно сообщение: текст, вложения, автор, время, статус доставки."""

    def __init__(self, message: MockMessage) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        own = message.sender == "master"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        if own:
            row.addStretch(1)

        bubble = QFrame()
        bubble.setObjectName("bubbleMaster" if own else "bubbleClient")
        bubble.setMaximumWidth(560)
        inner = QVBoxLayout(bubble)
        inner.setContentsMargins(12, 9, 12, 8)
        inner.setSpacing(4)

        text = QLabel(message.text)
        text.setObjectName("bubbleText")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inner.addWidget(text)

        for attachment in message.attachments:
            inner.addWidget(_attachment_tile(attachment))

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)

        if message.sender_name:
            author = QLabel(message.sender_name)
            author.setObjectName("bubbleAuthor")
            meta.addWidget(author)

        meta.addStretch(1)

        mark = DELIVERY_MARKS.get(message.delivery, "")
        if mark:
            status = QLabel(mark)
            status.setObjectName("bubbleMetaRead" if message.delivery == "read" else "bubbleMeta")
            meta.addWidget(status)

        time_label = QLabel(message.time)
        time_label.setObjectName("bubbleMeta")
        meta.addWidget(time_label)
        inner.addLayout(meta)

        row.addWidget(bubble)
        if not own:
            row.addStretch(1)


class SystemBubble(QWidget):
    def __init__(self, message: MockMessage) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(f"{message.text} · {message.time}")
        label.setObjectName("systemBubble")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)


class ChatView(QWidget):
    """История переписки + поле ввода."""

    send_requested = Signal(str)
    attach_requested = Signal()
    close_requested = Signal()
    reopen_requested = Signal()
    add_master_requested = Signal()
    more_requested = Signal()
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = TicketHeader()
        layout.addWidget(self.header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("chatScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.bubble_host = QWidget()
        self.bubble_host.setObjectName("chatBackground")
        self.bubble_layout = QVBoxLayout(self.bubble_host)
        self.bubble_layout.setContentsMargins(20, 16, 20, 16)
        self.bubble_layout.setSpacing(10)
        self.scroll_area.setWidget(self.bubble_host)
        layout.addWidget(self.scroll_area, 1)

        self.composer = Composer()
        self.composer.send_requested.connect(self.send_requested.emit)
        self.composer.attach_requested.connect(self.attach_requested.emit)
        self.composer.close_requested.connect(self.close_requested.emit)
        self.composer.reopen_requested.connect(self.reopen_requested.emit)
        self.composer.add_master_requested.connect(self.add_master_requested.emit)
        self.composer.more_requested.connect(self.more_requested.emit)
        self.composer.back_requested.connect(self.back_requested.emit)
        layout.addWidget(self.composer)

        self.ticket: MockTicket | None = None

    def show_ticket(self, ticket: MockTicket) -> None:
        self.ticket = ticket
        self.header.set_ticket(ticket)
        self.composer.set_enabled_state(ticket.is_closed)
        self.redraw()

    def redraw(self) -> None:
        clear_layout(self.bubble_layout)
        if self.ticket is None:
            return
        for message in self.ticket.messages:
            self.bubble_layout.addWidget(self._build(message))
        _scroll_to_bottom(self.scroll_area)

    def append_message(self, message: MockMessage) -> None:
        if self.ticket is None:
            return
        self.ticket.messages.append(message)
        self.bubble_layout.addWidget(self._build(message))
        _scroll_to_bottom(self.scroll_area)

    def append_local_message(self, text: str, attachment_names: list[str]) -> None:
        self.append_message(
            MockMessage(
                sender="master",
                text=text,
                time="сейчас",
                delivery="sending",
                attachments=[MockAttachment(name=name, size_kb=0) for name in attachment_names],
            )
        )

    def append_system_message(self, text: str) -> None:
        self.append_message(MockMessage(sender="system", text=text, time="сейчас"))

    def _build(self, message: MockMessage) -> QWidget:
        if message.sender == "system":
            return SystemBubble(message)
        return MessageBubble(message)


def _attachment_tile(attachment: MockAttachment) -> QWidget:
    tile = QFrame()
    tile.setObjectName("attachmentTile")
    layout = QVBoxLayout(tile)
    layout.setContentsMargins(8, 8, 8, 6)
    layout.setSpacing(2)

    is_photo = attachment.name.lower().endswith(PHOTO_SUFFIXES)
    icon = QLabel("🖼" if is_photo else "📄")
    icon.setObjectName("attachmentIcon")
    layout.addWidget(icon)

    caption = QLabel(attachment.name)
    caption.setObjectName("attachmentName")
    caption.setToolTip(attachment.name)
    layout.addWidget(caption)

    if attachment.size_kb:
        size = QLabel(f"{attachment.size_kb} КБ")
        size.setObjectName("attachmentSize")
        layout.addWidget(size)
    return tile


class MembersPanel(QFrame):
    """Справа от чата: мастера, подключённые к заявке."""

    add_master_requested = Signal()
    master_remove_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("membersPanel")
        self.setFixedWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 14)
        layout.setSpacing(10)

        title = QLabel("Мастера по заявке")
        title.setObjectName("membersTitle")
        layout.addWidget(title)

        self.rows = QVBoxLayout()
        self.rows.setSpacing(6)
        layout.addLayout(self.rows)
        layout.addStretch(1)

        self.add_button = QPushButton("+ Подключить мастера")
        self.add_button.setObjectName("secondaryButton")
        self.add_button.clicked.connect(self.add_master_requested.emit)
        layout.addWidget(self.add_button)

    def set_masters(self, masters: list[tuple[str, str]]) -> None:
        clear_layout(self.rows)
        for master_id, name in masters:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            label = QLabel(f"● {name}")
            label.setObjectName("memberRow")
            row_layout.addWidget(label)
            row_layout.addStretch(1)

            remove = QPushButton("✕")
            remove.setObjectName("iconButtonSmall")
            remove.setToolTip(f"Отключить {name}")
            remove.setFixedSize(22, 22)
            remove.clicked.connect(
                lambda _checked=False, mid=master_id: self.master_remove_requested.emit(mid)
            )
            row_layout.addWidget(remove)
            self.rows.addWidget(row)

        self.add_button.setText(
            "+ Подключить мастера" if masters else "+ Подключить мастера (вы первый)"
        )


class ChatPage(QWidget):
    """Чат вместе с панелью мастеров."""

    add_master_requested = Signal()
    master_remove_requested = Signal(str)

    def __init__(self, chat: ChatView) -> None:
        super().__init__()
        self.chat = chat
        self.members_panel = MembersPanel()
        self.chat.add_master_requested.connect(self.add_master_requested.emit)
        self.members_panel.add_master_requested.connect(self.add_master_requested.emit)
        self.members_panel.master_remove_requested.connect(self.master_remove_requested.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(chat, 1)
        layout.addWidget(self.members_panel)

    def set_masters(self, masters: list[tuple[str, str]]) -> None:
        self.members_panel.set_masters(masters)
