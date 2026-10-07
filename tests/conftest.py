"""Общие фикстуры: временная БД, writer и сборщики серверных payload'ов.

Payload'ы повторяют форму ответов сервера: тесты должны ломаться, если клиент
начнёт ждать поля, которого в реальном ответе нет.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chat_multi_cli.config import ConfigStore, Paths
from chat_multi_cli.services import AppContext
from chat_multi_cli.storage.db import Database
from chat_multi_cli.storage.repo import Repo
from chat_multi_cli.storage.writer import WriterThread

INSTANCE_ID = "11111111-1111-4111-8111-111111111111"
BASE_URL = "http://testserver/v1"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "chat.db")
    database.migrate()
    yield database
    database.close_all()


@pytest.fixture
def writer(db: Database) -> Iterator[WriterThread]:
    thread = WriterThread(db)
    yield thread
    thread.stop()


@pytest.fixture
def repo(db: Database) -> Repo:
    return Repo(db)


@pytest.fixture
def master_id() -> str:
    return "m1"


# ---------- Сервисный слой ----------
# fake_server импортируется здесь лениво: сам он импортирует нас за builders'ами.


@pytest.fixture
def fake_server() -> Any:
    from tests.fake_server import seeded_server

    return seeded_server()


@pytest.fixture
def ui_store(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(Paths.for_user(tmp_path))
    store.save(store.config.with_changes(full_name="Иванов Иван", base_url=BASE_URL))
    store.ensure_instance_id()
    return store


@pytest.fixture
def app_ctx(fake_server: Any, ui_store: ConfigStore) -> Iterator[AppContext]:
    context = AppContext(ui_store, transport=fake_server.transport())
    context.bootstrap()
    yield context
    context.stop()


# ---------- Сборщики payload'ов ----------


def master_payload(
    master_id: str = "m1",
    full_name: str = "Иванов Иван",
    workshop_id: str = "body",
) -> dict[str, Any]:
    return {
        "id": master_id,
        "full_name": full_name,
        "workshop_id": workshop_id,
        "workshop_name": "Кузовной",
        "online": True,
        "active_tickets": 2,
    }


def ticket_payload(
    ticket_id: str = "t1",
    *,
    status: str = "new",
    subject: str = "Не открывается дверь",
    owner: dict[str, Any] | None = None,
    last_message: dict[str, Any] | None = None,
    text: str = "Дверь не открывается",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": ticket_id,
        "status": status,
        "subject": subject,
        "text": text,
        "source": "telegram",
        "created_at": "2026-02-01T10:00:00+00:00",
        "updated_at": "2026-02-01T10:05:00+00:00",
        "closed_at": None,
        "close_reason": None,
        "client": {"id": "c1", "display_name": "Пётр", "phone": None},
        "workshop_id": "body",
        "workshop_name": "Кузовной",
        "owner": owner if owner is not None else master_payload(),
        "members": [],
        "last_message": last_message,
        "unread_count": 1,
    }
    return payload


def message_payload(
    message_id: str = "m-1",
    *,
    seq: int = 1,
    ticket_id: str = "t1",
    sender: str = "client",
    text: str = "Здравствуйте",
    delivery: str = "queued",
    read_at: str | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "seq": seq,
        "ticket_id": ticket_id,
        "sender": sender,
        "sender_name": "Пётр" if sender == "client" else "Иванов Иван",
        "text": text,
        "created_at": "2026-02-01T10:01:00+00:00",
        "attachments": [],
        "sender_master_id": None if sender == "client" else "m1",
        "delivery": delivery,
        "read_at": read_at,
    }


def event_payload(
    seq: int,
    event_type: str,
    *,
    ticket_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "type": event_type,
        "ts": "2026-02-01T10:10:00+00:00",
        "ticket_id": ticket_id,
        "data": data or {},
    }


def session_payload(
    *,
    master_id: str = "m1",
    cursor: int = 0,
    token: str = "secret",
) -> dict[str, Any]:
    return {
        "master_id": master_id,
        "profile": {
            "id": master_id,
            "full_name": "Иванов Иван",
            "workshop_id": "body",
            "workshop_name": "Кузовной",
            "is_active": True,
            "online": True,
        },
        "cursor": cursor,
        "server_time": "2026-02-01T10:10:00+00:00",
        "token": token,
    }


__all__ = [
    "BASE_URL",
    "INSTANCE_ID",
    "event_payload",
    "master_payload",
    "message_payload",
    "session_payload",
    "ticket_payload",
]
