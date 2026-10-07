"""Адаптеры между доменными моделями и виджетами.

Доменные модели (models) неизменяемы и не знают про русские подписи и форматы
времени. Вместо прослойки с дублирующими dataclass'ами здесь только функции,
которые виджеты зовут там, где раньше жили поля моков: client_name, time_label
и т.п. — чтобы замену моков на реальные модели не пришлось делать заново.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from chat_multi_cli.models import SENDER_CLIENT, TicketBrief

INCOMING_TITLE: Final = "Входящие заявки"

#: Фолбэк для клиента без имени (в редких payload'ах client отсутствует).
CLIENT_FALLBACK: Final = "Клиент"


def _parse(iso: str | None) -> datetime | None:
    """datetime из ISO-строки сервера; при любом сбое — None.

    datetime.fromisoformat в Python 3.11 понимает и 'Z', и '+00:00'; на случай
    нестандартной строки не падаем, а отдаём None — виджет покажет сырой текст.
    """
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def format_time(iso: str) -> str:
    """Короткая подпись времени для списка заявок.

    Сегодня — HH:MM, вчера — «вчера», раньше — DD.MM. Всё в локальном
    времени мастера, серверные метки всегда UTC.
    """
    moment = _parse(iso)
    if moment is None:
        return iso
    local = moment.astimezone()
    today = datetime.now().astimezone().date()
    day = local.date()
    if day == today:
        return local.strftime("%H:%M")
    if day == today - timedelta(days=1):
        return "вчера"
    return local.strftime("%d.%m")


def format_message_time(iso: str) -> str:
    """Время в пузыре сообщения: HH:MM, а для старых — ещё и дата."""
    moment = _parse(iso)
    if moment is None:
        return iso
    local = moment.astimezone()
    if local.date() == datetime.now().astimezone().date():
        return local.strftime("%H:%M")
    return local.strftime("%d.%m %H:%M")


def client_name(ticket: TicketBrief) -> str:
    """Имя клиента; пустые/отсутствующие уходят в безопасный фолбэк."""
    if ticket.client and ticket.client.display_name:
        return ticket.client.display_name
    return CLIENT_FALLBACK


def ticket_time(ticket: TicketBrief) -> str:
    return format_time(ticket.updated_at)


def is_client_message(sender: str) -> bool:
    return sender == SENDER_CLIENT


def member_pairs(ticket: TicketBrief) -> list[tuple[str, str]]:
    """Участники заявки парами (id, имя) — формат панели мастеров.

    Владелец и участники хранятся разными типами с разными полями id,
    поэтому собираем их в один список руками.
    """
    pairs: list[tuple[str, str]] = []
    if ticket.owner is not None:
        pairs.append((ticket.owner.id, ticket.owner.full_name))
    pairs.extend((member.master_id, member.full_name) for member in ticket.members)
    return pairs
