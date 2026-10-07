-- Локальный кэш клиента. Это не копия серверной базы, а «быстрый ответ для UI»:
-- всё, что здесь лежит, можно в любой момент удалить и загрузить заново по REST.
--
-- Ключевые решения:
--
-- 1. Курсор лежит в meta, а не в отдельной таблице: он один на клиента, и
--    двигать его нужно в той же транзакции, что и события, иначе после падения
--    между commit'ами события приедут повторно (dedup это переживёт, но
--    «unread» может сбиться).
--
-- 2. applied_events — защита от at-least-once доставки. Сервер вправе прислать
--    событие дважды; без этой таблицы сообщение задвоилось бы в истории.
--    Таблица растёт, поэтому чистится по seq (см. writer.prune_applied).
--
-- 3. read_seq живёт в заявке, а не в messages: «прочитано до N» — это одно
--    число на заявку, и пересчитывать его из сообщений незачем.
--
-- 4. unread считается запросом, а не хранится колонкой: счётчик зависит от
--    того, где остановился пользователь, а не от состояния сервера.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applied_events (
    seq        INTEGER PRIMARY KEY,
    type       TEXT NOT NULL,
    ticket_id  TEXT,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id                     TEXT PRIMARY KEY,
    status                 TEXT NOT NULL,
    subject                TEXT NOT NULL DEFAULT '',
    text                   TEXT NOT NULL DEFAULT '',
    source                 TEXT NOT NULL DEFAULT '',
    workshop_id            TEXT,
    workshop_name          TEXT,
    client_id              TEXT,
    client_name            TEXT NOT NULL DEFAULT '',
    client_phone           TEXT,
    owner_id               TEXT,
    owner_name             TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    closed_at              TEXT,
    close_reason           TEXT,
    read_seq               INTEGER NOT NULL DEFAULT 0,
    last_message_seq       INTEGER NOT NULL DEFAULT 0,
    last_message_sender    TEXT,
    last_message_preview   TEXT NOT NULL DEFAULT '',
    hidden                 INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets (owner_id);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    ticket_id        TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    sender           TEXT NOT NULL,
    sender_name      TEXT NOT NULL DEFAULT '',
    sender_master_id TEXT,
    text             TEXT NOT NULL DEFAULT '',
    attachments      TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    delivery         TEXT NOT NULL DEFAULT 'queued',
    read_at          TEXT,
    UNIQUE (ticket_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_ticket ON messages (ticket_id, seq);

CREATE TABLE IF NOT EXISTS ticket_members (
    ticket_id TEXT NOT NULL,
    master_id TEXT NOT NULL,
    full_name TEXT NOT NULL DEFAULT '',
    role      TEXT NOT NULL DEFAULT 'collaborator',
    joined_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (ticket_id, master_id)
);

CREATE TABLE IF NOT EXISTS masters (
    id             TEXT PRIMARY KEY,
    full_name      TEXT NOT NULL DEFAULT '',
    workshop_id    TEXT,
    workshop_name  TEXT,
    online         INTEGER NOT NULL DEFAULT 0,
    active_tickets INTEGER NOT NULL DEFAULT 0
);