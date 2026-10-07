"""Единственный писатель в SQLite + применение событий ленты.

Почему writer отдельным потоком, а не «просто пиши где удобно»:

* sqlite3-соединение нельзя использовать из двух потоков, а UI, sync-воркер и
  отправка сообщений живут в разных потоках. Один писатель снимает вопрос
  целиком: читать могут все, писать — только он.
* События ленты применяются пачкой одной транзакцией. Если в середине пачки
  что-то сломалось, не применяется ничего и курсор остаётся на месте — на
  следующем запросе сервер пришлёт те же события ещё раз. Полудучая лента
  (сообщение есть, а «принята» — нет) мастеру хуже, чем повтор.

Все функции записи принимают уже открытое соединение и не открывают свои
транзакции: транзакцией владеет вызывающий (WriterThread.run_sync оборачивает
задачу в BEGIN/COMMIT).
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Generic, TypeVar, cast

from chat_multi_cli.models import (
    Attachment,
    Event,
    MasterBrief,
    Message,
    Ticket,
    TicketBrief,
)
from chat_multi_cli.storage.db import Database, get_int_meta, get_meta, now_iso, set_meta

logger = logging.getLogger(__name__)

#: Типы событий сервера (api/events.py). Дублируются здесь намеренно: клиент
#: не должен импортировать серверный пакет, и при появлении нового типа
#: сервера мы узнаем о нём из лога, а не из ImportError.
TICKET_CREATED: Final = "ticket.created"
TICKET_ACCEPTED: Final = "ticket.accepted"
TICKET_RELEASED: Final = "ticket.released"
TICKET_CLOSED: Final = "ticket.closed"
TICKET_REOPENED: Final = "ticket.reopened"
TICKET_MEMBER_ADDED: Final = "ticket.member_added"
TICKET_MEMBER_REMOVED: Final = "ticket.member_removed"
MESSAGE_CREATED: Final = "message.created"
MESSAGE_UPDATED: Final = "message.updated"
MASTERS_CHANGED: Final = "masters.directory_changed"

#: События, в которых сервер не присылает тело заявки: клиент обязан досмотреть
#: её по REST, иначе в списке будет заявка без темы и клиента.
TICKET_REFETCH_TYPES: Final[frozenset[str]] = frozenset(
    {TICKET_CREATED, TICKET_MEMBER_ADDED, TICKET_MEMBER_REMOVED}
)

#: Ключи в таблице meta.
CURSOR_KEY: Final = "sync_cursor"
MASTERS_DIRTY_KEY: Final = "masters_dirty"
PROFILE_KEY: Final = "profile_json"

#: Сколько seq держим в applied_events. Нужно для дедупликации, но таблица
#: не должна расти вечно: события старше этого хвоста сервер всё равно
#: пришлёт заново только после 410.
APPLIED_KEEP: Final = 20_000

#: Тип результата задачи писателя — она может вернуть что угодно (курсор,
#: AppliedBatch, количество записей), и вызывающий знает, чего ждёт.
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AppliedBatch:
    """Итог применения пачки событий.

    refetch_tickets / refetch_messages — то, что событие не донесло и придётся
    забрать обычным REST-запросом (ticket.created приходит только с id).
    masters_dirty — справочник мастеров изменился, его надо обновить.
    """

    cursor: int
    applied: int
    refetch_tickets: tuple[str, ...] = ()
    refetch_messages: tuple[str, ...] = ()
    masters_dirty: bool = False

    @property
    def needs_rest_requests(self) -> bool:
        return bool(self.refetch_tickets or self.refetch_messages or self.masters_dirty)


@dataclass(slots=True)
class _Outcome(Generic[T]):  # noqa: UP046 - проект собирается под 3.11, PEP 695 там нет
    """Результат задачи писателя: значение или исключение для вызывающего."""

    value: T | None = None
    error: BaseException | None = None
    done: threading.Event = field(default_factory=threading.Event)


@dataclass(slots=True)
class _Task:
    fn: Callable[[sqlite3.Connection], Any]
    outcome: _Outcome[Any] | None = None


class WriterThread:
    """Поток-писатель: очередь задач, одна транзакция на задачу."""

    def __init__(self, database: Database, *, name: str = "chat-cache-writer") -> None:
        self._db = database
        self._queue: queue.Queue[_Task | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._stopped = threading.Event()
        self._thread.start()

    # ---------- Публичный интерфейс ----------

    def submit(self, fn: Callable[[sqlite3.Connection], Any]) -> None:
        """Кладёт задачу без ожидания результата (удобно для fire-and-forget)."""
        if self._stopped.is_set():
            raise RuntimeError("WriterThread остановлен")
        self._queue.put(_Task(fn=fn))

    def run_sync(self, fn: Callable[[sqlite3.Connection], T], timeout: float = 15.0) -> T:
        """Выполняет задачу и ждёт результат.

        Нужен там, где вызывающему нужен результат записи: применение событий
        возвращает новый курсор, и UI должен знать, что показать.
        """
        if self._stopped.is_set():
            raise RuntimeError("WriterThread остановлен")
        outcome: _Outcome[T] = _Outcome()
        self._queue.put(_Task(fn=fn, outcome=outcome))
        if not outcome.done.wait(timeout):
            raise TimeoutError(f"WriterThread не ответил за {timeout}с")
        if outcome.error is not None:
            raise outcome.error
        return cast(T, outcome.value)

    def stop(self, timeout: float = 10.0) -> None:
        """Останавливает поток и закрывает его соединение с БД."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._queue.put(None)
        self._thread.join(timeout)
        if self._thread.is_alive():
            logger.warning("Поток писателя не остановился за %.1fс", timeout)
        self._db.close()

    # ---------- Внутреннее ----------

    def _run(self) -> None:
        connection = self._db.connect()
        while True:
            task = self._queue.get()
            if task is None:
                self._db.close()
                return
            try:
                with self._db.transaction(connection):
                    outcome_value = task.fn(connection)
            except BaseException as exc:
                logger.exception("Задача писателя упала: %s", exc)
                if task.outcome is not None:
                    task.outcome.error = exc
            else:
                if task.outcome is not None:
                    task.outcome.value = outcome_value
            finally:
                if task.outcome is not None:
                    task.outcome.done.set()


