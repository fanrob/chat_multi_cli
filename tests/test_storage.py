"""Хранилище: идемпотентность событий, курсор, чтение ленты и истории."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from chat_multi_cli.models import STATUS_IN_PROGRESS, STATUS_NEW, Event, Message, Ticket
from chat_multi_cli.storage.db import Database
from chat_multi_cli.storage.repo import Repo
from chat_multi_cli.storage.writer import (
    WriterThread,
    apply_events,
    hide_ticket,
    mark_read,
    read_cursor,
    save_profile,
    set_cursor,
    show_ticket,
    upsert_messages,
    upsert_ticket,
)
from tests.conftest import event_payload, master_payload, message_payload, ticket_payload


def _event(seq: int, event_type: str, **kwargs: Any) -> Event:
    return Event.from_payload(event_payload(seq, event_type, **kwargs))


def _write(writer: WriterThread, fn: Any) -> Any:
    return writer.run_sync(fn)


# ---------- Миграции ----------


def test_migrate_creates_tables_and_version(db: Database) -> None:
    version = db.migrate()
    rows = db.connect().execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {str(row["name"]) for row in rows}
    assert version >= 1
    assert {"tickets", "messages", "meta", "applied_events"} <= tables


def test_migrate_is_idempotent(db: Database) -> None:
    first = db.migrate()
    second = db.migrate()
    assert first == second


# ---------- Курсор ----------


def test_cursor_starts_at_zero(db: Database) -> None:
    assert _write(WriterThread(db), read_cursor) == 0


def test_set_cursor_persists(writer: WriterThread) -> None:
    _write(writer, lambda conn: set_cursor(conn, 42))
    assert _write(writer, read_cursor) == 42


def test_profile_round_trip(writer: WriterThread) -> None:
    payload = {"master_id": "m1", "full_name": "Иванов Иван", "cursor": 7}
    _write(writer, lambda conn: save_profile(conn, payload))
    from chat_multi_cli.storage.writer import read_profile

    stored = _write(writer, read_profile)
    assert stored["full_name"] == "Иванов Иван"
    assert stored["cursor"] == 7


# ---------- События ----------


def test_ticket_created_with_full_payload_is_applied(writer: WriterThread, repo: Repo) -> None:
    batch = _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(
                    1,
                    "ticket.created",
                    ticket_id="t1",
                    data={"ticket": ticket_payload()},
                )
            ],
        ),
    )
    assert batch.applied == 1
    assert batch.cursor == 1
    assert batch.refetch_tickets == ()
    ticket = repo.ticket("t1")
    assert ticket is not None
    assert ticket.subject == "Не открывается дверь"
    assert ticket.owner is not None
    assert ticket.owner.full_name == "Иванов Иван"


def test_ticket_created_id_only_requires_refetch(writer: WriterThread, repo: Repo) -> None:
    """Так сервер и шлёт: только id, тело забираем отдельным запросом."""
    batch = _write(
        writer,
        lambda conn: apply_events(
            conn,
            [_event(1, "ticket.created", ticket_id="t1", data={"ticket_id": "t1"})],
        ),
    )
    assert batch.refetch_tickets == ("t1",)
    assert repo.ticket("t1") is None


def test_ticket_closed_id_only_requests_ticket(writer: WriterThread, repo: Repo) -> None:
    batch = _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(2, "ticket.closed", ticket_id="t1", data={"ticket_id": "t1"}),
            ],
        ),
    )
    assert batch.refetch_tickets == ("t1",)
    assert repo.status("t1") == "closed"


def test_message_created_is_stored(writer: WriterThread, repo: Repo) -> None:
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(
                    2,
                    "message.created",
                    ticket_id="t1",
                    data={"message": message_payload()},
                ),
            ],
        ),
    )
    messages = repo.messages("t1")
    assert len(messages) == 1
    assert messages[0].text == "Здравствуйте"
    assert messages[0].is_from_client


def test_duplicate_events_are_ignored(writer: WriterThread, repo: Repo) -> None:
    """At-least-once: повтор той же пачки не должен ни задублировать, ни сдвинуть курсор."""
    events = [
        _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
        _event(2, "message.created", ticket_id="t1", data={"message": message_payload()}),
    ]
    _write(writer, lambda conn: apply_events(conn, events))
    before = repo.unread_total()

    batch = _write(writer, lambda conn: apply_events(conn, events))
    assert batch.applied == 0
    assert batch.cursor == 2
    assert len(repo.messages("t1")) == 1
    assert repo.unread_total() == before


def test_partially_seen_batch_only_applies_new(writer: WriterThread, repo: Repo) -> None:
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [_event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()})],
        ),
    )
    batch = _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(2, "ticket.created", ticket_id="t2", data={"ticket": ticket_payload("t2")}),
            ],
        ),
    )
    assert batch.applied == 1
    assert batch.cursor == 2
    assert repo.exists("t2")


def test_cursor_never_goes_backwards(writer: WriterThread) -> None:
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [_event(5, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()})],
        ),
    )
    assert _write(writer, read_cursor) == 5
    batch = _write(
        writer,
        lambda conn: apply_events(
            conn,
            [_event(3, "ticket.created", ticket_id="t2", data={"ticket": ticket_payload("t2")})],
        ),
    )
    assert batch.cursor == 5


def test_ticket_accepted_updates_owner(writer: WriterThread, repo: Repo) -> None:
    other = master_payload("m2", "Петров Пётр")
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(
                    2,
                    "ticket.accepted",
                    ticket_id="t1",
                    data={"ticket": ticket_payload(status=STATUS_IN_PROGRESS, owner=other)},
                ),
            ],
        ),
    )
    ticket = repo.ticket("t1")
    assert ticket is not None
    assert ticket.status == STATUS_IN_PROGRESS
    assert ticket.owner is not None
    assert ticket.owner.id == "m2"


def test_ticket_closed_by_bot_sets_status_and_refetches(writer: WriterThread, repo: Repo) -> None:
    """Закрытие из Telegram приходит вообще без тела: статус ставим сразу,
    остальные поля забираем по GET /tickets/{id}."""
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(
                    2,
                    "ticket.closed",
                    ticket_id="t1",
                    data={"by_master_id": None, "by_client": True, "ticket_id": "t1"},
                ),
            ],
        ),
    )
    ticket = repo.ticket("t1")
    assert ticket is not None
    assert ticket.is_closed


def test_ticket_closed_without_reason_keeps_data(writer: WriterThread, repo: Repo) -> None:
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(2, "ticket.closed", ticket_id="t1", data={}),
            ],
        ),
    )
    ticket = repo.ticket("t1")
    assert ticket is not None
    assert ticket.is_closed
    assert ticket.subject == "Не открывается дверь"


def test_message_updated_changes_delivery(writer: WriterThread, repo: Repo) -> None:
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(2, "message.created", ticket_id="t1", data={"message": message_payload()}),
                _event(
                    3,
                    "message.updated",
                    ticket_id="t1",
                    data={"message_id": "m-1", "delivery": "delivered", "read_at": None},
                ),
            ],
        ),
    )
    messages = repo.messages("t1")
    assert messages[0].delivery == "delivered"
    # Базовое событие не двигает курсор назад и не плодит дубликаты.
    assert len(messages) == 1


def test_message_updated_unknown_message_is_ignored(writer: WriterThread, repo: Repo) -> None:
    batch = _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(
                    1,
                    "message.updated",
                    ticket_id="t1",
                    data={"message_id": "nope", "delivery": "delivered"},
                )
            ],
        ),
    )
    assert batch.applied == 1
    assert repo.messages("t1") == []


def test_member_added_and_removed(writer: WriterThread, repo: Repo) -> None:
    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [
                _event(1, "ticket.created", ticket_id="t1", data={"ticket": ticket_payload()}),
                _event(
                    2,
                    "ticket.member_added",
                    ticket_id="t1",
                    data={
                        "member": {
                            "master_id": "m2",
                            "full_name": "Петров Пётр",
                            "role": "collaborator",
                            "joined_at": "2026-02-01T11:00:00+00:00",
                        }
                    },
                ),
            ],
        ),
    )
    ticket = repo.ticket("t1")
    assert ticket is not None
    assert [m.master_id for m in ticket.members] == ["m2"]

    _write(
        writer,
        lambda conn: apply_events(
            conn,
            [_event(3, "ticket.member_removed", ticket_id="t1", data={"master_id": "m2"})],
        ),
    )
    ticket = repo.ticket("t1")
    assert ticket is not None
    assert ticket.members == ()


def test_masters_directory_changed_sets_flag(writer: WriterThread, repo: Repo) -> None:
    batch = _write(
        writer,
        lambda conn: apply_events(conn, [_event(1, "masters.directory_changed", data={})]),
    )
    assert batch.masters_dirty
    assert repo.masters_dirty


def test_unknown_event_advances_cursor_only(writer: WriterThread, repo: Repo) -> None:
    """Неизвестный тип не должен ломать синхронизацию: курсор обязаны двигать."""
    batch = _write(
        writer,
        lambda conn: apply_events(
            conn,
            [_event(9, "master.heartbeat.v2", data={"whatever": 1})],
        ),
    )
    assert batch.applied == 1
    assert batch.cursor == 9
    assert batch.refetch_tickets == ()
    assert _write(writer, read_cursor) == 9


# ---------- Запись из REST ----------


def test_upsert_ticket_overwrites_fields(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload())))
    updated = ticket_payload(status=STATUS_IN_PROGRESS, subject="Уточнённая тема")
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(updated)))
    ticket = repo.ticket("t1")
    assert ticket is not None
    assert ticket.subject == "Уточнённая тема"
    assert ticket.status == STATUS_IN_PROGRESS


def test_upsert_messages_is_idempotent(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload())))
    message = Message.from_payload(message_payload())
    _write(writer, lambda conn: upsert_messages(conn, [message, message]))
    assert len(repo.messages("t1")) == 1


# ---------- Лента и счётчики ----------


def test_feed_shows_only_new_tickets(writer: WriterThread, repo: Repo) -> None:
    """По README лента — это status = new; принятые живут во вкладке «мои»."""
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload("t1"))))
    _write(
        writer,
        lambda conn: upsert_ticket(
            conn, Ticket.from_payload(ticket_payload("t2", status=STATUS_IN_PROGRESS))
        ),
    )
    _write(
        writer,
        lambda conn: upsert_ticket(
            conn, Ticket.from_payload(ticket_payload("t3", status="closed"))
        ),
    )
    assert [t.id for t in repo.feed()] == ["t1"]
    assert [t.id for t in repo.closed()] == ["t3"]
    assert repo.status("t1") == STATUS_NEW


def test_feed_sorted_by_updated_desc(writer: WriterThread, repo: Repo) -> None:
    older = ticket_payload("t1")
    older["updated_at"] = "2026-02-01T09:00:00+00:00"
    newer = ticket_payload("t2")
    newer["updated_at"] = "2026-02-01T12:00:00+00:00"
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(older)))
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(newer)))
    assert [t.id for t in repo.feed()] == ["t2", "t1"]


def test_mine_filters_by_owner(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload("t1"))))
    _write(
        writer,
        lambda conn: upsert_ticket(
            conn, Ticket.from_payload(ticket_payload("t2", owner=master_payload("m2")))
        ),
    )
    repo.my_id = "m2"
    assert [t.id for t in repo.mine()] == ["t2"]
    repo.my_id = "m1"
    assert [t.id for t in repo.mine()] == ["t1"]


def test_unread_count_and_total(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload())))
    _write(
        writer,
        lambda conn: upsert_messages(
            conn,
            [
                Message.from_payload(message_payload("m1", seq=1)),
                Message.from_payload(message_payload("m2", seq=2, text="И ещё")),
                Message.from_payload(message_payload("m3", seq=3, sender="master")),
            ],
        ),
    )
    assert repo.unread_count("t1") == 2
    assert repo.unread_total() == 2
    assert repo.feed()[0].unread_count == 2


def test_mark_read_moves_read_seq(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload())))
    _write(
        writer,
        lambda conn: upsert_messages(
            conn,
            [
                Message.from_payload(message_payload("m1", seq=1)),
                Message.from_payload(message_payload("m2", seq=2)),
            ],
        ),
    )
    _write(writer, lambda conn: mark_read(conn, "t1", 1))
    assert repo.unread_count("t1") == 1
    _write(writer, lambda conn: mark_read(conn, "t1", 2))
    assert repo.unread_count("t1") == 0


def test_hide_and_show_ticket(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload())))
    assert repo.feed()
    _write(writer, lambda conn: hide_ticket(conn, "t1"))
    assert repo.is_hidden("t1")
    assert repo.feed() == []
    _write(writer, lambda conn: show_ticket(conn, "t1"))
    assert repo.feed()


def test_search_matches_subject_and_client(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload("t1"))))
    _write(
        writer,
        lambda conn: upsert_ticket(
            conn,
            Ticket.from_payload(
                ticket_payload("t2", subject="Стекло треснуло", text="Выпали осколки")
            ),
        ),
    )
    assert [t.id for t in repo.search("дверь")] == ["t1"]
    assert [t.id for t in repo.search("стекло")] == ["t2"]
    assert [t.id for t in repo.search("СТЕКЛО")] == ["t2"]
    assert [t.id for t in repo.search("Пётр")] == ["t1", "t2"]
    assert repo.search("нет такого") == []
    assert repo.search("   ") == []


# ---------- История ----------


def test_messages_pagination_both_directions(writer: WriterThread, repo: Repo) -> None:
    _write(writer, lambda conn: upsert_ticket(conn, Ticket.from_payload(ticket_payload())))
    _write(
        writer,
        lambda conn: upsert_messages(
            conn,
            [Message.from_payload(message_payload(f"m{i}", seq=i)) for i in range(1, 6)],
        ),
    )
    first = repo.messages("t1", limit=2)
    assert [m.seq for m in first] == [1, 2]
    older = repo.messages("t1", limit=2, before_seq=3)
    assert [m.seq for m in older] == [1, 2]
    newer = repo.messages("t1", after_seq=3)
    assert [m.seq for m in newer] == [4, 5]
    assert repo.last_message_seq("t1") == 5


def test_messages_of_unknown_ticket_is_empty(repo: Repo) -> None:
    assert repo.messages("нет-такой") == []
    assert repo.unread_count("нет-такой") == 0


def test_writer_reports_task_errors(writer: WriterThread) -> None:
    def boom(conn: sqlite3.Connection) -> None:
        raise ValueError("сломалось")

    with pytest.raises(ValueError, match="сломалось"):
        writer.run_sync(boom)


def test_stopped_writer_refuses_tasks(db: Database) -> None:
    thread = WriterThread(db)
    thread.stop()
    with pytest.raises(RuntimeError):
        thread.run_sync(lambda conn: None)
