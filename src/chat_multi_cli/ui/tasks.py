"""Мост между рабочими потоками и UI: запуск действий и сигналы воркера.

Из фонового потока нельзя трогать виджеты, но сигналы Qt можно эмитить из
любого потока: соединение с объектом, живущим в UI-потоке, будет доставлено
очередью событий. На этом держатся оба класса:

* TaskRunner — действие (accept, send, ...) выполняется в отдельном потоке,
  результат или ошибка возвращаются в UI-поток сигналом. inline=True
  выполняет всё сразу в вызывающем потоке — так тесты висят на синхронности.
* SyncBridge — подписки на колбэки SyncWorker; вызываются из его потока и
  просто переносят данные в сигналы окна.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class TaskRunner(QObject):
    """Запускает действие в фоне и доставляет результат в UI-поток."""

    succeeded = Signal(object, object)
    failed = Signal(object, object)

    def __init__(
        self,
        *,
        inline: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._inline = inline

    @property
    def inline(self) -> bool:
        return self._inline

    def submit(
        self,
        fn: Callable[[], Any],
        *,
        on_done: Callable[[Any], Any] | None = None,
        on_error: Callable[[BaseException], Any] | None = None,
    ) -> None:
        """Выполняет fn в фоне; колбэки вызываются в потоке окна.

        fn никогда не трогает виджеты: она работает с AppContext (сеть и БД).
        Результат приходит в on_done, ошибка — в on_error.
        """
        if self._inline:
            self._run(fn, on_done, on_error)
            return
        thread = threading.Thread(
            target=self._emit_from_thread,
            args=(fn, on_done, on_error),
            name="ui-action",
            daemon=True,
        )
        thread.start()

    def _emit_from_thread(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], Any] | None,
        on_error: Callable[[BaseException], Any] | None,
    ) -> None:
        try:
            result = fn()
        except BaseException as exc:
            self.failed.emit(on_error, exc)
        else:
            self.succeeded.emit(on_done, result)

    def _run(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], Any] | None,
        on_error: Callable[[BaseException], Any] | None,
    ) -> None:
        try:
            result = fn()
        except BaseException as exc:
            logger.debug("Действие не удалось: %s", exc)
            if on_error is not None:
                on_error(exc)
        else:
            if on_done is not None:
                on_done(result)


class SyncBridge(QObject):
    """Переносит колбэки SyncWorker в сигналы окна.

    Колбэки регистрируются в worker.start() до запуска потока; сами колбэки
    вызываются из его потока, а сигналы Qt доставляются в UI-поток.
    """

    batch_applied = Signal(object)
    status_changed = Signal(object)

    def on_batch(self, batch: Any) -> None:
        self.batch_applied.emit(batch)

    def on_status(self, status: Any) -> None:
        self.status_changed.emit(status)