# ==================== Запись состояния ====================


def apply_events(connection: sqlite3.Connection, events: Sequence[Event]) -> AppliedBatch:
    """Применяет пачку событий и двигает курсор. Возвращает, что получилось.

    Порядок — строго по возрастанию seq, как отдаёт сервер: «закрыта» не должна
    примениться раньше «принята». Внутри порядок страхуется сортировкой,
    потому что cursor_expired-ресинк и повторные попытки могут прислать пачку
    с перестановками.
    """
    cursor = get_int_meta(connection, CURSOR_KEY)
    applied = 0
    refetch_tickets: list[str] = []
    refetch_messages: list[str] = []
    masters_dirty = False
    stamp = now_iso()

    for event in sorted(events, key=lambda item: item.seq):
        if event.seq <= cursor:
            continue
        # At-least-once: тот же seq второй раз не применяем.
        inserted = connection.execute(
            "INSERT OR IGNORE INTO applied_events (seq, type, ticket_id, applied_at) "
            "VALUES (?, ?, ?, ?)",
            (event.seq, event.type, event.ticket_id, stamp),
        ).rowcount
        if inserted == 0:
            continue

        cursor = event.seq
        applied += 1
        masters_dirty = _apply_one(connection, event, refetch_tickets, refetch_messages) or (
            masters_dirty
        )

    set_meta(connection, CURSOR_KEY, str(cursor))
    _prune_applied(connection)
    if masters_dirty:
        set_meta(connection, MASTERS_DIRTY_KEY, "1")

    return AppliedBatch(
        cursor=cursor,
        applied=applied,
        refetch_tickets=tuple(dict.fromkeys(refetch_tickets)),
        refetch_messages=tuple(dict.fromkeys(refetch_messages)),
        masters_dirty=masters_dirty,
    )


