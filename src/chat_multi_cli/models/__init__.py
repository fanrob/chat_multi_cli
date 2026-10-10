"""Модели данных клиента: то, что приходит по сети и лежит в SQLite.

Все модели — frozen dataclass: они неизменяемы, их безопасно отдавать в UI из
другого потока, и кэш не нужно защищать от случайной правки на месте.

Разбор JSON сделан строгим, но без жёсткой схемы: обязательные поля падают с
PayloadError (значит, сервер отдаёт не то, что мы ждём — это наш баг), а
незнакомые поля просто игнорируются (сервер вправе добавлять поля, и клиент
не должен падать из-за этого).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

#: Статусы заявки (README, раздел 5.1).
STATUS_NEW: Final = "new"
STATUS_IN_PROGRESS: Final = "in_progress"
STATUS_CLOSED: Final = "closed"

STATUS_LABELS: Final[Mapping[str, str]] = {
    STATUS_NEW: "В ленте",
    STATUS_IN_PROGRESS: "В работе",
    STATUS_CLOSED: "Закрыта",
}

#: Отправитель сообщения.
SENDER_CLIENT: Final = "client"
SENDER_MASTER: Final = "master"

SENDER_LABELS: Final[Mapping[str, str]] = {
    SENDER_CLIENT: "Клиент",
    SENDER_MASTER: "Мастер",
}

#: Состояние доставки сообщения мастера (очередь Telegram на сервере).
DELIVERY_QUEUED: Final = "queued"
DELIVERY_SENDING: Final = "sending"
DELIVERY_SENT: Final = "sent"
DELIVERY_FAILED: Final = "failed"

DELIVERY_LABELS: Final[Mapping[str, str]] = {
    DELIVERY_QUEUED: "в очереди",
    DELIVERY_SENDING: "отправляется",
    DELIVERY_SENT: "отправлено",
    DELIVERY_FAILED: "не отправлено",
}


class PayloadError(ValueError):
    """Ответ сервера не соответствует контракту."""


# ==================== Разбор JSON ====================


def _obj(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PayloadError(f"{where}: ожидался объект, получен {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _items(value: Any, where: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PayloadError(f"{where}: ожидался список, получен {type(value).__name__}")
    return list(value)


def _str(data: Mapping[str, Any], key: str, where: str, default: str | None = None) -> str:
    """Обязательная строка. default используется, когда сервер поле не шлёт.

    Так сделано для полей вроде role в карточке мастера: в полном справочнике
    их нет, а в карточке заявки есть — и оба ответа должны собираться.
    """
    value = data.get(key)
    if isinstance(value, str):
        return value
    if value is None and default is not None:
        return default
    raise PayloadError(f"{where}.{key}: ожидалась строка")


def _opt_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _int(data: Mapping[str, Any], key: str, where: str, default: int = 0) -> int:
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise PayloadError(f"{where}.{key}: ожидалось целое число")
    return value


def _opt_int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def label(labels: Mapping[str, str], value: str) -> str:
    """Человеческое название значения или само значение как запасной вариант."""
    return labels.get(value, value)


# ==================== Сущности ====================


@dataclass(frozen=True, slots=True)
class Attachment:
    """Вложение в сообщении.

    width/height сервер пока всегда отдаёт null (фото не разбираются на сервере),
    но поля в контракте есть — держим их, чтобы UI не переделывать потом.
    """

    attachment_id: str
    kind: str
    filename: str
    mime_type: str
    size: int
    source: str
    created_at: str
    width: int | None = None
    height: int | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> Attachment:
        where = "attachment"
        data = _obj(raw, where)
        return cls(
            attachment_id=_str(data, "attachment_id", where),
            kind=_str(data, "kind", where),
            filename=_str(data, "filename", where),
            mime_type=_str(data, "mime_type", where),
            size=_int(data, "size", where),
            source=_str(data, "source", where),
            created_at=_str(data, "created_at", where),
            width=_opt_int(data, "width"),
            height=_opt_int(data, "height"),
        )


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    seq: int
    ticket_id: str
    sender: str
    sender_name: str
    text: str
    created_at: str
    attachments: tuple[Attachment, ...] = ()
    sender_master_id: str | None = None
    delivery: str = DELIVERY_QUEUED
    read_at: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> Message:
        where = "message"
        data = _obj(raw, where)
        attachments = tuple(
            Attachment.from_payload(item)
            for item in _items(data.get("attachments"), f"{where}.attachments")
        )
        return cls(
            id=_str(data, "id", where),
            seq=_int(data, "seq", where),
            ticket_id=_str(data, "ticket_id", where),
            sender=_str(data, "sender", where),
            sender_name=_str(data, "sender_name", where),
            text=_str(data, "text", where),
            created_at=_str(data, "created_at", where),
            attachments=attachments,
            sender_master_id=_opt_str(data, "sender_master_id"),
            delivery=_str(data, "delivery", where),
            read_at=_opt_str(data, "read_at"),
        )

    @property
    def is_from_client(self) -> bool:
        return self.sender == SENDER_CLIENT

    @property
    def delivery_label(self) -> str:
        return label(DELIVERY_LABELS, self.delivery)

    @property
    def has_attachments(self) -> bool:
        return bool(self.attachments)


@dataclass(frozen=True, slots=True)
class ClientBrief:
    """Клиент, оставивший заявку (Telegram или почта)."""

    id: str
    display_name: str
    phone: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> ClientBrief:
        where = "client"
        data = _obj(raw, where)
        return cls(
            id=_str(data, "id", where),
            display_name=_str(data, "display_name", where),
            phone=_opt_str(data, "phone"),
        )


@dataclass(frozen=True, slots=True)
class MasterBrief:
    """Мастер в карточке заявки: владелец или участник."""

    id: str
    full_name: str
    workshop_id: str | None = None
    workshop_name: str | None = None
    online: bool = False
    active_tickets: int = 0
    role: str = ""

    @classmethod
    def from_payload(cls, raw: Any) -> MasterBrief:
        where = "master"
        data = _obj(raw, where)
        return cls(
            id=_str(data, "id", where),
            full_name=_str(data, "full_name", where),
            workshop_id=_opt_str(data, "workshop_id"),
            workshop_name=_opt_str(data, "workshop_name"),
            online=bool(data.get("online", False)),
            active_tickets=_int(data, "active_tickets", where),
            role=_str(data, "role", where, ""),
        )


@dataclass(frozen=True, slots=True)
class Member:
    """Участник заявки, кроме владельца."""

    master_id: str
    full_name: str
    role: str
    joined_at: str

    @classmethod
    def from_payload(cls, raw: Any) -> Member:
        where = "member"
        data = _obj(raw, where)
        return cls(
            master_id=_str(data, "master_id", where),
            full_name=_str(data, "full_name", where),
            role=_str(data, "role", where),
            joined_at=_str(data, "joined_at", where),
        )


@dataclass(frozen=True, slots=True)
class LastMessage:
    """Превью последнего сообщения в списке заявок."""

    seq: int
    sender: str
    preview: str

    @classmethod
    def from_payload(cls, raw: Any) -> LastMessage:
        where = "last_message"
        data = _obj(raw, where)
        return cls(
            seq=_int(data, "seq", where),
            sender=_str(data, "sender", where),
            preview=_str(data, "preview", where),
        )


@dataclass(frozen=True, slots=True)
class TicketBrief:
    """Заявка в списке — без текста первого сообщения и служебных полей."""

    id: str
    status: str
    subject: str
    created_at: str
    updated_at: str
    text: str = ""
    client: ClientBrief | None = None
    workshop_id: str | None = None
    workshop_name: str | None = None
    owner: MasterBrief | None = None
    members: tuple[Member, ...] = ()
    last_message: LastMessage | None = None
    unread_count: int = 0

    @classmethod
    def from_payload(cls, raw: Any) -> TicketBrief:
        where = "ticket"
        data = _obj(raw, where)
        client_raw = data.get("client")
        owner_raw = data.get("owner")
        last_raw = data.get("last_message")
        return cls(
            id=_str(data, "id", where),
            status=_str(data, "status", where),
            subject=_str(data, "subject", where),
            created_at=_str(data, "created_at", where),
            updated_at=_str(data, "updated_at", where),
            text=_str(data, "text", where, ""),
            client=ClientBrief.from_payload(client_raw) if client_raw else None,
            workshop_id=_opt_str(data, "workshop_id"),
            workshop_name=_opt_str(data, "workshop_name"),
            owner=MasterBrief.from_payload(owner_raw) if owner_raw else None,
            members=tuple(
                Member.from_payload(item)
                for item in _items(data.get("members"), f"{where}.members")
            ),
            last_message=LastMessage.from_payload(last_raw) if last_raw else None,
            unread_count=_int(data, "unread_count", where),
        )

    @property
    def is_closed(self) -> bool:
        return self.status == STATUS_CLOSED

    @property
    def status_label(self) -> str:
        return label(STATUS_LABELS, self.status)

    def is_mine(self, master_id: str | None) -> bool:
        """Заявка закреплена за этим мастером."""
        if not master_id:
            return False
        if self.owner is not None and self.owner.id == master_id:
            return True
        return any(member.master_id == master_id for member in self.members)

    def members_all(self) -> tuple[MasterBrief | Member, ...]:
        """Владелец и участники одним списком — так их рисует UI."""
        people: list[MasterBrief | Member] = []
        if self.owner is not None:
            people.append(self.owner)
        people.extend(self.members)
        return tuple(people)


@dataclass(frozen=True, slots=True)
class Ticket(TicketBrief):
    """Полная карточка заявки: ответ POST /accept, GET /tickets/{id} и часть событий."""

    text: str = ""
    source: str = ""
    closed_at: str | None = None
    close_reason: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> Ticket:
        where = "ticket"
        data = _obj(raw, where)
        brief = TicketBrief.from_payload(data)
        return cls(
            id=brief.id,
            status=brief.status,
            subject=brief.subject,
            created_at=brief.created_at,
            updated_at=brief.updated_at,
            client=brief.client,
            workshop_id=brief.workshop_id,
            workshop_name=brief.workshop_name,
            owner=brief.owner,
            members=brief.members,
            last_message=brief.last_message,
            unread_count=brief.unread_count,
            text=_str(data, "text", where, ""),
            source=_str(data, "source", where, ""),
            closed_at=_opt_str(data, "closed_at"),
            close_reason=_opt_str(data, "close_reason"),
        )


@dataclass(frozen=True, slots=True)
class Profile:
    """Профиль мастера — ответ /session."""

    id: str
    full_name: str
    workshop_id: str | None = None
    workshop_name: str | None = None
    is_active: bool = True
    online: bool = True

    @classmethod
    def from_payload(cls, raw: Any) -> Profile:
        where = "profile"
        data = _obj(raw, where)
        return cls(
            id=_str(data, "id", where),
            full_name=_str(data, "full_name", where),
            workshop_id=_opt_str(data, "workshop_id"),
            workshop_name=_opt_str(data, "workshop_name"),
            is_active=bool(data.get("is_active", True)),
            online=bool(data.get("online", True)),
        )


@dataclass(frozen=True, slots=True)
class Session:
    """Ответ POST /session и GET /session.

    cursor — актуальный seq ленты этого мастера. Именно его клиент ставит
    себе в кэш после полного ресинка: события до него уже учтены в REST-данных.
    """

    master_id: str
    profile: Profile
    cursor: int
    server_time: str
    token: str | None = None
    telegram: Mapping[str, Any] | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> Session:
        where = "session"
        data = _obj(raw, where)
        telegram = data.get("telegram")
        return cls(
            master_id=_str(data, "master_id", where),
            profile=Profile.from_payload(data.get("profile")),
            cursor=_int(data, "cursor", where),
            server_time=_str(data, "server_time", where),
            token=_opt_str(data, "token"),
            telegram=_obj(telegram, f"{where}.telegram") if telegram else None,
        )


@dataclass(frozen=True, slots=True)
class Event:
    """Событие ленты.

    data разбирается не здесь: у каждого типа свой набор ключей, и разбор
    делает writer при применении. Здесь только конверт и «сырые» данные.
    """

    seq: int
    type: str
    ts: str
    data: Mapping[str, Any]
    ticket_id: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> Event:
        where = "event"
        data = _obj(raw, where)
        return cls(
            seq=_int(data, "seq", where),
            type=_str(data, "type", where),
            ts=_str(data, "ts", where),
            data=_obj(data.get("data"), f"{where}.data"),
            ticket_id=_opt_str(data, "ticket_id"),
        )


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Ответ GET /sync.

    Пустой результат приходит двумя способами: 204 (ждали и не дождались) и
    200 с пустым списком (wait=0). Для клиента это одно и то же — «нового
    ничего», поэтому оба случая сводятся к пустому events с тем же курсором.
    """

    events: tuple[Event, ...]
    cursor: int
    has_more: bool

    @property
    def is_empty(self) -> bool:
        return not self.events


