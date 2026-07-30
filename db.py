import sqlite3
import threading
from datetime import datetime, timezone

import config

_lock = threading.Lock()
_conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
_conn.execute("PRAGMA journal_mode=WAL")


def _ensure_column(table, column, coltype_with_default):
    cols = [row[1] for row in _conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_with_default}")


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
        # Columns added after the initial release - kept as migrations so an
        # already-running deployment's data doesn't need to be wiped.
        _ensure_column("connections", "owner_username", "TEXT")
        _ensure_column("connections", "owner_first_name", "TEXT")
        _ensure_column("connections", "plan", "TEXT DEFAULT 'free'")
        _ensure_column("connections", "first_connected_at", "TEXT")


def upsert_connection(connection_id, owner_user_id, owner_chat_id, is_enabled, owner_username=None, owner_first_name=None):
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _conn:
        _conn.execute(
            """
            INSERT INTO connections (
                connection_id, owner_user_id, owner_chat_id, is_enabled,
                owner_username, owner_first_name, updated_at, first_connected_at, plan
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'free')
            ON CONFLICT(connection_id) DO UPDATE SET
                owner_user_id=excluded.owner_user_id,
                owner_chat_id=excluded.owner_chat_id,
                is_enabled=excluded.is_enabled,
                owner_username=excluded.owner_username,
                owner_first_name=excluded.owner_first_name,
                updated_at=excluded.updated_at
            """,
            (connection_id, owner_user_id, owner_chat_id, int(is_enabled), owner_username, owner_first_name, now, now),
        )


def get_connection(connection_id):
    with _lock:
        cur = _conn.execute("SELECT * FROM connections WHERE connection_id=?", (connection_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def set_plan(connection_id, plan):
    with _lock, _conn:
        _conn.execute("UPDATE connections SET plan=? WHERE connection_id=?", (plan, connection_id))


def list_connections_with_stats():
    """One row per connected user, with usage counts, for the admin dashboard."""
    with _lock:
        cur = _conn.execute(
            """
            SELECT
                c.connection_id,
                c.owner_user_id,
                c.owner_chat_id,
                c.owner_username,
                c.owner_first_name,
                c.is_enabled,
                c.plan,
                c.first_connected_at,
                c.updated_at,
                (SELECT COUNT(*) FROM messages m WHERE m.connection_id = c.connection_id) AS message_count,
                (SELECT COUNT(*) FROM edit_history e WHERE e.connection_id = c.connection_id) AS edit_count,
                (SELECT COUNT(*) FROM messages m WHERE m.connection_id = c.connection_id AND m.deleted = 1) AS delete_count,
                (SELECT COUNT(*) FROM messages m WHERE m.connection_id = c.connection_id AND m.media_path IS NOT NULL) AS media_count
            FROM connections c
            ORDER BY c.first_connected_at DESC
            """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_totals():
    with _lock:
        row = _conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM connections) AS total_users,
                (SELECT COUNT(*) FROM connections WHERE is_enabled = 1) AS active_users,
                (SELECT COUNT(*) FROM messages) AS total_messages,
                (SELECT COUNT(*) FROM edit_history) AS total_edits,
                (SELECT COUNT(*) FROM messages WHERE deleted = 1) AS total_deletes,
                (SELECT COUNT(*) FROM messages WHERE media_path IS NOT NULL) AS total_media
            """
        ).fetchone()
        cols = ["total_users", "active_users", "total_messages", "total_edits", "total_deletes", "total_media"]
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