def _apply_one(
    connection: sqlite3.Connection,
    event: Event,
    refetch_tickets: list[str],
    refetch_messages: list[str],
) -> bool:
    """Применяет одно событие.

    Возвращает True, если изменился справочник мастеров. Списки refetch_* наполняются
    на месте: события, которые не донесли тело заявки или сообщения, помечаются
    для догрузки обычными REST-запросами.
    """
    data = event.data

    if event.type == TICKET_CREATED:
        # Сервер шлёт только id заявки: тело забираем отдельным REST-запросом.
        # Если вдруг придёт полный объект (смена версии сервера), применяем сразу —
        # лишний запрос на каждую заявку не нужен.
        raw_ticket = data.get("ticket")
        if isinstance(raw_ticket, Mapping):
            upsert_ticket(connection, Ticket.from_payload(raw_ticket))
            return False
        ticket_id = str(data.get("ticket_id") or event.ticket_id or "")
        if ticket_id:
            refetch_tickets.append(ticket_id)
        return False

    if event.type in (TICKET_ACCEPTED, TICKET_RELEASED, TICKET_CLOSED, TICKET_REOPENED):
        raw_ticket = data.get("ticket")
        if isinstance(raw_ticket, Mapping):
            upsert_ticket(connection, Ticket.from_payload(raw_ticket))
            return False
        # Закрытие от Telegram-бота приходит вообще без тела заявки: ни статуса,
        # ни причины. Статус выставляем сразу (он однозначен), остальное добираем
        # по GET /tickets/{id}.
        ticket_id = str(data.get("ticket_id") or event.ticket_id or "")
        if not ticket_id:
            return False
        if event.type == TICKET_CLOSED:
            connection.execute(
                "UPDATE tickets SET status = 'closed', updated_at = ? WHERE id = ?",
                (now_iso(), ticket_id),
            )
        refetch_tickets.append(ticket_id)
        return False

    if event.type == MESSAGE_CREATED:
        raw_message = data.get("message")
        if raw_message is None:
            ticket_id = str(event.ticket_id or "")
            if ticket_id:
                refetch_messages.append(ticket_id)
            return False
        upsert_message(connection, Message.from_payload(raw_message))
        return False

    if event.type == MESSAGE_UPDATED:
        # Событие доставки: без seq, найти можно только по message_id.
        message_id = str(data.get("message_id") or "")
        if not message_id:
            return False
        if _apply_delivery(connection, message_id, data) == 0:
            ticket_id = str(event.ticket_id or "")
            if ticket_id:
                refetch_messages.append(ticket_id)
        return False

    if event.type == TICKET_MEMBER_ADDED:
        member = data.get("member")
        ticket_id = str(event.ticket_id or "")
        if isinstance(member, Mapping) and ticket_id:
            _upsert_member(connection, ticket_id, member)
            refetch_tickets.append(ticket_id)
        return False

    if event.type == TICKET_MEMBER_REMOVED:
        master_id = str(data.get("master_id") or "")
        ticket_id = str(event.ticket_id or "")
        if ticket_id and master_id:
            connection.execute(
                "DELETE FROM ticket_members WHERE ticket_id = ? AND master_id = ?",
                (ticket_id, master_id),
            )
            refetch_tickets.append(ticket_id)
        return False

    if event.type == MASTERS_CHANGED:
        master_id = str(data.get("master_id") or "")
        if master_id:
            connection.execute(
                "INSERT INTO masters (id, full_name) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET full_name = excluded.full_name",
                (master_id, str(data.get("full_name") or "")),
            )
        return True

    logger.debug("Неизвестный тип события %s (seq=%d) — пропущен", event.type, event.seq)
    return False


def _apply_delivery(
    connection: sqlite3.Connection,
    message_id: str,
    data: Mapping[str, Any],
) -> int:
    delivery = str(data.get("delivery") or "")
    read_at = data.get("read_at")
    read_value = read_at if isinstance(read_at, str) and read_at else None
    if not delivery:
        return connection.execute(
            "UPDATE messages SET read_at = COALESCE(?, read_at) WHERE id = ?",
            (read_value, message_id),
        ).rowcount
    return connection.execute(
        "UPDATE messages SET delivery = ?, read_at = COALESCE(?, read_at) WHERE id = ?",
        (delivery, read_value, message_id),
    ).rowcount


