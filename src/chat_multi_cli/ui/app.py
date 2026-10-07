"""Запуск GUI: QApplication, стили, первый запуск и главное окно.

Поток запуска: конфиг (instance_id всегда есть) → AppContext → bootstrap
(вход на сервер + первичная загрузка). Если ФИО/адрес не заполнены или сервер
не отвечает — диалог настроек с описанием проблемы; после успешного входа
предлагаем цех, если сервер их знает.
"""

from __future__ import annotations

import contextlib
import sys

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
)

from chat_multi_cli.api.errors import ApiError, NetworkError
from chat_multi_cli.config import ConfigStore, Paths, setup_logging
from chat_multi_cli.services import AppContext, SetupRequired
from chat_multi_cli.ui.main_window import MainWindow
from chat_multi_cli.ui.setup_dialog import SetupDialog
from chat_multi_cli.ui.style import QSS


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("chat_multi_cli")
    app.setOrganizationName("STO")
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)

    paths = Paths.for_user().ensure()
    setup_logging(paths)
    store = ConfigStore(paths)
    store.ensure_instance_id()

    ctx = _bootstrap_with_dialog(store)
    if ctx is None:
        return 1

    _offer_workshop(ctx)

    window = MainWindow(ctx)
    window.show()
    ctx.start_sync()
    code = app.exec()
    ctx.stop()
    return code


def _bootstrap_with_dialog(store: ConfigStore) -> AppContext | None:
    """Пробует войти; при любой неудаче — диалог настроек. None = выход."""
    while True:
        candidate = AppContext(store)
        try:
            candidate.bootstrap()
            return candidate
        except SetupRequired as exc:
            error = str(exc)
        except (ApiError, NetworkError) as exc:
            error = f"Не удалось подключиться: {exc}"
        except Exception:
            # Сюда попадать не должны, но поток писателя нельзя бросать.
            candidate.stop()
            raise

        candidate.stop()
        dialog = SetupDialog(store.config, error=error)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        store.save(
            store.config.with_changes(
                full_name=dialog.full_name,
                base_url=dialog.base_url,
            )
        )


def _offer_workshop(ctx: AppContext) -> None:
    """Цех спрашиваем после входа, только если сервер их отдаёт (dev — пусто)."""
    profile = ctx.profile
    if profile is None or profile.workshop_id:
        return
    try:
        workshops = ctx.workshops()
    except (ApiError, NetworkError):
        return
    if not workshops:
        return

    name, ok = QInputDialog.getItem(
        None,
        "Выбор цеха",
        "Укажите цех (потом можно сменить в настройках):",
        [item.name for item in workshops],
        0,
        False,
    )
    if not ok or not name:
        return
    workshop = next((item for item in workshops if item.name == name), None)
    if workshop is None:
        return
    with contextlib.suppress(ApiError, NetworkError):
        ctx.update_profile(workshop_id=workshop.id)
