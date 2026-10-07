"""Ошибки сервера: конверт `{"error": {...}}` в исключение.

Сервер отвечает единым конвертом, поэтому клиенту не нужно знать про HTTP-коды
для конкретных ситуаций: достаточно разобрать `code` и получить понятный текст.
Все коды из README, раздел 7, перечислены тут же — если сервер пришлёт новый,
подставится текст по умолчанию, а не случится KeyError.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

logger = logging.getLogger(__name__)

#: Коды, при которых повторять тот же запрос имеет смысл.
TRANSIENT_CODES: Final[frozenset[str]] = frozenset(
    {"rate_limited", "server_error", "cursor_expired"}
)

#: Коды, при которых клиент должен заново получить сессию или данные.
RESYNC_CODES: Final[frozenset[str]] = frozenset({"cursor_expired"})

#: Человеческие тексты по кодам ошибок. details подставляются в текст вызывающим кодом.
ERROR_TEXT: Final[Mapping[str, str]] = {
    "validation_error": "Проверьте введённые данные",
    "unauthorized": "Нет связи с сервером: клиент не зарегистрирован",
    "instance_not_whitelisted": "Этот компьютер не добавлен в белый список",
    "forbidden": "Действие запрещено",
    "ticket_not_found": "Заявка не найдена или стала недоступна",
    "master_not_found": "Мастер не найден",
    "attachment_not_found": "Вложение не найдено",
    "not_ticket_member": "Вы не участник этой заявки",
    "ticket_closed": "Заявка уже закрыта",
    "already_accepted": "Заявку уже взяли",
    "already_member": "Мастер уже подключён",
    "ticket_not_in_feed": "Заявки больше нет в ленте",
    "cursor_expired": "Лента обнулилась, нужна полная перезагрузка",
    "rate_limited": "Слишком много запросов, подождите",
    "server_error": "Ошибка сервера, повторим попытку",
}


class ApiError(Exception):
    """Ошибка, которую вернул сервер в конверте README, раздел 7.

    Хранит и код, и HTTP-статус, и details: по ним UI решает, что показать
    («Заявку уже взял Пётр Кузнецов») и что делать дальше — повторить, переподключиться
    или перезагрузить ленту.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details: Mapping[str, Any] = dict(details or {})
        super().__init__(self.text)

    @classmethod
    def from_payload(cls, status_code: int, payload: Any) -> ApiError:
        """Собирает ошибку из тела ответа.

        Тело может оказаться не-конвертом (например, HTML от прокси) — тогда
        берём текст ответа, но код оставляем осмысленный, чтобы retry-логика
        и UI не ломались.
        """
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or f"http_{status_code}")
                message = str(error.get("message") or ERROR_TEXT.get(code, "Ошибка сервера"))
                details = error.get("details")
                return cls(
                    status_code,
                    code,
                    message,
                    details if isinstance(details, dict) else None,
                )
        return cls(status_code, f"http_{status_code}", ERROR_TEXT.get("", "Ошибка сервера"))

    @property
    def text(self) -> str:
        """Текст для показа мастеру."""
        owner = self.taken_by_name
        if self.code == "already_accepted" and owner:
            return f"Заявку уже взял {owner}"
        return self.message or ERROR_TEXT.get(self.code, "Ошибка сервера")

    @property
    def taken_by_name(self) -> str:
        """Кто занял заявку — для сообщения вида «Заявку уже взял Фёдор»."""
        value = self.details.get("owner_name")
        return value if isinstance(value, str) else ""

    @property
    def min_available_cursor(self) -> int:
        """С какого курсора лента ещё цела (ответ 410 cursor_expired)."""
        value = self.details.get("min_available_cursor")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return 0

    @property
    def is_transient(self) -> bool:
        """Стоит ли повторить запрос как есть."""
        return self.code in TRANSIENT_CODES or self.status_code >= 500

    @property
    def needs_resync(self) -> bool:
        """Нужно ли перезагрузить ленту целиком."""
        return self.code in RESYNC_CODES

    def __repr__(self) -> str:
        return f"ApiError({self.status_code}, {self.code!r}, {self.message!r}, {self.details!r})"


class NetworkError(Exception):
    """Сервер не ответил: таймаут long-poll, обрыв, DNS, рестарт сервиса.

    Отличается от ApiError тем, что коды сервера тут нет: клиент знает только,
    что связи нет, и должен повторять с backoff.
    """
