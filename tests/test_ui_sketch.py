"""Проверки UI-эскиза: рисуем окно в offscreen и смотрим геометрию."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QFrame

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from chat_multi_cli.ui.chat_view import MessageBubble, SystemBubble
from chat_multi_cli.ui.main_window import MainWindow
from chat_multi_cli.ui.style import QSS
from chat_multi_cli.ui.ticket_list import INCOMING_ROLE, TICKET_ROLE


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance() or QApplication([])
    instance.setStyle("Fusion")
    instance.setStyleSheet(QSS)
    return instance


@pytest.fixture
def window(app: QApplication) -> MainWindow:
    win = MainWindow()
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


def test_opening_ticket_switches_page(window: MainWindow) -> None:
    window._open_ticket("t_9f2c")
    assert window.stack.currentWidget() is window.chat_page
    assert window.chat.ticket is not None
    assert window.chat.ticket.id == "t_9f2c"


def test_own_messages_right_client_messages_left(window: MainWindow) -> None:
    window._open_ticket("t_9f2c")
    QApplication.processEvents()
    host_width = window.chat.bubble_host.width()
    assert host_width > 300

    x_positions = [x for x, _ in _bubbles(window)]
    assert x_positions, "нет отрендеренных пузырей"
    assert min(x_positions) < host_width * 0.25, "сообщения клиента должны быть слева"
    assert max(x_positions) > host_width * 0.25, "свои сообщения должны быть справа"


def test_system_message_is_not_a_plain_bubble(window: MainWindow) -> None:
    window._open_ticket("t_9f2c")
    QApplication.processEvents()
    assert len(window.chat.findChildren(SystemBubble)) == 1


def test_accept_moves_ticket_into_list(window: MainWindow) -> None:
    before_incoming = len(window.incoming)
    window._accept("t_in_1")

    assert len(window.incoming) == before_incoming - 1
    assert window.stack.currentWidget() is window.chat_page
    assert window.chat.ticket is not None
    assert window.chat.ticket.status == "in_progress"
    assert window.chat.ticket.id == "t_in_1"


def test_send_appends_own_message(window: MainWindow) -> None:
    window._open_ticket("t_9f2c")
    count = len(window.chat.ticket.messages)
    window._send("Проверка связи")

    assert len(window.chat.ticket.messages) == count + 1
    last = window.chat.ticket.messages[-1]
    assert last.sender == "master"
    assert last.delivery == "sending"


def test_close_ticket_locks_composer(window: MainWindow) -> None:
    window._open_ticket("t_9f2c")
    window._close_ticket()

    composer = window.chat.composer
    assert composer.input.isEnabled() is False
    assert composer.close_button.isVisible() is False
    assert composer.reopen_button.isVisible() is True


def test_reopen_ticket_unlocks_composer(window: MainWindow) -> None:
    window._open_ticket("t_1c88")
    assert window.chat.composer.input.isEnabled() is False

    window._reopen_ticket()
    assert window.chat.ticket.status == "in_progress"
    assert window.chat.composer.input.isEnabled() is True


def test_add_and_remove_master(window: MainWindow) -> None:
    before = len(window.masters)
    window._pick_master_from(("m_08",))
    assert len(window.masters) == before + 1
    assert "Антон Волков" in {name for _, name in window.masters}

    window._remove_master("m_08")
    assert "Антон Волков" not in {name for _, name in window.masters}


def test_rebuild_keeps_chat_open(window: MainWindow) -> None:
    window._open_ticket("t_9f2c")
    window.panel.rebuild()
    QApplication.processEvents()
    assert window.stack.currentWidget() is window.chat_page
    assert window.panel.current_ticket_id() == "t_9f2c"


def test_timer_refresh_does_not_change_page(window: MainWindow) -> None:
    window._open_ticket("t_3a71")
    window._refresh()
    QApplication.processEvents()
    assert window.stack.currentWidget() is window.chat_page
    assert window.panel.current_ticket_id() == "t_3a71"


def test_search_filters_rows(window: MainWindow) -> None:
    window.panel.search.setText("баккер")
    QApplication.processEvents()
    subjects = [
        window.panel.list.item(row).data(TICKET_ROLE).subject
        for row in range(1, window.panel.list.count())
    ]
    assert subjects == ["Левый баккер, горит на асфальте"]

    window.panel.search.setText("")
    QApplication.processEvents()
    assert window.panel.list.count() == 1 + len(window.tickets)
