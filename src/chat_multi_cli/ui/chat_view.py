"""Правый верх: переписка по заявке."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
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

from chat_multi_cli.models import (
    SENDER_CLIENT,
    SENDER_MASTER,
    Attachment,
    Message,
    Ticket,
)
from chat_multi_cli.ui.composer import Composer
from chat_multi_cli.ui.viewmodels import client_name, format_message_time
from chat_multi_cli.ui.widgets import clear_layout

DELIVERY_MARKS = {
    "read": "✓✓ прочитано",
    "sent": "✓✓ отправлено",
    "sending": "✓ отправляется",
    "queued": "в очереди",
    "failed": "не отправлено",
}

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


@dataclass(frozen=True, slots=True)
class PendingMessage:
    """Оптимистичное сообщение: уже отправляется, сервер ещё не подтвердил.

    baseline_seq — последний seq кэша на момент отправки: копия с сервера
    будет новее, только по seq старше baseline её отличаем от старых
    сообщений с таким же текстом.
    """

    message: Message
    baseline_seq: int


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

    def set_ticket(self, ticket: Ticket) -> None:
        self.subject.setText(ticket.subject)
        self.client.setText(client_name(ticket))
        workshop = ticket.workshop_name or ""
        self.workshop.setText(f"· {workshop}" if workshop else "")
        self.status.setText(ticket.status_label)
        closed = ticket.is_closed
        self.status.setStyleSheet("color: #b3b9c2;" if closed else "color: #2e9e5b;")


class AttachmentTile(QFrame):
    """Плитка вложения; по клику просит открыть файл.

    Сигнал несёт сам Attachment, а не только id: для оптимистичных сообщений
    вложение ещё локальное (файл на диске), для серверных — скачивается по id,
    и решать это должен обработчик, а не плитка.
    """

    activated = Signal(object)

    def __init__(self, attachment: Attachment) -> None:
        super().__init__()
        self.attachment = attachment
        self.setObjectName("attachmentTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{attachment.filename} — открыть")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(2)

        is_photo = attachment.filename.lower().endswith(PHOTO_SUFFIXES)
        icon = QLabel("🖼" if is_photo else "📄")
        icon.setObjectName("attachmentIcon")
        layout.addWidget(icon)

        caption = QLabel(attachment.filename)
        caption.setObjectName("attachmentName")
        caption.setToolTip(attachment.filename)
        layout.addWidget(caption)

        if attachment.size > 0:
            size = QLabel(f"{max(1, attachment.size // 1024)} КБ")
            size.setObjectName("attachmentSize")
            layout.addWidget(size)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.attachment)
        super().mousePressEvent(event)


class MessageBubble(QWidget):
    """Одно сообщение: текст, вложения, автор, время, статус доставки."""

    attachment_activated = Signal(object)

    def __init__(self, message: Message) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        own = message.sender == SENDER_MASTER
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
            tile = AttachmentTile(attachment)
            tile.activated.connect(self.attachment_activated.emit)
            inner.addWidget(tile)

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

        time_label = QLabel(format_message_time(message.created_at))
        time_label.setObjectName("bubbleMeta")
        meta.addWidget(time_label)
        inner.addLayout(meta)

        row.addWidget(bubble)
        if not own:
            row.addStretch(1)


class SystemBubble(QWidget):
    """Центрированное уведомление. Сервер system-сообщений не шлёт, но
    класс сохранён: если в данных встретится sender='system', он не сломает
    диалог."""

    def __init__(self, message: Message) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(f"{message.text} · {format_message_time(message.created_at)}")
        label.setObjectName("systemBubble")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)


class ChatView(QWidget):
    """История переписки + поле ввода."""

    send_requested = Signal(str)
    attach_requested = Signal()
    attachment_requested = Signal(object)
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

        self.ticket: Ticket | None = None
        self.messages: list[Message] = []
        #: отправленные, но ещё не подтверждённые сервером (оптимистичный пузырь)
        self.pending: list[PendingMessage] = []

    def show_ticket(
        self,
        ticket: Ticket | None,
        messages: list[Message] | None = None,
        *,
        scroll_to_bottom: bool = True,
    ) -> None:
        was_at_bottom = self.at_bottom()
        self.ticket = ticket
        if messages is not None:
            self.messages = list(messages)
        if ticket is not None:
            self.header.set_ticket(ticket)
        self.composer.set_enabled_state(ticket.is_closed if ticket else True)
        self.redraw()
        if scroll_to_bottom or was_at_bottom:
            _scroll_to_bottom(self.scroll_area)

    def redraw(self) -> None:
        clear_layout(self.bubble_layout)
        if self.ticket is None:
            return
        for message in self.visible_messages():
            self.bubble_layout.addWidget(self._build(message))

    def visible_messages(self) -> list[Message]:
        """Кэш диалога плюс вводное сообщение и неподтверждённые отправки.

        Вводное сообщение — текст клиента, создавший заявку: сервер держит его
        в карточке (ticket.text), а не в переписке, поэтому здесь он становится
        первым пузырём.

        Pending снимается, когда в кэше появилась его серверная копия: тот же
        отправитель, тот же текст и seq новее baseline (запомненного на момент
        отправки). Одна копия гасит один pending — так два одинаковых текста
        подряд не исчезнут, пока подтверждено только первое.
        """
        shown = list(self.messages)
        result = shown.copy()
        ticket_id = self.ticket.id if self.ticket else None

        if self.pending and ticket_id is not None:
            used: set[int] = set()
            for pending in self.pending:
                entry = pending.message
                if entry.ticket_id != ticket_id:
                    continue
                echo = next(
                    (
                        index
                        for index, message in enumerate(shown)
                        if index not in used
                        and message.sender == entry.sender
                        and message.text == entry.text
                        and message.seq > pending.baseline_seq
                    ),
                    None,
                )
                if echo is None:
                    result.append(entry)
                else:
                    used.add(echo)

        intro = self._intro_message()
        if intro is not None:
            result.insert(0, intro)
        return result

    def _intro_message(self) -> Message | None:
        """Первое сообщение клиента, создавшее заявку.

        Сервер отдаёт его в карточке (ticket.text), но не в переписке —
        поэтому без этого пузыря текст заявки в диалоге вообще не виден.
        Показываем только если такого текста ещё нет среди сообщений, чтобы
        серверная копия первого сообщения не задвоилась.
        """
        ticket = self.ticket
        if ticket is None or not ticket.text.strip():
            return None
        if any(message.text == ticket.text for message in self.messages):
            return None
        return Message(
            id=f"ticket-intro-{ticket.id}",
            seq=0,
            ticket_id=ticket.id,
            sender=SENDER_CLIENT,
            sender_name=ticket.client.display_name if ticket.client else "",
            text=ticket.text,
            created_at=ticket.created_at,
            delivery="delivered",
        )

    def add_pending(self, entry: Message, baseline_seq: int) -> None:
        """Показать сообщение сразу, не дожидаясь ответа сервера."""
        if self.ticket is None or entry.ticket_id != self.ticket.id:
            return
        self.pending.append(PendingMessage(message=entry, baseline_seq=baseline_seq))
        self.redraw()
        _scroll_to_bottom(self.scroll_area)

    def take_pending(self, entry: Message, confirmed_seq: int | None = None) -> None:
        """Снять оптимистичный пузырь (сервер подтвердил или ошибка).

        confirmed_seq — seq серверной копии: baseline других pending с тем же
        отправителем и текстом поднимается до неё, чтобы их не погасила чужая
        копия, пока свои ещё в полёте.
        """
        before = len(self.pending)
        self.pending = [item for item in self.pending if item.message.id != entry.id]
        if confirmed_seq is not None:
            self.pending = [
                PendingMessage(
                    message=item.message,
                    baseline_seq=max(item.baseline_seq, confirmed_seq),
                )
                if item.message.sender == entry.sender and item.message.text == entry.text
                else item
                for item in self.pending
            ]
        if len(self.pending) != before:
            self.redraw()

    def append_message(self, message: Message) -> None:
        if self.ticket is None:
            return
        self.messages.append(message)
        self.bubble_layout.addWidget(self._build(message))
        _scroll_to_bottom(self.scroll_area)

    def at_bottom(self) -> bool:
        """Диалог доскроллен до низа (параметр сохранения позиции при рефреше)."""
        bar = self.scroll_area.verticalScrollBar()
        return bar.maximum() - bar.value() < 60

    def _build(self, message: Message) -> QWidget:
        if message.sender == "system":
            return SystemBubble(message)
        bubble = MessageBubble(message)
        bubble.attachment_activated.connect(self.attachment_requested.emit)
        return bubble


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
