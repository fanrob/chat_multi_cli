"""Конфигурация, пути, логирование, идентификатор экземпляра.

Будет реализовано вместе с сетевым слоем; пока объявлен только каркас.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AppConfig:
    """Значения из ~/.chat_multi_cli/config.json."""

    instance_id: str = ""
    full_name: str = ""
    workshop_id: str = ""
    base_url: str = "https://localhost:8443/v1"
    token: str = ""
    sync_wait_seconds: int = 25
    refresh_seconds: int = 20
    extras: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Paths:
    """Каталоги приложения в профиле пользователя."""

    root: str
    config_file: str
    database_file: str
    logs_dir: str