def upsert_ticket(connection: sqlite3.Connection, ticket: Ticket | TicketBrief) -> None:
    """Записывает заявку целиком и пересобирает список участников.

    Участников проще перезаписать, чем сравнивать: в карточке их всегда полный
    список, а состав меняется редко.

    TicketBrief (список заявок) не знает текста первого сообщения и даты
    закрытия — эти поля при обновлении не затираются.
    """
    full = ticket if isinstance(ticket, Ticket) else None
    owner = ticket.owner
    connection.execute(
        """
        INSERT INTO tickets (
            id, status, subject, text, source, workshop_id, workshop_name,
            client_id, client_name, client_phone, owner_id, owner_name,
            created_at, updated_at, closed_at, close_reason,
            last_message_seq, last_message_sender, last_message_preview
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            subject = excluded.subject,
            text = CASE WHEN excluded.text != '' THEN excluded.text ELSE tickets.text END,
            source = CASE WHEN excluded.source != '' THEN excluded.source ELSE tickets.source END,
            workshop_id = excluded.workshop_id,
            workshop_name = excluded.workshop_name,
            client_id = excluded.client_id,
            client_name = excluded.client_name,
            client_phone = excluded.client_phone,
            owner_id = excluded.owner_id,
            owner_name = excluded.owner_name,
            updated_at = excluded.updated_at,
            closed_at = excluded.closed_at,
            close_reason = excluded.close_reason,
            last_message_seq = MAX(tickets.last_message_seq, excluded.last_message_seq),
            last_message_sender = CASE
                WHEN excluded.last_message_seq >= tickets.last_message_seq
                THEN excluded.last_message_sender ELSE tickets.last_message_sender END,
            last_message_preview = CASE
                WHEN excluded.last_message_seq >= tickets.last_message_seq
                THEN excluded.last_message_preview ELSE tickets.last_message_preview END
        """,
        (
            ticket.id,
            ticket.status,
            ticket.subject,
            full.text if full else "",
            full.source if full else "",
            ticket.workshop_id,
            ticket.workshop_name,
            ticket.client.id if ticket.client else None,
            ticket.client.display_name if ticket.client else "",
            ticket.client.phone if ticket.client else None,
            owner.id if owner else None,
            owner.full_name if owner else "",
            ticket.created_at,
            ticket.updated_at,
            full.closed_at if full else None,
            full.close_reason if full else None,
            ticket.last_message.seq if ticket.last_message else 0,
            ticket.last_message.sender if ticket.last_message else None,
            ticket.last_message.preview if ticket.last_message else "",
        ),
    )

    connection.execute("DELETE FROM ticket_members WHERE ticket_id = ?", (ticket.id,))
    for member in ticket.members:
        _upsert_member(
            connection,
            ticket.id,
            {
                "master_id": member.master_id,
                "full_name": member.full_name,
                "role": member.role,
                "joined_at": member.joined_at,
            },
        )
    _sync_master(connection, owner)


def _upsert_member(
    connection: sqlite3.Connection,
    ticket_id: str,
    member: Mapping[str, Any],
) -> None:
    master_id = str(member.get("master_id") or "")
    if not ticket_id or not master_id:
        return
    connection.execute(
        """
        INSERT INTO ticket_members (ticket_id, master_id, full_name, role, joined_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticket_id, master_id) DO UPDATE SET
            full_name = excluded.full_name,
            role = excluded.role,
            joined_at = excluded.joined_at
        """,
        (
            ticket_id,
            master_id,
            str(member.get("full_name") or ""),
            str(member.get("role") or "collaborator"),
            str(member.get("joined_at") or ""),
        ),
    )
    _sync_master(
        connection,
        None,
        master_id=master_id,
        full_name=str(member.get("full_name") or ""),
    )


def _sync_master(
    connection: sqlite3.Connection,
    master: MasterBrief | None = None,
    *,
    master_id: str = "",
    full_name: str = "",
) -> None:
    """Кэширует мастера из карточки заявки, чтобы показывать имя без REST.

    Карточка заявки знает про мастера меньше, чем справочник (нет workshop при
    member), поэтому пустые поля не затирают уже сохранённые — COALESCE и
    CASE в UPSERT это и делают.
    """
    if master is not None:
        master_id = master.id
        full_name = master.full_name
    elif not master_id:
        return

    if master is None:
        # Событие участника знает только id и имя. Онлайн и число заявок здесь
        # неизвестны, и затирать ими уже загруженные данные нельзя: колонки
        # NOT NULL, поэтому COALESCE и обновляем только то, что знаем.
        connection.execute(
            """
            INSERT INTO masters (id, full_name, online, active_tickets)
            VALUES (?, ?, 0, 0)
            ON CONFLICT(id) DO UPDATE SET
                full_name = CASE WHEN excluded.full_name != '' THEN excluded.full_name
                                 ELSE masters.full_name END
            """,
            (master_id, full_name),
        )
        return

    connection.execute(
        """
        INSERT INTO masters (id, full_name, workshop_id, workshop_name, online, active_tickets)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            full_name = excluded.full_name,
            workshop_id = COALESCE(excluded.workshop_id, masters.workshop_id),
            workshop_name = COALESCE(excluded.workshop_name, masters.workshop_name),
            online = excluded.online,
            active_tickets = excluded.active_tickets
        """,
        (
            master.id,
            master.full_name,
            master.workshop_id,
            master.workshop_name,
            1 if master.online else 0,
            master.active_tickets,
        ),
    )


def upsert_messages(
    connection: sqlite3.Connection,
    messages: Iterable[Message],
) -> int:
    """Записывает пачку сообщений (ответы REST при ресинке). Возвращает количество."""
    count = 0
    for message in messages:
        upsert_message(connection, message)
        count += 1
    return count


