"""Конфигурация, пути, логирование, идентификатор экземпляра.

Всё живёт в профиле пользователя (`~/.chat_multi_cli`), потому что это рабочее
приложение, а не демо: при обновлении программы профиль и переписка не должны
теряться, а конфиг с токеном — тем более попадать в репозиторий.

Файлы:
  config.json       настройки, в том числе instance_id
  chat.db           локальный кэш (WAL)
  logs/app.log      ротируемый лог приложения
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final

#: Каталог профиля приложения в домашней директории.
PROFILE_DIRNAME: Final = ".chat_multi_cli"

#: Сколько мегабайт может занимать лог до ротации.
LOG_MAX_BYTES: Final = 5 * 1024 * 1024

#: Сколько старых логов храним: app.log, app.log.1, app.log.2.
LOG_BACKUP_COUNT: Final = 3

#: Куда пишутся сообщения об ошибках в формате файла.
LOG_FORMAT: Final = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


@dataclass(slots=True)
class AppConfig:
    """Значения из config.json."""

    instance_id: str = ""
    full_name: str = ""
    workshop_id: str = ""
    base_url: str = "http://127.0.0.1:21000/v1"
    token: str = ""
    sync_wait_seconds: int = 25
    refresh_seconds: int = 20
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def ready_for_session(self) -> bool:
        """Хватает ли данных для первого входа на сервер."""
        return bool(self.instance_id and self.full_name and self.base_url)

    @property
    def missing_fields(self) -> list[str]:
        """Что нужно спросить у мастера в окне настроек."""
        missing: list[str] = []
        if not self.full_name:
            missing.append("ФИО")
        if not self.workshop_id:
            missing.append("участок")
        if not self.base_url:
            missing.append("адрес сервера")
        return missing

    def to_payload(self) -> dict[str, Any]:
        """То, что уходит в JSON: известные поля плюс всё, чего не знает код."""
        payload: dict[str, Any] = dict(self.extras)
        payload.update(
            {
                "instance_id": self.instance_id,
                "full_name": self.full_name,
                "workshop_id": self.workshop_id,
                "base_url": self.base_url,
                "token": self.token,
                "sync_wait_seconds": self.sync_wait_seconds,
                "refresh_seconds": self.refresh_seconds,
            }
        )
        return payload

    def with_changes(self, **changes: Any) -> AppConfig:
        """Копия с изменёнными полями — панель настроек правит так конфиг."""
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class Paths:
    """Каталоги приложения в профиле пользователя."""

    root: Path
    config_file: Path
    database_file: Path
    logs_dir: Path

    @classmethod
    def for_user(cls, home: Path | None = None) -> Paths:
        """Стандартные пути: ~/.chat_multi_cli."""
        base = Path(home) if home is not None else Path.home()
        root = base / PROFILE_DIRNAME
        return cls(
            root=root,
            config_file=root / "config.json",
            database_file=root / "chat.db",
            logs_dir=root / "logs",
        )

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "app.log"

    def ensure(self) -> Paths:
        """Создаёт каталоги. Идемпотентно: можно звать при каждом старте."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        return self


class ConfigStore:
    """Чтение и запись config.json.

    Хранилище переживает плохой JSON: если файл не разобрался, мы не падаем, а
    отдаём пустой конфиг и оставляем испорченный файл на месте под бэкапом.
    Иначе опечатка в конфиге означала бы невозможность запустить программу.
    """

    def __init__(self, paths: Paths) -> None:
        self._paths = paths
        self._config: AppConfig | None = None

    @property
    def paths(self) -> Paths:
        return self._paths

    @property
    def config(self) -> AppConfig:
        """Текущий конфиг, при первом обращении читается с диска."""
        if self._config is None:
            self._config = self.load()
        return self._config

    def load(self) -> AppConfig:
        """Читает конфиг; отсутствующий или битый файл — пустой конфиг."""
        if not self._paths.config_file.exists():
            self._config = AppConfig()
            return self._config
        try:
            raw = self._paths.config_file.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logging.getLogger(__name__).error(
                "Конфиг %s не читается: %s", self._paths.config_file, exc
            )
            self._config = AppConfig()
            return self._config
        if not isinstance(payload, dict):
            logging.getLogger(__name__).error(
                "Конфиг %s: ожидался объект, получено %s",
                self._paths.config_file,
                type(payload).__name__,
            )
            self._config = AppConfig()
            return self._config
        self._config = self._from_payload(payload)
        return self._config

    def save(self, config: AppConfig | None = None) -> AppConfig:
        """Пишет конфиг атомарно: временный файл плюс замена."""
        value = config or self.config
        self._paths.root.mkdir(parents=True, exist_ok=True)
        target = self._paths.config_file
        temp = target.with_suffix(".json.tmp")
        payload = json.dumps(value.to_payload(), ensure_ascii=False, indent=2)
        temp.write_text(payload + "\n", encoding="utf-8")
        os.replace(temp, target)
        self._config = value
        return value

    def ensure_instance_id(self) -> AppConfig:
        """Генерирует instance_id при первом запуске и сразу сохраняет.

        Идентификатор должен пережить перезапуск: по нему сервер узнаёт, что
        это тот же мастер, и не заставит вводить данные заново.
        """
        config = self.config
        if config.instance_id:
            return config
        return self.save(config.with_changes(instance_id=str(uuid.uuid4())))

    @staticmethod
    def _from_payload(payload: dict[str, Any]) -> AppConfig:
        """Отделяет известные поля от лишних: их нельзя потерять при записи."""
        known = {
            "instance_id": str,
            "full_name": str,
            "workshop_id": str,
            "base_url": str,
            "token": str,
            "sync_wait_seconds": int,
            "refresh_seconds": int,
        }
        values: dict[str, Any] = {}
        extras: dict[str, str] = {}
        defaults = AppConfig()
        for key, raw in payload.items():
            caster = known.get(key)
            if caster is None:
                extras[key] = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                continue
            try:
                values[key] = caster(raw)
            except (TypeError, ValueError):
                # Мусор в одном поле не должен обнулять остальные.
                values[key] = getattr(defaults, key)
        return AppConfig(extras=extras, **values)


def setup_logging(paths: Paths, *, debug: bool = False) -> logging.Logger:
    """Настраивает логирование в файл и консоль.

    В консоль пишем INFO (или DEBUG с --debug), в файл — всегда DEBUG с
    ротацией: по логу видно, что делал воркер синхронизации, когда UI
    «ничего не показывал».
    """
    paths.ensure()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = RotatingFileHandler(
        paths.log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # httpx на уровне INFO печатает каждый запрос и ответ: на 25-секундном
    # long-poll это шум, а при обрыве связи — десятки строк в минуту.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return logging.getLogger("chat_multi_cli")
