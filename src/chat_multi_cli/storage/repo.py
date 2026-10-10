"""Запросы на чтение для UI: список заявок, диалог, счётчики.

Только чтение, поэтому модуль безопасен вызывать из UI-потока: он открывает
своё соединение на поток и ничего не пишет. Все возвращаемые объекты —
frozen dataclass из models, то есть те же типы, что пришли из сети.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any, Final

from chat_multi_cli.models import (
    STATUS_CLOSED,
    STATUS_IN_PROGRESS,
    STATUS_NEW,
    Attachment,
    ClientBrief,
    LastMessage,
    MasterBrief,
    Member,
    Message,
    Ticket,
    TicketBrief,
)
from chat_multi_cli.storage.db import Database, get_meta_flag
from chat_multi_cli.storage.writer import MASTERS_DIRTY_KEY

#: Сколько заявок отдавать по умолчанию — столько же, сколько просит сервер.
DEFAULT_TICKETS_LIMIT: Final = 50

#: Сколько сообщений отдавать по умолчанию.
DEFAULT_MESSAGES_LIMIT: Final = 100

#: Сколько строк читать до фильтрации поиска. Поиск идёт через casefold в
#: Python, поэтому нужен запас: часть кандидатов отсеется по регистру.
SEARCH_CANDIDATES: Final = 500

_TICKET_COLUMNS: Final = """
    t.id, t.status, t.subject, t.text, t.source, t.workshop_id, t.workshop_name,
    t.client_id, t.client_name, t.client_phone, t.owner_id, t.owner_name,
    t.created_at, t.updated_at, t.closed_at, t.close_reason, t.read_seq, t.hidden,
    t.last_message_seq, t.last_message_sender, t.last_message_preview
"""

#: Непрочитанные считаются по messages, а не берутся из записи заявки:
#: в broadcast-событии unread_count считает тот мастер, который его вызвал,
#: а нам нужен свой.
_UNREAD_EXPR: Final = """
    (SELECT COUNT(*) FROM messages m
      WHERE m.ticket_id = t.id AND m.sender = 'client' AND m.seq > t.read_seq)