def upsert_message(connection: sqlite3.Connection, message: Message) -> None:
    """Записывает сообщение и подтягивает превью заявки.

    Превью в списке заявок нужно, чтобы не делать запрос истории на каждую
    строку списка, поэтому last_message_* обновляется здесь же.
    """
    connection.execute(
        """
        INSERT INTO messages (
            id, ticket_id, seq, sender, sender_name, sender_master_id,
            text, attachments, created_at, delivery, read_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            sender = excluded.sender,
            sender_name = excluded.sender_name,
            sender_master_id = excluded.sender_master_id,
            text = excluded.text,
            attachments = excluded.attachments,
            delivery = excluded.delivery,
            read_at = COALESCE(excluded.read_at, messages.read_at)
        """,
        (
            message.id,
            message.ticket_id,
            message.seq,
            message.sender,
            message.sender_name,
            message.sender_master_id,
            message.text,
            _dump_attachments(message.attachments),
            message.created_at,
            message.delivery,
            message.read_at,
        ),
    )

    preview = message.text.strip().replace("\n", " ")[:200]
    connection.execute(
        """
        UPDATE tickets SET
            last_message_seq = ?,
            last_message_sender = ?,
            last_message_preview = ?,
            updated_at = CASE WHEN ? > updated_at THEN ? ELSE updated_at END
        WHERE id = ? AND ? >= last_message_seq
        """,
        (
            message.seq,
            message.sender,
            preview,
            message.created_at,
            message.created_at,
            message.ticket_id,
            message.seq,
        ),
    )


def mark_read(connection: sqlite3.Connection, ticket_id: str, seq: int) -> None:
    """Отмечает прочитанным до seq. Монотонно: назад не откатываем."""
    connection.execute(
        "UPDATE tickets SET read_seq = MAX(read_seq, ?) WHERE id = ?",
        (seq, ticket_id),
    )


def hide_ticket(connection: sqlite3.Connection, ticket_id: str) -> None:
    """Прячет заявку из ленты (мастер нажал «скрыть»)."""
    connection.execute("UPDATE tickets SET hidden = 1 WHERE id = ?", (ticket_id,))


def show_ticket(connection: sqlite3.Connection, ticket_id: str) -> None:
    connection.execute("UPDATE tickets SET hidden = 0 WHERE id = ?", (ticket_id,))


def set_cursor(connection: sqlite3.Connection, cursor: int) -> None:
    """Ставит курсор явно — после полного ресинка он берётся из /session."""
    set_meta(connection, CURSOR_KEY, str(max(0, cursor)))


def save_profile(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
    set_meta(connection, PROFILE_KEY, json.dumps(payload, ensure_ascii=False))


def save_masters(connection: sqlite3.Connection, masters: Iterable[MasterBrief]) -> int:
    """Перезаписывает справочник мастеров ответом GET /masters."""
    count = 0
    for master in masters:
        _sync_master(connection, master)
        count += 1
    set_meta(connection, MASTERS_DIRTY_KEY, "0")
    return count


def _dump_attachments(attachments: Iterable[Attachment]) -> str:
    """Вложения храним JSON-строкой: пользоваться ими будут только позже."""
    return json.dumps(
        [
            {
                "attachment_id": item.attachment_id,
                "kind": item.kind,
                "filename": item.filename,
                "mime_type": item.mime_type,
                "size": item.size,
                "source": item.source,
            }
            for item in attachments
        ],
        ensure_ascii=False,
    )


def _prune_applied(connection: sqlite3.Connection, keep: int = APPLIED_KEEP) -> None:
    """Подрезает хвост applied_events, оставляя последние keep seq."""
    row = connection.execute("SELECT MAX(seq) AS last FROM applied_events").fetchone()
    last = int(row["last"]) if row is not None and row["last"] is not None else 0
    if last <= keep:
        return
    connection.execute("DELETE FROM applied_events WHERE seq <= ?", (last - keep,))


# ---------- Чтение для писателя (мелочи, нужные воркеру) ----------


def read_cursor(connection: sqlite3.Connection) -> int:
    return get_int_meta(connection, CURSOR_KEY)


def read_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    raw = get_meta(connection, PROFILE_KEY)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def consume_masters_dirty(connection: sqlite3.Connection) -> bool:
    """Проверяет флаг «справочник мастеров устарел» и сбрасывает его."""
    dirty = get_meta(connection, MASTERS_DIRTY_KEY) == "1"
    if dirty:
        set_meta(connection, MASTERS_DIRTY_KEY, "0")
    return dirty
