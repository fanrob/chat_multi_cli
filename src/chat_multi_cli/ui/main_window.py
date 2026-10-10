"""Главное окно: панель заявок слева, чат/входящие справа.

Вся сеть и БД — за AppContext (services.py): окно только читает кэш и шлёт
действия. События ленты приходят колбэками SyncWorker через SyncBridge и
перерисовывают списки; действия выполняются TaskRunner'ом в фоне, чтобы
окно не висло на сетевом запросе.
"""

from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
)

from chat_multi_cli.models import (
    DELIVERY_SENDING,
    SENDER_MASTER,
    Attachment,
    Message,
    Ticket,
    TicketBrief,
)
from chat_multi_cli.services import AppContext
from chat_multi_cli.ui import theme
from chat_multi_cli.ui.chat_view import PHOTO_SUFFIXES, ChatPage, ChatView
from chat_multi_cli.ui.incoming_view import IncomingView
from chat_multi_cli.ui.master_picker import MasterPicker
from chat_multi_cli.ui.tasks import SyncBridge, TaskRunner
from chat_multi_cli.ui.ticket_list import TicketListPanel
from chat_multi_cli.ui.viewmodels import member_pairs
from chat_multi_cli.ui.widgets import Placeholder, StatusDot


def _local_message(
    ctx: AppContext,
    ticket: Ticket,
    text: str,
    paths: list[str],
    baseline_seq: int,
) -> Message:
    """Оптимистичное сообщение: показывается сразу, до ответа сервера.

    Живёт только в памяти ChatView (в кэш не пишется): id временный,
    seq — baseline + 1 только для порядка в списке, delivery — «отправляется».
    Вложения показываем по локальным файлам, реальные upload-иды придут
    с серверной копией.
    """
    now = datetime.now().astimezone().isoformat()
    profile = ctx.profile
    return Message(
        id=f"local-{uuid.uuid4().hex}",
        seq=baseline_seq + 1,
        ticket_id=ticket.id,
        sender=SENDER_MASTER,
        sender_name=profile.full_name if profile else "",
        text=text,
        created_at=now,
        attachments=tuple(_local_attachment(path, now) for path in paths),
        sender_master_id=profile.id if profile else None,
        delivery=DELIVERY_SENDING,
    )


