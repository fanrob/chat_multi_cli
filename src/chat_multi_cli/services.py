"""Сервисный слой: сеть, кэш и воркер синхронизации одним объектом для UI.

AppContext — единственная точка, через которую окно трогает остальной мир:

* чтение — всегда из локального SQLite (Repo), поэтому список заявок не ждёт
  сети и не мерцает при обрыве;
* действия (принять, закрыть, написать) — сначала API, потом запись в кэш
  тем же writer-потоком, которым пользуются события ленты: так в БД не может
  оказаться двух правд о заявке;
* события ленты приходят фоновым SyncWorker'ом, UI подписывается на его
  колбэки отдельно (ui/tasks.py), этот модуль про Qt ничего не знает.

Порядок bootstrap принципиален: сессия (и её cursor) читаются ДО данных, а
курсор записывается ПОЗЖЕ данных. Тогда событие, пришедшее во время загрузки,
и уже учтено в данных, и будет повторно доставлено лентой — at-least-once, а
не потеря.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from chat_multi_cli.api.client import ApiClient
from chat_multi_cli.api.errors import ApiError
from chat_multi_cli.config import AppConfig, ConfigStore
from chat_multi_cli.models import (
    MasterBrief,
    Message,
    Profile,
    Session,
    Ticket,
    TicketBrief,
    Workshop,
)
from chat_multi_cli.storage.db import Database
from chat_multi_cli.storage.repo import Repo
from chat_multi_cli.storage.writer import (
    WriterThread,
    hide_ticket,
    mark_read,
    save_masters,
    save_profile,
    set_cursor,
    upsert_message,
    upsert_messages,
    upsert_ticket,
)
from chat_multi_cli.sync.worker import SyncWorker

logger = logging.getLogger(__name__)

#: Сколько заявок и сообщений берём при первичной загрузке — как и воркер.
BOOTSTRAP_TICKETS_LIMIT = 50
BOOTSTRAP_MESSAGES_LIMIT = 100


class SetupRequired(Exception):
    """Мастер ещё не ввёл ФИО или адрес сервера — нужен диалог настроек."""


class AppContext:
    """Всё состояние приложения вне Qt. Создаётся один раз на запуск."""

    def __init__(
        self,
        store: ConfigStore,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.store = store
        self.config: AppConfig = store.ensure_instance_id()
        paths = store.paths.ensure()

        self.db = Database(paths.database_file)
        self.db.migrate()
        self.writer = WriterThread(self.db)
        self.repo = Repo(self.db)
        self.client = ApiClient(
            self.config.base_url,
            self.config.instance_id,
            transport=transport,
        )
        self.worker = SyncWorker(
            self.client,
            self.writer,
            self.repo,
            wait_seconds=self.config.sync_wait_seconds,
        )

        #: Последний ответ /session; None до bootstrap().
        self.session: Session | None = None
        self._sync_started = False
        self._stopped = False

    # ---------- Жизненный цикл ----------

    def bootstrap(self) -> Session:
        """Вход на сервер и полная первичная загрузка кэша.

        Вызывается один раз на запуск (и заново после смены адреса сервера).
        NetworkError/ApiError наружу: показ ошибки — дело UI.
        """
        if not self.config.ready_for_session:
            missing = [
                name
                for name, value in (
                    ("ФИО", self.config.full_name),
                    ("адрес сервера", self.config.base_url),
                )
                if not value
            ]
            raise SetupRequired("Не заданы: " + ", ".join(missing))

        session = self._open_session()
        self.session = session
        self.repo.my_id = session.master_id
        self.writer.run_sync(partial(save_profile, payload=asdict(session)))

        for scope in ("feed", "mine"):
            page = self.client.tickets(
                scope=scope,
                limit=BOOTSTRAP_TICKETS_LIMIT,
            )
            for item in page.tickets:
                self.writer.run_sync(partial(upsert_ticket, ticket=item))
                history = self.client.messages(
                    item.id,
                    limit=BOOTSTRAP_MESSAGES_LIMIT,
                )
                self.writer.run_sync(partial(upsert_messages, messages=history.messages))

        directory = self.client.masters()
        self.writer.run_sync(partial(save_masters, masters=directory.masters))

        # Данные уже в кэше — теперь можно двигать курсор. Если упали раньше,
        # курсор остался старым и лента при следующем запуске придёт повторно.
        self.writer.run_sync(partial(set_cursor, cursor=session.cursor))
        logger.info(
            "Загрузка завершена: мастер %s, курсор %d",
            session.master_id,
            session.cursor,
        )
        return session

    def start_sync(self) -> None:
        """Запускает фоновый long-poll. Идемпотентно."""
        if self._sync_started or self._stopped:
            return
        self._sync_started = True
        self.worker.start()

    def stop(self) -> None:
        """Останавливает воркер и писателя. Безопасно вызывать повторно."""
        if self._stopped:
            return
        self._stopped = True
        if self._sync_started:
            self.worker.stop()
            self._sync_started = False
        self.writer.stop()
        self.client.shutdown()

    def _open_session(self) -> Session:
        """GET /session, а для нового instance_id — первый POST /session."""
        try:
            return self.client.read_session()
        except ApiError as exc:
            if exc.code != "master_not_found":
                raise
        config = self.store.config
        return self.client.create_session(
            config.full_name,
            config.workshop_id or None,
        )

    # ---------- Чтение (вызывается из UI-потока) ----------

    @property
    def profile(self) -> Profile | None:
        return self.session.profile if self.session else None

    @property
    def master_id(self) -> str:
        return self.session.master_id if self.session else ""

    def feed(self) -> list[TicketBrief]:
        """Входящие: статус new, ещё никто не принял."""
        return self.repo.feed()

    def panel_tickets(self) -> list[TicketBrief]:
        """Заявки для панели слева: мои плюс закрытые."""
        merged: dict[str, TicketBrief] = {item.id: item for item in self.repo.mine()}
        for item in self.repo.closed():
            merged.setdefault(item.id, item)
        return list(merged.values())

    def search(self, query: str) -> list[TicketBrief]:
        return self.repo.search(query)

    def ticket(self, ticket_id: str) -> Ticket | None:
        return self.repo.ticket(ticket_id)

    def messages(self, ticket_id: str, **kwargs: Any) -> list[Message]:
        return self.repo.messages(ticket_id, **kwargs)

    def masters(self) -> list[MasterBrief]:
        return self.repo.masters()

    def master_pairs(self) -> list[tuple[str, str]]:
        """Справочник мастеров парами (id, имя) — формат диалога выбора."""
        return [(item.id, item.full_name) for item in self.repo.masters()]

    def workshops(self) -> list[Workshop]:
        """Цеха сервера; пусто в dev-базе — выбор цеха тогда не показываем."""
        return list(self.client.workshops().workshops)

    # ---------- Действия (вызываются в фоне, UI-поток ждёт сигнала) ----------

    def accept(self, ticket_id: str) -> Ticket:
        """Взять заявку в работу. API без ретраев, затем запись в кэш."""
        ticket = self.client.accept(ticket_id)
        self.writer.run_sync(partial(upsert_ticket, ticket=ticket))
        return ticket

    def decline(self, ticket_id: str) -> None:
        """Скрыть заявку из своей ленты."""
        self.client.decline(ticket_id)
        self.writer.run_sync(partial(hide_ticket, ticket_id=ticket_id))

    def close(self, ticket_id: str) -> Ticket:
        ticket = self.client.close(ticket_id)
        self.writer.run_sync(partial(upsert_ticket, ticket=ticket))
        return ticket

    def reopen(self, ticket_id: str) -> Ticket:
        ticket = self.client.reopen(ticket_id)
        self.writer.run_sync(partial(upsert_ticket, ticket=ticket))
        return ticket

    def release(self, ticket_id: str) -> Ticket:
        """Вернуть заявку в общую ленту."""
        ticket = self.client.release(ticket_id)
        self.writer.run_sync(partial(upsert_ticket, ticket=ticket))
        return ticket

    def send(self, ticket_id: str, text: str, paths: Sequence[str] = ()) -> Message:
        """Загрузить вложения, отправить сообщение, запомнить его в кэше."""
        attachment_ids = [
            self.client.upload_attachment(Path(path), ticket_id=ticket_id).attachment_id
            for path in paths
        ]
        message = self.client.send_message(
            ticket_id,
            text,
            attachments=attachment_ids,
        )
        self.writer.run_sync(partial(upsert_message, message=message))
        return message

    def mark_read(self, ticket_id: str) -> None:
        """Прочитать всё до последнего seq: серверу и в локальный кэш."""
        seq = self.repo.last_message_seq(ticket_id)
        self.client.mark_read(ticket_id, seq)
        self.writer.run_sync(partial(mark_read, ticket_id=ticket_id, seq=seq))

    def add_member(self, ticket_id: str, master_id: str) -> Ticket:
        """Подключить мастера и сразу получить обновлённую карточку.

        Событие member_added обновит кэш чуть позже через ленту, но панель
        участников должна перерисоваться сразу после ответа.
        """
        self.client.add_member(ticket_id, master_id)
        return self._refetch_ticket(ticket_id)

    def remove_member(self, ticket_id: str, master_id: str) -> Ticket:
        self.client.remove_member(ticket_id, master_id)
        return self._refetch_ticket(ticket_id)

    def update_profile(
        self,
        *,
        full_name: str | None = None,
        workshop_id: str | None = None,
    ) -> Session:
        """Смена ФИО/цеха на сервере с сохранением в config.json."""
        session = self.client.update_profile(full_name, workshop_id)
        self.session = session
        self.repo.my_id = session.master_id
        self.writer.run_sync(partial(save_profile, payload=asdict(session)))

        changes: dict[str, Any] = {}
        if full_name is not None:
            changes["full_name"] = full_name
        if workshop_id is not None:
            changes["workshop_id"] = workshop_id
        if changes:
            self.config = self.store.save(self.store.config.with_changes(**changes))
        return session

    # ---------- Внутреннее ----------

    def _refetch_ticket(self, ticket_id: str) -> Ticket:
        ticket = self.client.ticket(ticket_id)
        self.writer.run_sync(partial(upsert_ticket, ticket=ticket))
        return ticket
