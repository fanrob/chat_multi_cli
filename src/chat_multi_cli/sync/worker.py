"""Фоновый long-poll: единственный поток, который ходит на сервер за событиями.

Что делает поток по кругу:

    курсор из БД → GET /sync (ждёт до wait) → события одной транзакцией в БД →
    REST-догрузка того, чего событие не донесло → сигнал UI.

Почему важна именно эта последовательность:

* Курсор двигается только вместе с записанными событиями. Обрыв между «получили
  события» и «записали» приводит к повтору, а не к потере: доставка at-least-once.
* События, которых не хватает для отрисовки (ticket.created приходит только с
  id), догружаются обычным REST-запросом до того, как UI увидит пакет. Иначе в
  списке на секунду всплыла бы заявка без темы и клиента.
* 410 cursor_expired — не поломка, а сигнал «лента чистилась, перезагрузись».
  Воркер делает полный ресинк по README и продолжает работу, а не падает.

Поток обычный, а не QThread: так его можно тестировать без Qt-цикла событий.
Когда UI подключат, обёртка в QThread + Signal делается в ui/ тремя строками —
логика от этого не меняется.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Final

from chat_multi_cli.api.client import ApiClient
from chat_multi_cli.api.errors import ApiError, NetworkError
from chat_multi_cli.models import (
    Event,
    MasterBrief,
    Message,
    Session,
    Ticket,
    TicketBrief,
    TicketsPage,
)
from chat_multi_cli.storage.repo import Repo
from chat_multi_cli.storage.writer import (
    AppliedBatch,
    WriterThread,
    apply_events,
    read_cursor,
    read_profile,
    save_masters,
    set_cursor,
    upsert_messages,
    upsert_ticket,
)

logger = logging.getLogger(__name__)

#: Обычное время ожидания: совпадает с настройкой клиента и дефолтом сервера.
DEFAULT_WAIT_SECONDS: Final = 25

#: Сколько событий просим за раз (максимум сервера — 500).
DEFAULT_LIMIT: Final = 200

#: Пауза перед первой попыткой после обрыва связи.
BACKOFF_INITIAL: Final = 0.5

#: Потолок паузы: за это время реконнект уже успел отработать.
BACKOFF_MAX: Final = 30.0

#: Во сколько раз растёт пауза после каждой неудачи.
BACKOFF_FACTOR: Final = 2.0

#: Сколько заявок и сообщений берём при полной перезагрузке.
RESYNC_TICKETS_LIMIT: Final = 50
RESYNC_MESSAGES_LIMIT: Final = 100


@dataclass(frozen=True, slots=True)
class SyncStatus:
    """Состояние связи для UI: «нет связи» / «синхронизировано»."""

    online: bool
    cursor: int
    detail: str = ""
    applied: int = 0

    @property
    def text(self) -> str:
        if self.online:
            return "Связь есть"
        return f"Нет связи: {self.detail or 'повторяем'}"


class SyncWorker:
    """Фоновый опрос ленты событий.

    start()/stop() — обычная пара потоков, а не Thread API: так в тестах можно
    поднимать воркер на один-два ответа и гасить, не переживая за Qt.
    """

    def __init__(
        self,
        client: ApiClient,
        writer: WriterThread,
        repo: Repo,
        *,
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        limit: int = DEFAULT_LIMIT,
        backoff_initial: float = BACKOFF_INITIAL,
        backoff_max: float = BACKOFF_MAX,
        backoff_factor: float = BACKOFF_FACTOR,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = client
        self._writer = writer
        self._repo = repo
        self._wait = wait_seconds
        self._limit = limit
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._backoff_factor = backoff_factor
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cursor = 0
        self._failures = 0
        self._listeners: list[Callable[[AppliedBatch], None]] = []
        self._status_listeners: list[Callable[[SyncStatus], None]] = []

    # ---------- Жизненный цикл ----------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("SyncWorker уже запущен")
        self._thread = threading.Thread(target=self.run, name="chat-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> bool:
        """Останавливает опрос. True, если поток завершился.

        Если идёт длинное ожидание, поток сначала доходит до конца запроса:
        прерывать на середине бессмысленно, сервер всё равно уже отправил
        события или нет. Пауза backoff при этом прерывается сразу.
        """
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def cursor(self) -> int:
        """Текущий курсор — его показывают в отладке и в профиле соединения."""
        return self._cursor

    @property
    def session(self) -> Session | None:
        return self._session

    _session: Session | None = None

    # ---------- Подписки ----------

    def on_events(self, callback: Callable[[AppliedBatch], None]) -> None:
        """Вызывается после каждой применённой пачки событий."""
        self._listeners.append(callback)

    def on_status(self, callback: Callable[[SyncStatus], None]) -> None:
        """Вызывается при смене состояния связи, включая обрыв."""
        self._status_listeners.append(callback)

    # ---------- Основной цикл ----------

    def run(self) -> None:
        """Цикл до stop(). Исключения наружу не выпускает: обрывы тут норма."""
        self._cursor = self._writer.run_sync(lambda conn: read_cursor(conn))
        self._session = self._cached_session() or self._fetch_session()
        self._report(online=True, detail="синхронизация запущена")

        while not self._stop.is_set():
            try:
                self._poll_once()
            except ApiError as exc:
                if exc.needs_resync:
                    self._handle_resync(exc)
                else:
                    self._handle_failure(f"{exc.code}: {exc.text}")
            except NetworkError as exc:
                self._handle_failure(str(exc))
            except Exception as exc:
                # Воркер не имеет права умирать: UI останется без ленты навсегда.
                logger.exception("Непредвиденная ошибка в цикле синхронизации")
                self._handle_failure(str(exc))
            else:
                self._failures = 0

    def _poll_once(self) -> None:
        """Один заход: запрос ленты, применение, догрузка остального."""
        result = self._client.sync(cursor=self._cursor, wait=self._wait, limit=self._limit)

        if result.events:
            batch = self._apply(result.events)
            self._cursor = batch.cursor
            self._report(online=True, applied=batch.applied)
            return

        # Пусто (204 после ожидания или 200 при wait=0): сервер уже подождал,
        # повторяем сразу — задержку уже обеспечил сервер.
        self._report(online=True)

    # ---------- Применение событий ----------

    def _apply(self, events: Sequence[Event]) -> AppliedBatch:
        """Записывает пачку и догружает то, чего в событии не было.

        Сначала события (они двигают курсор), потом REST. Обратный порядок дал бы
        «записали курсор, но не дописали заявку» — то есть потерю события без
        повтора.
        """
        batch = self._writer.run_sync(lambda conn: apply_events(conn, events))
        self._refetch_missing(batch)
        return batch

    def _refetch_missing(self, batch: AppliedBatch) -> None:
        """Дочитывает заявки и сообщения, которых не было в событии."""
        for ticket_id in batch.refetch_tickets:
            try:
                ticket = self._client.ticket(ticket_id)
            except ApiError as exc:
                if exc.status_code == 404:
                    # Заявка скрыта от нас (например, её отклонили): в ленте её
                    # показывать нечего, это не ошибка синхронизации.
                    logger.debug("Заявка %s недоступна: %s", ticket_id, exc.code)
                    continue
                raise
            self._writer.run_sync(partial(_upsert_ticket_task, item=ticket))

        for ticket_id in batch.refetch_messages:
            history = self._client.messages(ticket_id, limit=RESYNC_MESSAGES_LIMIT)
            self._writer.run_sync(partial(_upsert_messages_task, items=history.messages))

        if batch.masters_dirty:
            directory = self._client.masters()
            self._writer.run_sync(partial(_save_masters_task, items=directory.masters))

    # ---------- Ресинк после 410 ----------

    def _handle_resync(self, exc: ApiError) -> None:
        """Полная перезагрузка состояния после cursor_expired.

        Порядок принципиален: сначала данные по REST, потом курсор из /session,
        и только один раз. Если поставить курсор раньше, события, пришедшие между
        чтением данных и постановкой курсора, потерялись бы молча.
        """
        logger.warning(
            "Курсор устарел (min_available=%s) — перезагружаем состояние",
            exc.min_available_cursor,
        )
        self._report(online=True, detail="перезагрузка ленты")
        self._failures = 0

        for page in self._load_tickets():
            self._load_tickets_page(page)

        session = self._client.read_session()
        self._session = session
        self._cursor = session.cursor
        self._writer.run_sync(partial(_set_cursor_task, cursor=session.cursor))
        for callback in self._listeners:
            self._call(callback, AppliedBatch(cursor=session.cursor, applied=0))
        self._report(online=True, detail="лента перезагружена")

    def _load_tickets(self) -> list[TicketsPage]:
        """Лента и «мои» заявки: вместе они закрывают то, что было в UI до 410."""
        pages: list[TicketsPage] = []
        for scope in ("feed", "mine"):
            pages.append(self._client.tickets(scope=scope, limit=RESYNC_TICKETS_LIMIT))
        return pages

    def _load_tickets_page(self, page: TicketsPage) -> None:
        """Заявка и её последние сообщения — этого хватает открытому диалогу."""
        for ticket in page.tickets:
            self._writer.run_sync(partial(_upsert_ticket_task, item=ticket))
            history = self._client.messages(ticket.id, limit=RESYNC_MESSAGES_LIMIT)
            self._writer.run_sync(partial(_upsert_messages_task, items=history.messages))

    # ---------- Реконнект ----------

    def _handle_failure(self, detail: str) -> None:
        """Экспоненциальная пауза и уведомление UI."""
        self._failures += 1
        delay = self._backoff_delay()
        logger.warning("Синхронизация не удалась (%s), повтор через %.1fс", detail, delay)
        self._report(online=False, detail=detail)
        self._pause(delay)

    def _backoff_delay(self) -> float:
        delay = self._backoff_initial * (self._backoff_factor ** (self._failures - 1))
        return min(delay, self._backoff_max)

    def _pause(self, delay: float) -> None:
        """Ждёт перед повтором. Через Event.wait, чтобы stop() не ждал паузу."""
        if self._sleep is not None:
            self._sleep(delay)
            return
        self._stop.wait(delay)

    # ---------- Уведомления ----------

    def _report(self, *, online: bool, detail: str = "", applied: int = 0) -> None:
        status = SyncStatus(online=online, cursor=self._cursor, detail=detail, applied=applied)
        for callback in self._status_listeners:
            self._call(callback, status)

    def _call(self, callback: Callable[[Any], None], payload: Any) -> None:
        """Ошибка в подписчике не должна останавливать синхронизацию."""
        try:
            callback(payload)
        except Exception:
            logger.exception("Подписчик синхронизации упал")

    # ---------- Кэш профиля ----------

    def _cached_session(self) -> Session | None:
        """Профиль из кэша: после перезапуска UI знает мастера, не дожидаясь сети."""
        payload = self._writer.run_sync(_read_profile_task)
        if not payload:
            return None
        try:
            return Session.from_payload(payload)
        except ValueError:
            logger.warning("Профиль в кэше не разобрался, будет получен заново")
            return None

    def _fetch_session(self) -> Session | None:
        """Профиль с сервера. Отсутствие не критично: лента придёт в любом случае."""
        try:
            return self._client.read_session()
        except (ApiError, NetworkError) as exc:
            logger.info("Не удалось получить профиль: %s", exc)
            return None


def _upsert_ticket_task(connection: sqlite3.Connection, item: Ticket | TicketBrief) -> None:
    upsert_ticket(connection, item)


def _upsert_messages_task(connection: sqlite3.Connection, items: Iterable[Message]) -> None:
    upsert_messages(connection, items)


def _save_masters_task(connection: sqlite3.Connection, items: Iterable[MasterBrief]) -> None:
    save_masters(connection, items)


def _set_cursor_task(connection: sqlite3.Connection, cursor: int) -> None:
    set_cursor(connection, cursor)


def _read_profile_task(connection: sqlite3.Connection) -> dict[str, Any]:
    return read_profile(connection)
