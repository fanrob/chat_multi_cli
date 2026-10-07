"""HTTP-клиент мессенджера: все эндпоинты README и ретраи с backoff.

Два решения, которые стоит понимать до правок:

1. keep-alive обязателен. Long-poll держит соединение до 25 секунд, и клиент
   переоткрывает его на каждом запросе — то есть 20 новых TCP-соединений в
   минуту на мастера. С keep-alive этого не происходит, поэтому клиент один
   на всё приложение и переиспользует соединение.

2. Таймаут чтения длиннее, чем wait. Если клиент попросил ждать 25 секунд,
   а read timeout тоже 25, то на ровно границе httpx рвёт соединение и
   long-poll превращается в набор бесполезных реконнектов. Поэтому read
   timeout = wait + запас.

Ретраи: сетевые обрывы и 5xx/429 повторяем с backoff, но не ретраим accept —
он не идемпотентен, и повтор после состоявшегося коммита прилетел бы
409 already_accepted вместо успеха.
"""

from __future__ import annotations

import logging
import mimetypes
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, Final

import httpx

from chat_multi_cli import __version__
from chat_multi_cli.api.errors import ApiError, NetworkError
from chat_multi_cli.models import (
    Attachment,
    Event,
    MastersPage,
    Member,
    Message,
    MessagesPage,
    PayloadError,
    Session,
    SyncResult,
    Ticket,
    TicketsPage,
    WorkshopsPage,
)

logger = logging.getLogger(__name__)

#: Сколько событий просим за раз. Больше 500 сервер не отдаёт.
DEFAULT_SYNC_LIMIT: Final = 200

#: Запас к wait сверх таймаута чтения, чтобы httpx не оборвал ровно на границе.
LONGPOLL_TIMEOUT_MARGIN: Final = 10.0

#: Обычный таймаут чтения для REST-запросов.
DEFAULT_READ_TIMEOUT: Final = 10.0

#: Таймаут установки соединения.
DEFAULT_CONNECT_TIMEOUT: Final = 5.0

#: Сколько сообщений догружать при полной перезагрузке диалога.
DEFAULT_MESSAGES_LIMIT: Final = 100


def _reason(reason: str | None) -> dict[str, Any]:
    return {} if reason is None else {"reason": reason}


