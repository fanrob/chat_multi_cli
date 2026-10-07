"""Сервисный слой поверх фейкового сервера: вход, кэш, действия."""

from __future__ import annotations

from pathlib import Path

import pytest

from chat_multi_cli.config import ConfigStore, Paths
from chat_multi_cli.services import AppContext, SetupRequired
from tests.conftest import BASE_URL
from tests.fake_server import FakeServer, seeded_server


def test_bootstrap_requires_full_name(tmp_path: Path) -> None:
    store = ConfigStore(Paths.for_user(tmp_path))
    store.ensure_instance_id()
    ctx = AppContext(store, transport=seeded_server().transport())
    try:
        with pytest.raises(SetupRequired):
            ctx.bootstrap()
    finally:
        ctx.stop()


def test_first_run_registers_new_instance(tmp_path: Path) -> None:
    server = seeded_server()
    store = ConfigStore(Paths.for_user(tmp_path))
    store.save(store.config.with_changes(full_name="Новый Мастер", base_url=BASE_URL))
    store.ensure_instance_id()
    ctx = AppContext(store, transport=server.transport())
    try:
        session = ctx.bootstrap()
    finally:
        ctx.stop()
    assert session.profile.full_name == "Новый Мастер"
    assert server.full_name == "Новый Мастер"
    assert bool(server.instances)


def test_bootstrap_fills_cache(app_ctx: AppContext) -> None:
    assert [ticket.id for ticket in app_ctx.feed()] == ["t2"]
    assert {ticket.id for ticket in app_ctx.panel_tickets()} == {"t1", "t3"}
    assert len(app_ctx.messages("t1")) == 3
    names = {name for _, name in app_ctx.master_pairs()}
    assert "Пётр Кузнецов" in names


def test_accept_persists(app_ctx: AppContext, fake_server: FakeServer) -> None:
    app_ctx.accept("t2")
    assert not app_ctx.feed()
    ticket = app_ctx.ticket("t2")
    assert ticket is not None
    assert ticket.status == "in_progress"
    assert ticket.owner is not None and ticket.owner.id == app_ctx.master_id
    assert fake_server.tickets["t2"]["status"] == "in_progress"


def test_release_returns_to_feed(app_ctx: AppContext) -> None:
    app_ctx.accept("t2")
    app_ctx.release("t2")
    assert [ticket.id for ticket in app_ctx.feed()] == ["t2"]


def test_decline_hides(app_ctx: AppContext) -> None:
    app_ctx.decline("t2")
    assert app_ctx.repo.is_hidden("t2")
    assert not app_ctx.feed()


def test_close_and_reopen(app_ctx: AppContext) -> None:
    app_ctx.accept("t2")
    app_ctx.close("t2")
    assert app_ctx.ticket("t2").is_closed
    app_ctx.reopen("t2")
    assert app_ctx.ticket("t2").status == "in_progress"


def test_send_message_updates_cache(app_ctx: AppContext, fake_server: FakeServer) -> None:
    app_ctx.send("t1", "Проверка связи")
    messages = app_ctx.messages("t1")
    assert messages[-1].text == "Проверка связи"
    assert messages[-1].sender == "master"
    assert len(fake_server.messages["t1"]) == 4


def test_upload_attachment_and_send(
    app_ctx: AppContext, tmp_path: Path
) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake")
    message = app_ctx.send("t1", "Смотрите фото", [str(path)])
    assert message.has_attachments
    assert message.attachments[0].filename == "photo.jpg"


def test_mark_read(app_ctx: AppContext) -> None:
    assert app_ctx.repo.unread_count("t1") == 1
    app_ctx.mark_read("t1")
    assert app_ctx.repo.unread_count("t1") == 0


def test_members(app_ctx: AppContext) -> None:
    ticket = app_ctx.add_member("t1", "m2")
    assert "Пётр Кузнецов" in {member.full_name for member in ticket.members}
    ticket = app_ctx.remove_member("t1", "m2")
    assert ticket.members == ()


def test_update_profile_saves_config(app_ctx: AppContext) -> None:
    session = app_ctx.update_profile(full_name="Пётр Новиков")
    assert session.profile.full_name == "Пётр Новиков"
    assert app_ctx.config.full_name == "Пётр Новиков"
    assert app_ctx.store.config.full_name == "Пётр Новиков"