@dataclass(frozen=True, slots=True)
class TicketsPage:
    tickets: tuple[TicketBrief, ...]
    next_before: str | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> TicketsPage:
        where = "tickets"
        data = _obj(raw, where)
        tickets = tuple(
            TicketBrief.from_payload(item)
            for item in _items(data.get("tickets"), f"{where}.tickets")
        )
        return cls(tickets=tickets, next_before=_opt_str(data, "next_before"))


@dataclass(frozen=True, slots=True)
class MessagesPage:
    """История сообщений по заявке, по возрастанию seq."""

    messages: tuple[Message, ...]
    has_more_before: bool = False
    has_more_after: bool = False

    @classmethod
    def from_payload(cls, raw: Any) -> MessagesPage:
        where = "messages"
        data = _obj(raw, where)
        messages = tuple(
            Message.from_payload(item) for item in _items(data.get("messages"), f"{where}.messages")
        )
        return cls(
            messages=messages,
            has_more_before=bool(data.get("has_more_before", False)),
            has_more_after=bool(data.get("has_more_after", False)),
        )


@dataclass(frozen=True, slots=True)
class Workshop:
    id: str
    name: str

    @classmethod
    def from_payload(cls, raw: Any) -> Workshop:
        where = "workshop"
        data = _obj(raw, where)
        return cls(
            id=_str(data, "id", where),
            name=_str(data, "name", where),
        )


@dataclass(frozen=True, slots=True)
class MastersPage:
    masters: tuple[MasterBrief, ...]

    @classmethod
    def from_payload(cls, raw: Any) -> MastersPage:
        where = "masters"
        data = _obj(raw, where)
        return cls(
            masters=tuple(
                MasterBrief.from_payload(item)
                for item in _items(data.get("masters"), f"{where}.masters")
            )
        )


@dataclass(frozen=True, slots=True)
class WorkshopsPage:
    workshops: tuple[Workshop, ...]

    @classmethod
    def from_payload(cls, raw: Any) -> WorkshopsPage:
        where = "workshops"
        data = _obj(raw, where)
        return cls(
            workshops=tuple(
                Workshop.from_payload(item)
                for item in _items(data.get("workshops"), f"{where}.workshops")
            )
        )


def sort_by_seq(messages: Sequence[Message]) -> list[Message]:
    """Сообщения по возрастанию seq — так их показывает диалог."""
    return sorted(messages, key=lambda message: message.seq)
