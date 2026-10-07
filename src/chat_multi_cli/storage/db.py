"""Подключение к локальной SQLite: WAL, миграции, соединение на поток.

Почему соединение на поток, а не одно общее: sqlite3 не любит, когда одним
соединением пользуются из двух потоков (GIL не спасает — блокировки всё равно
пересекаются). Поэтому у каждого потока своё соединение, а запись дополнительно
сериализована WriterThread: читать могут все, писать — только он.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: Версия схемы. Растёт вместе с миграциями в _MIGRATIONS.
SCHEMA_VERSION = 1

_SCHEMA_FILE = Path(__file__).with_name("schema.sql")

#: Хвост миграций: (версия, SQL). Каждая версия применяется один раз и
#: оборачивается в транзакцию, поэтому оборванная миграция не оставляет
#: половину схемы.
_MIGRATIONS: tuple[tuple[int, str], ...] = ((1, _SCHEMA_FILE.read_text(encoding="utf-8")),)


class Database:
    """Локальная база клиента.

    Открывает соединения лениво и держит по одному на поток. Закрывать нужно
    все: у вызывающего потока — close(), у писателя — WriterThread.stop().
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- Соединения ----------

    def connect(self) -> sqlite3.Connection:
        """Соединение текущего потока (создаётся при первом обращении)."""
        existing: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if existing is not None:
            return existing

        connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,  # транзакции управляем явно: с autocommit забытый
            # BEGIN неявно открыл бы транзакцию на первом же чтении и заблокировал
            # файл для остальных потоков.
            check_same_thread=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")

        self._local.connection = connection
        with self._lock:
            self._connections.append(connection)
        return connection

    def close(self) -> None:
        """Закрывает соединение текущего потока."""
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            return
        with self._lock:
            if connection in self._connections:
                self._connections.remove(connection)
        connection.close()
        self._local.connection = None

    def close_all(self) -> None:
        """Закрывает соединения всех потоков.

        sqlite3 запрещает закрывать соединение из чужого потока, а WriterThread
        закрывает своё сам при остановке. Здесь игнорируем именно эту ошибку:
        она означает «этим соединением уже не пользуются», а не проблему.
        Настоящие ошибки закрытия логируем.
        """
        with self._lock:
            connections, self._connections = self._connections, []
        for connection in connections:
            try:
                connection.close()
            except sqlite3.ProgrammingError:
                logger.debug("Соединение уже закрыто другим потоком, пропускаем")
            except sqlite3.Error as exc:
                logger.warning("Не удалось закрыть соединение: %s", exc)
        self._local.connection = None

    # ---------- Миграции ----------

    def migrate(self) -> int:
        """Приводит схему к текущей версии. Возвращает итоговую версию."""
        connection = self.connect()
        with self._lock:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])

        for version, sql in _MIGRATIONS:
            if version <= current:
                continue
            logger.info("Миграция локальной БД: %d", version)
            connection.executescript(sql)
            connection.execute(f"PRAGMA user_version = {version}")
            current = version

        if current != SCHEMA_VERSION:
            raise RuntimeError(
                f"Схема БД ({current}) новее кода ({SCHEMA_VERSION}): обновите приложение"
            )
        return current

    # ---------- Транзакции ----------

    @staticmethod
    def transaction(connection: sqlite3.Connection) -> _Transaction:
        """Контекстный менеджер транзакции: BEGIN IMMEDIATE ... COMMIT/ROLLBACK.

        IMMEDIATE, а не DEFERRED: запись всегда должна взять блокировку сразу,
        иначе два писателя получат отложенный конфликт на COMMIT.
        """
        return _Transaction(connection)


class _Transaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._connection.execute("BEGIN IMMEDIATE")
        return self._connection

    def __exit__(self, exc_type: object, *_rest: object) -> None:
        if exc_type is None:
            self._connection.execute("COMMIT")
        else:
            self._connection.execute("ROLLBACK")


# ---------- Мелкие помощники для записи ----------


#: Текущее время в том же формате, что и сервер (ISO-8601 без миллисекунд).
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else default


def get_int_meta(connection: sqlite3.Connection, key: str, default: int = 0) -> int:
    raw = get_meta(connection, key)
    try:
        return int(raw)
    except ValueError:
        return default


def get_meta_flag(connection: sqlite3.Connection, key: str) -> bool:
    """Читает флаг из meta, не сбрасывая его (в отличие от consume_*)."""
    return get_meta(connection, key) == "1"
