"""Проверки главного окна на живом AppContext с фейковым сервером.

Всё, что умеет окно, гоняем в offscreen: списки, навигация, действия (они идут
через TaskRunner(inline=True) — синхронно, чтобы тест видел результат сразу),
геометрия пузырей, реакции на обновление данных.
"""

from __future__ import annotations

import os
from functools import partial

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QFrame

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from chat_multi_cli.models import Message
from chat_multi_cli.services import AppContext
from chat_multi_cli.storage.writer import upsert_message
from chat_multi_cli.ui.chat_view import MessageBubble, SystemBubble
from chat_multi_cli.ui.main_window import MainWindow
from chat_multi_cli.ui.style import QSS
from chat_multi_cli.ui.tasks import TaskRunner
from chat_multi_cli.ui.ticket_list import INCOMING_ROLE, TICKET_ROLE
from tests.conftest import message_payload
from tests.fake_server import FakeServer


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    instance.setStyle("Fusion")
    instance.setStyleSheet(QSS)
    return instance


@pytest.fixture
def window(app: QApplication, app_ctx: AppContext) -> MainWindow:
    win = MainWindow(app_ctx, tasks=TaskRunner(inline=True))
    win.show()
    app.processEvents()
    yield win
    win.close()
    app.processEvents()


def _bubbles(window: MainWindow) -> list[tuple[int, int]]:
    """Возвращает (x, ширина) рамки каждого обычного пузыря."""
    result: list[tuple[int, int]] = []
    for bubble in window.chat.findChildren(MessageBubble):
        frames = [
            bubble.layout().itemAt(i).widget()
            for i in range(bubble.layout().count())
            if isinstance(bubble.layout().itemAt(i).widget(), QFrame)
        ]
        if frames:
            rect: QRect = frames[0].geometry()
            result.append((rect.x(), rect.width()))
    return result


def test_list_shows_incoming_first(window: MainWindow) -> None:
    assert window.panel.list.count() == 1 + len(window.tickets)
    assert window.panel.list.item(0).data(INCOMING_ROLE) == len(window.incoming)
    assert window.panel.list.item(0).data(TICKET_ROLE) is None
    assert window.panel.is_incoming_selected()


def test_closed_tickets_are_last(window: MainWindow) -> None:
    statuses = [
        window.panel.list.item(row).data(TICKET_ROLE).status
        for row in range(1, window.panel.list.count())
    ]
    first_closed = statuses.index("closed")
    assert "in_progress" not in statuses[first_closed:]


def test_opening_ticket_marks_read(window: MainWindow) -> None:
    assert window.ctx.repo.unread_count("t1") == 1
    window._open_ticket("t1")
    assert window.stack.currentWidget() is window.chat_page
    assert window.chat.ticket is not None
    assert window.chat.ticket.id == "t1"
    assert window.ctx.repo.unread_count("t1") == 0


def test_own_messages_right_client_messages_left(window: MainWindow) -> None:
    window._open_ticket("t1")
    QApplication.processEvents()
    host_width = window.chat.bubble_host.width()
    assert host_width > 300

    x_positions = [x for x, _ in _bubbles(window)]
    assert x_positions, "нет отрендеренных пузырей"
    assert min(x_positions) < host_width * 0.25, "сообщения клиента должны быть слева"
    assert max(x_positions) > host_width * 0.25, "свои сообщения должны быть справа"


def test_system_message_is_a_system_bubble(window: MainWindow) -> None:
    window._open_ticket("t1")
    QApplication.processEvents()
    assert len(window.chat.findChildren(SystemBubble)) == 1


def test_accept_moves_ticket_into_list(window: MainWindow) -> None:
    before_incoming = len(window.incoming)
    window._accept("t2")

    assert len(window.incoming) == before_incoming - 1
    assert window.stack.currentWidget() is window.chat_page
    assert window.chat.ticket is not None
    assert window.chat.ticket.status == "in_progress"
    assert window.chat.ticket.id == "t2"


def test_send_appends_own_message(window: MainWindow) -> None:
    window._open_ticket("t1")
    count = len(window.chat.messages)
    window._send("Проверка связи")

    assert len(window.chat.messages) == count + 1
    last = window.chat.messages[-1]
    assert last.sender == "master"
    assert last.text == "Проверка связи"


def test_close_ticket_locks_composer(window: MainWindow) -> None:
    window._open_ticket("t1")
    window._close_ticket()

    composer = window.chat.composer
    assert composer.input.isEnabled() is False
    assert composer.close_button.isVisible() is False
    assert composer.reopen_button.isVisible() is True


def test_reopen_ticket_unlocks_composer(window: MainWindow) -> None:
    window._open_ticket("t3")
    assert window.chat.composer.input.isEnabled() is False

    window._reopen_ticket()
    assert window.chat.ticket.status == "in_progress"
    assert window.chat.composer.input.isEnabled() is True


def test_add_and_remove_master(window: MainWindow) -> None:
    window._open_ticket("t1")
    before = len(window.masters)
    window._connect_master("m2")
    assert len(window.masters) == before + 1
    assert "Пётр Кузнецов" in {name for _, name in window.masters}

    window._remove_master("m2")
    assert "Пётр Кузнецов" not in {name for _, name in window.masters}


def test_reload_keeps_chat_open(window: MainWindow) -> None:
    window._open_ticket("t1")
    window._on_batch(None)
    QApplication.processEvents()
    assert window.stack.currentWidget() is window.chat_page
    assert window.panel.current_ticket_id() == "t1"
    assert window.chat.ticket.id == "t1"


def test_incoming_message_appears_after_reload(
    window: MainWindow, fake_server: FakeServer
) -> None:
    window._open_ticket("t1")
    assert len(window.chat.messages) == 3

    fake_server.add_message(
        "t1",
        message_payload("m-new", seq=4, ticket_id="t1", sender="client", text="Вечером ждём"),
    )
    # Воркер применил бы событие message.created так же: запись в кэш + сигнал.
    payload = fake_server.messages["t1"][-1]
    window.ctx.writer.run_sync(partial(upsert_message, message=Message.from_payload(payload)))
    window._on_batch(None)
    QApplication.processEvents()

    assert len(window.chat.messages) == 4
    assert window.chat.messages[-1].text == "Вечером ждём"
    assert window.ctx.repo.unread_count("t1") == 1
    assert window.tickets[0].unread_count == 1


def test_search_filters_rows(window: MainWindow) -> None:
    window.panel.search.setText("стук")
    QApplication.processEvents()
    subjects = [
        window.panel.list.item(row).data(TICKET_ROLE).subject
        for row in range(1, window.panel.list.count())
    ]
    assert subjects == ["Стук при повороте"]

    window.panel.search.setText("")
    QApplication.processEvents()
    assert window.panel.list.count() == 1 + len(window.tickets)