def _local_attachment(path_str: str, created_at: str) -> Attachment:
    path = Path(path_str)
    mime, _ = mimetypes.guess_type(path.name)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return Attachment(
        attachment_id=f"local-{uuid.uuid4().hex}",
        kind="photo" if path.suffix.lower() in PHOTO_SUFFIXES else "file",
        filename=path.name,
        mime_type=mime or "application/octet-stream",
        size=size,
        source=path_str,
        created_at=created_at,
    )


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, *, tasks: TaskRunner | None = None) -> None:
        super().__init__()
        self.ctx = ctx
        self.tasks = tasks or TaskRunner()
        self.bridge = SyncBridge()
        ctx.worker.on_events(self.bridge.on_batch)
        ctx.worker.on_status(self.bridge.on_status)
        self.bridge.batch_applied.connect(self._on_batch)
        self.bridge.status_changed.connect(self._on_status)

        self.setWindowTitle("Заявки — мессенджер мастера")
        self.resize(1180, 760)

        self.tickets: list[TicketBrief] = []
        self.incoming: list[TicketBrief] = []
        self.masters: list[tuple[str, str]] = []

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
        profile = ctx.profile
        identity = profile.full_name if profile else "—"
        status.addWidget(QLabel(f"{identity} · {ctx.config.base_url}"))
        self.dot = StatusDot("подключение…", QColor("#b3b9c2"))
        status.addPermanentWidget(self.dot)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(ctx.config.refresh_seconds * 1000)
        self.refresh_timer.timeout.connect(self._reload)
        self.refresh_timer.start()

        self._reload()
        self.panel.select_incoming()
        self._open_incoming()

    # --- навигация ---

    def _open_incoming(self) -> None:
        self.incoming_view.set_tickets(self.incoming)
        self.stack.setCurrentWidget(self.incoming_view)

    def _open_ticket(self, ticket_id: str) -> None:
        ticket = self.ctx.ticket(ticket_id)
        if ticket is None:
            return
        self._show_ticket(ticket)
        self._mark_unread(ticket)

    def _show_ticket(self, ticket: Ticket) -> None:
        self.panel.select_ticket(ticket.id)
        messages = self.ctx.messages(ticket.id)
        self.masters = member_pairs(ticket)
        self.chat_page.set_masters(self.masters)
        self.chat.show_ticket(ticket, messages)
        self.stack.setCurrentWidget(self.chat_page)

    # --- синхронизация ---

    def _on_batch(self, _batch: object) -> None:
        """Пачка событий ленты применена — перерисовать всё из кэша."""
        self._reload()

    def _on_status(self, status: object) -> None:
        online = bool(getattr(status, "online", True))
        text = getattr(status, "text", "на связи")
        self.dot.set_color(QColor("#2e9e5b") if online else QColor("#d64545"))
        self.dot.set_text(text)

    def _reload(self) -> None:
        open_id = self.chat.ticket.id if self.chat.ticket else None
        self.tickets = self.ctx.panel_tickets()
        self.incoming = self.ctx.feed()
        self.panel.set_data(self.tickets, self.incoming)
        self.incoming_view.set_tickets(self.incoming)
        if open_id is not None:
            self._refresh_open_chat(open_id)

    def _refresh_open_chat(self, ticket_id: str) -> None:
        ticket = self.ctx.ticket(ticket_id)
        if ticket is None:
            return
        messages = self.ctx.messages(ticket_id)
        keep_position = not self.chat.at_bottom()
        self.masters = member_pairs(ticket)
        self.chat_page.set_masters(self.masters)
        self.chat.show_ticket(ticket, messages, scroll_to_bottom=keep_position)

    # --- действия: API в фоне, UI обновляется по готовности ---

    def _send(self, text: str) -> None:
        ticket = self.chat.ticket
        if ticket is None:
            return
        paths = self.chat.composer.attachments.paths
        self.chat.composer.attachments.set_files([])

        baseline = self.ctx.repo.last_message_seq(ticket.id)
        entry = _local_message(self.ctx, ticket, text, paths, baseline)
        self.chat.add_pending(entry, baseline)

        def action() -> Message:
            return self.ctx.send(ticket.id, text, paths)

        def done(message: Message) -> None:
            self.chat.take_pending(entry, confirmed_seq=message.seq)
            self.chat.append_message(message)
            self._reload()

        def error(exc: BaseException) -> None:
            self.chat.take_pending(entry)
            QMessageBox.warning(self, "Не отправлено", str(exc))
            self.chat.composer.input.setPlainText(text)

        self.tasks.submit(action, on_done=done, on_error=error)

    def _accept(self, ticket_id: str) -> None:
        self.tasks.submit(
            lambda: self.ctx.accept(ticket_id),
            on_done=lambda _result: self._open_after_action(ticket_id),
            on_error=self._action_error,
        )

    def _decline(self, ticket_id: str) -> None:
        self.tasks.submit(
            lambda: self.ctx.decline(ticket_id),
            on_done=lambda _result: self._reload(),
            on_error=self._action_error,
        )

    def _close_ticket(self) -> None:
        ticket = self.chat.ticket
        if ticket is None or ticket.is_closed:
            return
        self.tasks.submit(
            lambda: self.ctx.close(ticket.id),
            on_done=lambda _result: self._reload(),
            on_error=self._action_error,
        )

    def _reopen_ticket(self) -> None:
        ticket = self.chat.ticket
        if ticket is None:
            return
        self.tasks.submit(
            lambda: self.ctx.reopen(ticket.id),
            on_done=lambda _result: self._reload(),
            on_error=self._action_error,
        )

    def _more_actions(self) -> None:
        ticket = self.chat.ticket
        if ticket is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Действия с заявкой")
        box.setText(ticket.subject)
        release_button = box.addButton("Вернуть в ленту", QMessageBox.ButtonRole.ActionRole)
        read_button = box.addButton(
            "Отметить прочитанным", QMessageBox.ButtonRole.ActionRole
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is release_button:
            self.tasks.submit(
                lambda: self.ctx.release(ticket.id),
                on_done=lambda _result: self._reload(),
                on_error=self._action_error,
            )
        elif clicked is read_button:
            self.tasks.submit(
                lambda: self.ctx.mark_read(ticket.id),
                on_done=lambda _result: self._reload(),
                on_error=self._action_error,
            )

    def _pick_master(self) -> None:
        directory = self.ctx.master_pairs()
        if not directory:
            QMessageBox.information(
                self, "Мастера", "Справочник мастеров пуст — подключать некого."
            )
            return
        dialog = MasterPicker(directory, self.masters, self)
        if dialog.exec() != MasterPicker.DialogCode.Accepted:
            return
        self._connect_master(dialog.selected_id())

    def _connect_master(self, master_id: str | None) -> None:
        ticket = self.chat.ticket
        if ticket is None or not master_id:
            return
        self.tasks.submit(
            lambda: self.ctx.add_member(ticket.id, master_id),
            on_done=lambda _result: self._reload(),
            on_error=self._action_error,
        )

    def _remove_master(self, master_id: str) -> None:
        ticket = self.chat.ticket
        if ticket is None:
            return
        self.tasks.submit(
            lambda: self.ctx.remove_member(ticket.id, master_id),
            on_done=lambda _result: self._reload(),
            on_error=self._action_error,
        )

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
        strip.set_files(strip.paths + list(paths))

    # --- прочее ---

    def _mark_unread(self, ticket: Ticket) -> None:
        """Гасим бейдж непрочитанных: считаем по кэшу, а не по полю карточки
        (в полной карточке unread_count всегда 0 — его собирает только список)."""
        unread = self.ctx.repo.unread_count(ticket.id)
        if not unread:
            return
        self.tasks.submit(
            lambda: self.ctx.mark_read(ticket.id),
            on_done=lambda _result: self._reload(),
            on_error=self._action_error,
        )

    def _action_error(self, exc: BaseException) -> None:
        QMessageBox.warning(self, "Не получилось", str(exc))

    def _open_after_action(self, ticket_id: str) -> None:
        """После приёма заявки: обновить списки и открыть диалог."""
        self._reload()
        ticket = self.ctx.ticket(ticket_id)
        if ticket is not None:
            self._show_ticket(ticket)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.refresh_timer.stop()
        self.ctx.stop()
        super().closeEvent(event)