"""


class Repo:
    """Чтение локального кэша."""

    def __init__(self, database: Database, my_id: str = "") -> None:
        self._db = database
        #: Идентификатор мастера. Проставляется один раз после POST /session:
        #: без него фильтр «мои заявки» не работает.
        self.my_id = my_id

    @property
    def _connection(self) -> sqlite3.Connection:
        return self._db.connect()

    # ---------- Заявки ----------

    def feed(self, *, limit: int = DEFAULT_TICKETS_LIMIT) -> list[TicketBrief]:
        """Лента: заявки в статусе new, которые мастер не скрыл."""
        connection = self._connection
        rows = connection.execute(
            f"""
            SELECT {_TICKET_COLUMNS}
            FROM tickets t
            WHERE t.status = ? AND t.hidden = 0
            ORDER BY t.updated_at DESC
            LIMIT ?
            """,
            (STATUS_NEW, limit),
        ).fetchall()
        return self._to_briefs(connection, rows)

    def mine(self, *, limit: int = DEFAULT_TICKETS_LIMIT) -> list[TicketBrief]:
        """Мои заявки: где я владелец или участник."""
        connection = self._connection
        rows = connection.execute(
            f"""
            SELECT {_TICKET_COLUMNS}
            FROM tickets t
            WHERE t.owner_id = ?
               OR EXISTS (SELECT 1 FROM ticket_members tm
                          WHERE tm.ticket_id = t.id AND tm.master_id = ?)
            ORDER BY CASE t.status WHEN 'in_progress' THEN 0 ELSE 1 END, t.updated_at DESC
            LIMIT ?
            """,
            (self.my_id, self.my_id, limit),
        ).fetchall()
        return self._to_briefs(connection, rows)

    def closed(self, *, limit: int = DEFAULT_TICKETS_LIMIT) -> list[TicketBrief]:
        connection = self._connection
        rows = connection.execute(
            f"""
            SELECT {_TICKET_COLUMNS}
            FROM tickets t
            WHERE t.status = ?
            ORDER BY t.updated_at DESC
            LIMIT ?
            """,
            (STATUS_CLOSED, limit),
        ).fetchall()
        return self._to_briefs(connection, rows)

    def search(self, query: str, *, limit: int = DEFAULT_TICKETS_LIMIT) -> list[TicketBrief]:
        """Поиск по теме, тексту заявки, имени и телефону клиента.

        Ключи сравнения регистронезависимые по-русски: LIKE в SQLite сравнивает
        регистр только для ASCII, поэтому «стекло» не нашло бы «Стекло». Читаем
        кандидатов в ленте и сравниваем через casefold — на локальном кэше из
        сотен строк это быстрее, чем SQL с допущениями о кодировке.
        """
        needle = query.strip().casefold()
        if not needle:
            return []
        connection = self._connection
        rows = connection.execute(
            f"""
            SELECT {_TICKET_COLUMNS}
            FROM tickets t
            WHERE t.hidden = 0
            ORDER BY t.updated_at DESC
            LIMIT ?
            """,
            (SEARCH_CANDIDATES,),
        ).fetchall()

        matched: list[sqlite3.Row] = []
        for row in rows:
            haystack = " ".join(
                str(row[column] or "")
                for column in ("subject", "text", "client_name", "client_phone")
            ).casefold()
            if needle in haystack:
                matched.append(row)
            if len(matched) >= limit:
                break
        return self._to_briefs(connection, matched)

    def ticket(self, ticket_id: str) -> Ticket | None:
        """Полная карточка заявки."""
        connection = self._connection
        row = connection.execute(
            f"SELECT {_TICKET_COLUMNS} FROM tickets t WHERE t.id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            return None
        return self._to_ticket(connection, row)

    def exists(self, ticket_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        return row is not None

    def status(self, ticket_id: str) -> str:
        row = self._connection.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        return str(row["status"]) if row is not None else ""

    # ---------- Сообщения ----------

    def messages(
        self,
        ticket_id: str,
        *,
        after_seq: int | None = None,
        before_seq: int | None = None,
        limit: int = DEFAULT_MESSAGES_LIMIT,
    ) -> list[Message]:
        """История по возрастанию seq.

        after_seq — догрузить новое (обычно после последнего показанного),
        before_seq — подгрузить старое при прокрутке вверх: берём последние
        limit сообщений *до* курсора и разворачиваем, чтобы порядок остался
        по возрастанию seq.
        """
        clauses = ["ticket_id = ?"]
        params: list[Any] = [ticket_id]
        if after_seq is not None:
            clauses.append("seq > ?")
            params.append(after_seq)
        if before_seq is not None:
            clauses.append("seq < ?")
            params.append(before_seq)

        backwards = before_seq is not None and after_seq is None
        rows = self._connection.execute(
            f"""
            SELECT * FROM messages
            WHERE {" AND ".join(clauses)}
            ORDER BY seq {"DESC" if backwards else "ASC"}
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        messages = [_to_message(row) for row in rows]
        if backwards:
            messages.reverse()
        return messages

    def last_message_seq(self, ticket_id: str) -> int:
        row = self._connection.execute(
            "SELECT MAX(seq) AS last FROM messages WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return int(row["last"]) if row is not None and row["last"] is not None else 0

    def unread_count(self, ticket_id: str) -> int:
        """Сколько сообщений клиента мастер ещё не прочитал."""
        row = self._connection.execute(
            f"SELECT {_UNREAD_EXPR} AS unread FROM tickets t WHERE t.id = ?",
            (ticket_id,),
        ).fetchone()
        return int(row["unread"]) if row is not None else 0

    def is_hidden(self, ticket_id: str) -> bool:
        row = self._connection.execute(
            "SELECT hidden FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        return bool(row["hidden"]) if row is not None else False

    # ---------- Прочее ----------

    def masters(self) -> list[MasterBrief]:
        rows = self._connection.execute("SELECT * FROM masters ORDER BY full_name").fetchall()
        return [
            MasterBrief(
                id=str(row["id"]),
                full_name=str(row["full_name"]),
                workshop_id=row["workshop_id"],
                workshop_name=row["workshop_name"],
                online=bool(row["online"]),
                active_tickets=int(row["active_tickets"]),
            )
            for row in rows
        ]

    def masters_dirty(self) -> bool:
        return get_meta_flag(self._connection, MASTERS_DIRTY_KEY)

    def counts(self) -> dict[str, int]:
        """Счётчики для бейджей: сколько в ленте, сколько моих, сколько закрыто."""
        rows = self._connection.execute(
            "SELECT status, COUNT(*) AS total FROM tickets WHERE hidden = 0 GROUP BY status"
        ).fetchall()
        counts = {STATUS_NEW: 0, STATUS_IN_PROGRESS: 0, STATUS_CLOSED: 0}
        for row in rows:
            counts[str(row["status"])] = int(row["total"])
        return counts

    def unread_total(self) -> int:
        """Всего непрочитанных по всем заявкам — для бейджа окна."""
        row = self._connection.execute(
            f"SELECT SUM(unread) AS total FROM (SELECT {_UNREAD_EXPR} AS unread FROM tickets t)"
        ).fetchone()
        return int(row["total"]) if row is not None and row["total"] is not None else 0

    # ---------- Внутреннее ----------

    def _to_briefs(
        self,
        connection: sqlite3.Connection,
        rows: Sequence[sqlite3.Row],
    ) -> list[TicketBrief]:
        """Собирает список заявок вместе с непрочитанными.

        Счётчики берём одним запросом на весь список, а не по одному на строку:
        в ленте до 50 заявок, и 50 дополнительных SELECT на каждый перерисовку
        списка — лишняя работа на ровном месте.
        """
        if not rows:
            return []
        unread = self._unread_map(connection, [str(row["id"]) for row in rows])
        briefs: list[TicketBrief] = []
        for row in rows:
            ticket = self._to_ticket(connection, row)
            briefs.append(
                TicketBrief(
                    id=ticket.id,
                    status=ticket.status,
                    subject=ticket.subject,
                    created_at=ticket.created_at,
                    updated_at=ticket.updated_at,
                    text=ticket.text,
                    client=ticket.client,
                    workshop_id=ticket.workshop_id,
                    workshop_name=ticket.workshop_name,
                    owner=ticket.owner,
                    members=ticket.members,
                    last_message=ticket.last_message,
                    unread_count=unread.get(ticket.id, 0),
                )
            )
        return briefs

    def _unread_map(self, connection: sqlite3.Connection, ticket_ids: list[str]) -> dict[str, int]:
        placeholders = ", ".join("?" for _ in ticket_ids)
        rows = connection.execute(
            f"""
            SELECT m.ticket_id AS ticket_id, COUNT(*) AS total
            FROM messages m
            JOIN tickets t ON t.id = m.ticket_id
            WHERE m.sender = 'client'
              AND m.ticket_id IN ({placeholders})
              AND m.seq > t.read_seq
            GROUP BY m.ticket_id
            """,
            ticket_ids,
        ).fetchall()
        return {str(row["ticket_id"]): int(row["total"]) for row in rows}

    def _to_ticket(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Ticket:
        ticket_id = str(row["id"])
        client = None
        if row["client_id"]:
            client = ClientBrief(
                id=str(row["client_id"]),
                display_name=str(row["client_name"]),
                phone=row["client_phone"],
            )

        owner = None
        if row["owner_id"]:
            owner = MasterBrief(
                id=str(row["owner_id"]),
                full_name=str(row["owner_name"]),
                workshop_id=row["workshop_id"],
                workshop_name=row["workshop_name"],
            )

        last_message = None
        if row["last_message_seq"]:
            last_message = LastMessage(
                seq=int(row["last_message_seq"]),
                sender=str(row["last_message_sender"] or "client"),
                preview=str(row["last_message_preview"] or ""),
            )
        else:
            # Сервер может не слать last_message у только что созданной заявки,
            # хотя сообщения в ленте уже пришли (например, это событие опередило
            # догрузку карточки). Тогда превью собираем из локальной истории.
            last_message = _last_message_from_history(connection, ticket_id)

        members = [
            Member(
                master_id=str(member["master_id"]),
                full_name=str(member["full_name"]),
                role=str(member["role"]),
                joined_at=str(member["joined_at"]),
            )
            for member in connection.execute(
                "SELECT * FROM ticket_members WHERE ticket_id = ? ORDER BY joined_at",
                (ticket_id,),
            ).fetchall()
        ]

        return Ticket(
            id=ticket_id,
            status=str(row["status"]),
            subject=str(row["subject"]),
            text=str(row["text"]),
            source=str(row["source"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            client=client,
            workshop_id=row["workshop_id"],
            workshop_name=row["workshop_name"],
            owner=owner,
            members=tuple(members),
            last_message=last_message,
            unread_count=0,
            closed_at=row["closed_at"],
            close_reason=row["close_reason"],
        )


def _last_message_from_history(
    connection: sqlite3.Connection,
    ticket_id: str,
) -> LastMessage | None:
    """Последнее сообщение заявки из локальной истории.

    Нужно как запасной источник превью: сервер не всегда присылает
    last_message в карточке, но история сообщений у клиента уже есть.
    """
    row = connection.execute(
        "SELECT seq, sender, text FROM messages WHERE ticket_id = ? ORDER BY seq DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    preview = str(row["text"]).strip().replace("\n", " ")[:200]
    return LastMessage(
        seq=int(row["seq"]),
        sender=str(row["sender"]),
        preview=preview,
    )


def _to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=str(row["id"]),
        seq=int(row["seq"]),
        ticket_id=str(row["ticket_id"]),
        sender=str(row["sender"]),
        sender_name=str(row["sender_name"]),
        text=str(row["text"]),
        created_at=str(row["created_at"]),
        attachments=_read_attachments(str(row["attachments"])),
        sender_master_id=row["sender_master_id"],
        delivery=str(row["delivery"]),
        read_at=row["read_at"],
    )


def _read_attachments(raw: str) -> tuple[Attachment, ...]:
    """Вложения хранятся JSON-строкой; битый JSON не должен ронять диалог."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ()
    if not isinstance(parsed, list):
        return ()
    attachments: list[Attachment] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        attachments.append(
            Attachment(
                attachment_id=str(item.get("attachment_id", "")),
                kind=str(item.get("kind", "file")),
                filename=str(item.get("filename", "")),
                mime_type=str(item.get("mime_type", "")),
                size=int(item.get("size", 0) or 0),
                source=str(item.get("source", "master")),
                created_at="",
            )
        )
    return tuple(attachments)
