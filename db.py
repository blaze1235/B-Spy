import sqlite3
import threading
from datetime import datetime, timezone

import config

_lock = threading.Lock()
_conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")


def init_db():
    with _lock, _conn:
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connections (
                connection_id TEXT PRIMARY KEY,
                owner_user_id INTEGER,
                owner_chat_id INTEGER,
                is_enabled INTEGER,
                updated_at TEXT
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                connection_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                sender_id INTEGER,
                sender_name TEXT,
                chat_title TEXT,
                text TEXT,
                media_type TEXT,
                media_path TEXT,
                date TEXT,
                deleted INTEGER DEFAULT 0,
                PRIMARY KEY (connection_id, chat_id, msg_id)
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edit_history (
                connection_id TEXT,
                chat_id INTEGER,
                msg_id INTEGER,
                old_text TEXT,
                new_text TEXT,
                edited_at TEXT
            )
            """
        )


def upsert_connection(connection_id, owner_user_id, owner_chat_id, is_enabled):
    with _lock, _conn:
        _conn.execute(
            """
            INSERT INTO connections (connection_id, owner_user_id, owner_chat_id, is_enabled, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                owner_user_id=excluded.owner_user_id,
                owner_chat_id=excluded.owner_chat_id,
                is_enabled=excluded.is_enabled,
                updated_at=excluded.updated_at
            """,
            (connection_id, owner_user_id, owner_chat_id, int(is_enabled), datetime.now(timezone.utc).isoformat()),
        )


def get_connection(connection_id):
    with _lock:
        cur = _conn.execute("SELECT * FROM connections WHERE connection_id=?", (connection_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def upsert_message(connection_id, chat_id, msg_id, sender_id, sender_name, chat_title, text, media_type, media_path, date):
    with _lock, _conn:
        _conn.execute(
            """
            INSERT INTO messages (connection_id, chat_id, msg_id, sender_id, sender_name, chat_title, text, media_type, media_path, date, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(connection_id, chat_id, msg_id) DO UPDATE SET
                text=excluded.text,
                media_type=COALESCE(excluded.media_type, messages.media_type),
                media_path=COALESCE(excluded.media_path, messages.media_path)
            """,
            (connection_id, chat_id, msg_id, sender_id, sender_name, chat_title, text, media_type, media_path, date),
        )


def get_message(connection_id, chat_id, msg_id):
    with _lock:
        cur = _conn.execute(
            "SELECT * FROM messages WHERE connection_id=? AND chat_id=? AND msg_id=?",
            (connection_id, chat_id, msg_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def mark_deleted(connection_id, chat_id, msg_id):
    with _lock, _conn:
        _conn.execute(
            "UPDATE messages SET deleted=1 WHERE connection_id=? AND chat_id=? AND msg_id=?",
            (connection_id, chat_id, msg_id),
        )


def add_edit_history(connection_id, chat_id, msg_id, old_text, new_text):
    with _lock, _conn:
        _conn.execute(
            "INSERT INTO edit_history (connection_id, chat_id, msg_id, old_text, new_text, edited_at) VALUES (?, ?, ?, ?, ?, ?)",
            (connection_id, chat_id, msg_id, old_text, new_text, datetime.now(timezone.utc).isoformat()),
        )