class ApiClient:
    """Тонкая обёртка над httpx с доменными методами.

    Не хранит состояние: курсор и данные живут в SQLite (storage), поэтому
    клиент можно пересоздать в любой момент — например, при смене адреса
    сервера из настроек.
    """

    def __init__(
        self,
        base_url: str,
        instance_id: str,
        *,
        client_version: str = __version__,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        long_poll_margin: float = LONGPOLL_TIMEOUT_MARGIN,
        max_retries: int = 3,
        backoff_initial: float = 0.5,
        backoff_factor: float = 2.0,
        backoff_max: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.instance_id = instance_id
        self.client_version = client_version
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.long_poll_margin = long_poll_margin
        self.max_retries = max_retries
        self.backoff_initial = backoff_initial
        self.backoff_factor = backoff_factor
        self.backoff_max = backoff_max
        self._sleep = sleep

        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
            headers={
                "X-Instance-Id": instance_id,
                "X-Client-Version": client_version,
                "Accept": "application/json",
            },
            transport=transport,
        )

    # ---------- Жизненный цикл ----------

    def shutdown(self) -> None:
        """Закрыть keep-alive соединения.

        Имя не close(), потому что close() здесь — это «закрыть заявку»,
        и путаница между ними стоила бы одного неверного вызова.
        """
        self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()

    # ---------- Сессия ----------

    def create_session(
        self,
        full_name: str,
        workshop_id: str | None = None,
        *,
        platform: str | None = None,
    ) -> Session:
        """Регистрация на сервере (или повторный вход тем же instance_id).

        Без заголовка X-Instance-Id: сервер сам себя узнаёт по теле метода.
        """
        body: dict[str, Any] = {
            "instance_id": self.instance_id,
            "full_name": full_name,
            "client_version": self.client_version,
        }
        if workshop_id:
            body["workshop_id"] = workshop_id
        if platform:
            body["platform"] = platform
        payload = self._request("POST", "/session", json=body)
        return Session.from_payload(payload)

    def read_session(self) -> Session:
        """Текущий профиль и актуальный курсор ленты.

        cursor из ответа — единственный честный способ завершить полную
        перезагрузку: события до него уже учтены в данных, полученных по REST.
        """
        payload = self._request("GET", "/session")
        return Session.from_payload(payload)

    def update_profile(
        self,
        full_name: str | None = None,
        workshop_id: str | None = None,
    ) -> Session:
        body: dict[str, Any] = {}
        if full_name is not None:
            body["full_name"] = full_name
        if workshop_id is not None:
            body["workshop_id"] = workshop_id
        payload = self._request("PATCH", "/session/profile", json=body)
        return Session.from_payload(payload)

    # ---------- Лента событий ----------

    def sync(
        self,
        cursor: int,
        wait: int = 25,
        *,
        limit: int = DEFAULT_SYNC_LIMIT,
        ticket_id: str | None = None,
        retries: int = 0,
    ) -> SyncResult:
        """Долгий опрос ленты: события после cursor, ожидание до wait секунд.

        Пустой ответ приходит двумя способами — 204 без тела (ждали, не дождались)
        и 200 с пустым списком (wait=0). Оба сводятся к пустому результату с
        тем же курсором, чтобы вызывающий код не различал их.

        Ретраи по умолчанию выключены: повторять долгий опрос должен внешний
        цикл с backoff (sync.worker), иначе сетевой обрыв превратится в
        лишний запрос внутри уже идущего ожидания.
        """
        read_timeout = wait + self.long_poll_margin if wait > 0 else self.read_timeout
        params: dict[str, Any] = {"cursor": cursor, "wait": wait, "limit": limit}
        if ticket_id:
            params["ticket_id"] = ticket_id

        payload = self._request(
            "GET",
            "/sync",
            params=params,
            read_timeout=read_timeout,
            retries=retries,
        )
        if payload is None:
            return SyncResult(events=(), cursor=cursor, has_more=False)

        events = tuple(Event.from_payload(item) for item in _items(payload.get("events")))
        return SyncResult(
            events=events,
            cursor=int(payload.get("cursor") or cursor),
            has_more=bool(payload.get("has_more")),
        )

    # ---------- Заявки ----------

    def tickets(
        self,
        *,
        scope: str = "feed",
        status: str | None = None,
        workshop_id: str | None = None,
        query: str | None = None,
        limit: int = 50,
        before: str | None = None,
    ) -> TicketsPage:
        """Список заявок: scope = feed (лента) | mine (мои) | all."""
        params: dict[str, Any] = {"scope": scope, "limit": limit}
        if status:
            params["status"] = status
        if workshop_id:
            params["workshop_id"] = workshop_id
        if query:
            params["query"] = query
        if before:
            params["before"] = before
        payload = self._request("GET", "/tickets", params=params)
        return TicketsPage.from_payload(payload)

    def ticket(self, ticket_id: str) -> Ticket:
        """Карточка заявки с участниками.

        Используется после ticket.created и ticket.closed от бота: эти события
        приходят только с идентификатором, тело надо досмотреть отдельно.
        """
        payload = self._request("GET", f"/tickets/{ticket_id}")
        return Ticket.from_payload(payload.get("ticket"))

    def accept(self, ticket_id: str) -> Ticket:
        """Взять заявку в работу.

        Без ретраев: если сеть отвалилась на неизвестном шаге, состояние
        заявки неизвестно, и повтор даст 409 already_accepted. Лучше честно
        сказать «не получилось» и дождаться события ленты.
        """
        payload = self._request("POST", f"/tickets/{ticket_id}/accept", retries=0)
        return Ticket.from_payload(payload.get("ticket"))

    def release(self, ticket_id: str, reason: str | None = None) -> Ticket:
        """Вернуть заявку в общую ленту."""
        payload = self._request("POST", f"/tickets/{ticket_id}/release", json=_reason(reason))
        return Ticket.from_payload(payload.get("ticket"))

    def close(self, ticket_id: str, reason: str | None = None) -> Ticket:
        """Закрыть заявку."""
        payload = self._request("POST", f"/tickets/{ticket_id}/close", json=_reason(reason))
        return Ticket.from_payload(payload.get("ticket"))

    def reopen(self, ticket_id: str) -> Ticket:
        """Переоткрыть закрытую заявку."""
        payload = self._request("POST", f"/tickets/{ticket_id}/reopen", json={})
        return Ticket.from_payload(payload.get("ticket"))

    def decline(self, ticket_id: str, reason: str | None = None) -> None:
        """Скрыть заявку из своей ленты."""
        self._request("POST", f"/tickets/{ticket_id}/decline", json=_reason(reason))

    def add_member(self, ticket_id: str, master_id: str) -> Member:
        """Подключить второго мастера к заявке."""
        payload = self._request(
            "POST", f"/tickets/{ticket_id}/members", json={"master_id": master_id}
        )
        return Member.from_payload(payload.get("member"))

    def remove_member(self, ticket_id: str, master_id: str) -> None:
        """Отключить мастера от заявки."""
        self._request("DELETE", f"/tickets/{ticket_id}/members/{master_id}")

    # ---------- Сообщения ----------

    def messages(
        self,
        ticket_id: str,
        *,
        after_seq: int | None = None,
        before_seq: int | None = None,
        limit: int = DEFAULT_MESSAGES_LIMIT,
    ) -> MessagesPage:
        """История сообщений по заявке, по возрастанию seq."""
        params: dict[str, Any] = {"limit": limit}
        if after_seq is not None:
            params["after_seq"] = after_seq
        if before_seq is not None:
            params["before_seq"] = before_seq
        payload = self._request("GET", f"/tickets/{ticket_id}/messages", params=params)
        return MessagesPage.from_payload(payload)

    def send_message(
        self,
        ticket_id: str,
        text: str,
        *,
        client_msg_id: str | None = None,
        attachments: Iterable[str] = (),
        retries: int | None = None,
    ) -> Message:
        """Отправить сообщение клиенту.

        client_msg_id — ключ идемпотентности: при ретрае после обрыва (когда
        неизвестно, дошло сообщение или нет) сервер вернёт то же самое
        сообщение, а не создаст дубль в Telegram. Поэтому отправку ретраим.

        Возвращает сообщение в состоянии queued; дальше придут события
        message.updated со сменой delivery.
        """
        body: dict[str, Any] = {
            "ticket_id": ticket_id,
            "client_msg_id": client_msg_id or new_client_msg_id(),
            "text": text,
        }
        attachment_ids = list(attachments)
        if attachment_ids:
            body["attachments"] = [{"attachment_id": item} for item in attachment_ids]
        payload = self._request(
            "POST",
            "/messages",
            json=body,
            retries=self.max_retries if retries is None else retries,
        )
        return Message.from_payload(payload.get("message"))

    def mark_read(self, ticket_id: str, seq: int | None = None) -> None:
        """Отметить прочитанным до seq (или до последнего, если seq не указан)."""
        self._request(
            "POST",
            f"/tickets/{ticket_id}/read",
            json={} if seq is None else {"seq": seq},
            retries=0,
        )

    # ---------- Вложения ----------

    def upload_attachment(
        self,
        file_path: str | Path,
        *,
        ticket_id: str | None = None,
    ) -> Attachment:
        """Загрузить файл от мастера (multipart POST /attachments).

        Отдельный метод, а не _request: тело — multipart, а не JSON. Ретраев
        нет, как у accept: повтор после обрыва создал бы вторую копию файла,
        а состояние неопределено — лучше честная ошибка.
        """
        path = Path(file_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = {"ticket_id": ticket_id} if ticket_id else None
        try:
            with path.open("rb") as handle:
                response = self._client.post(
                    "/attachments",
                    files={"file": (path.name, handle, mime_type)},
                    data=data,
                )
        except httpx.HTTPError as exc:
            raise NetworkError(f"POST /attachments: {exc}") from exc

        payload = _json_body(response)
        if response.is_success:
            if payload is None:
                raise ApiError(
                    response.status_code,
                    "invalid_response",
                    "Сервер вернул ответ без разбираемого JSON",
                )
            return Attachment.from_payload(payload.get("attachment"))
        raise ApiError.from_payload(response.status_code, payload)

    # ---------- Справочники ----------

    def masters(self) -> MastersPage:
        """Справочник мастеров — для подключения второго к заявке."""
        payload = self._request("GET", "/masters")
        return MastersPage.from_payload(payload)

    def workshops(self) -> WorkshopsPage:
        """Список цехов — для выбора при первом запуске."""
        payload = self._request("GET", "/workshops")
        return WorkshopsPage.from_payload(payload)

    # ---------- Служебное ----------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        read_timeout: float | None = None,
        retries: int | None = None,
    ) -> Any:
        """Один запрос с ретраями. Возвращает разобранный JSON или None для 204."""
        attempts = 1 + (self.max_retries if retries is None else max(0, retries))
        timeout = (
            httpx.Timeout(read_timeout, connect=self.connect_timeout)
            if read_timeout is not None
            else None
        )

        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                if attempt >= attempts:
                    raise NetworkError(f"{method} {path}: {exc}") from exc
                logger.warning("%s %s: обрыв (%s), повтор %d", method, path, exc, attempt)
                self._sleep_backoff(attempt)
                continue

            payload = _json_body(response)

            if response.status_code == 204 or not response.content:
                return None
            if response.is_success:
                if payload is None:
                    raise ApiError(
                        response.status_code,
                        "invalid_response",
                        "Сервер вернул ответ без разбираемого JSON",
                    )
                return payload

            error = ApiError.from_payload(response.status_code, payload)
            if error.is_transient and attempt < attempts:
                delay = _retry_after(response) or self._backoff_delay(attempt)
                logger.warning(
                    "%s %s: %s (%d), повтор через %.1fс",
                    method,
                    path,
                    error.code,
                    response.status_code,
                    delay,
                )
                self._sleep(delay)
                continue
            raise error

        raise NetworkError(f"{method} {path}: попытки исчерпаны")

    def _sleep_backoff(self, attempt: int) -> None:
        self._sleep(self._backoff_delay(attempt))

    def _backoff_delay(self, attempt: int) -> float:
        """Экспоненциальный рост с потолком: 0.5с, 1с, 2с, ... но не больше 15с."""
        delay = self.backoff_initial * (self.backoff_factor ** (attempt - 1))
        return min(delay, self.backoff_max)


def new_client_msg_id() -> str:
    """Ключ идемпотентности для отправки: «c» + uuid4 без дефисов."""
    return f"c{uuid.uuid4().hex}"


def _json_body(response: httpx.Response) -> Any:
    """Тело ответа как JSON; при не-JSON отдаём None и логируем."""
    try:
        return response.json()
    except ValueError:
        logger.warning(
            "%s %s: ответ не JSON (%s)",
            response.request.method,
            response.request.url.path,
            response.status_code,
        )
        return None


def _items(value: Any) -> Sequence[Any]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PayloadError("sync.events: ожидался список событий")
    return value


def _retry_after(response: httpx.Response) -> float | None:
    """Retry-After в секундах, если сервер его послал."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
