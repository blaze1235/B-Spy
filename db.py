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
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                sender_id INTEGER,
                chat_title TEXT,
                sender_name TEXT,
                text TEXT,
                media_type TEXT,
                media_path TEXT,
                date TEXT,
                deleted INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, msg_id)
            )
            """
        )
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edit_history (
                chat_id INTEGER NOT NULL,
                msg_id INTEGER NOT NULL,
                old_text TEXT,
                new_text TEXT,
                edited_at TEXT
            )
            """
        )
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_msgid ON messages(msg_id)")


def upsert_message(chat_id, msg_id, sender_id, chat_title, sender_name, text, media_type, media_path, date):
    with _lock, _conn:
        _conn.execute(
            """
            INSERT INTO messages (chat_id, msg_id, sender_id, chat_title, sender_name, text, media_type, media_path, date, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(chat_id, msg_id) DO UPDATE SET
                text=excluded.text,
                media_type=COALESCE(excluded.media_type, messages.media_type),
                media_path=COALESCE(excluded.media_path, messages.media_path)
            """,
            (chat_id, msg_id, sender_id, chat_title, sender_name, text, media_type, media_path, date),
        )


def get_message(chat_id, msg_id):
    with _lock:
        cur = _conn.execute("SELECT * FROM messages WHERE chat_id=? AND msg_id=?", (chat_id, msg_id))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def find_by_msgid_any_chat(msg_id, limit=5):
    """Deleted-message events for private chats/small groups don't include
    the chat id (a Telegram API limitation), so when we can't match by
    chat_id we fall back to searching recent cached messages by msg_id."""
    with _lock:
        cur = _conn.execute(
            "SELECT * FROM messages WHERE msg_id=? AND deleted=0 ORDER BY date DESC LIMIT ?",
            (msg_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def mark_deleted(chat_id, msg_id):
    with _lock, _conn:
        _conn.execute("UPDATE messages SET deleted=1 WHERE chat_id=? AND msg_id=?", (chat_id, msg_id))


def add_edit_history(chat_id, msg_id, old_text, new_text):
    with _lock, _conn:
        _conn.execute(
            "INSERT INTO edit_history (chat_id, msg_id, old_text, new_text, edited_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, msg_id, old_text, new_text, datetime.now(timezone.utc).isoformat()),
        )
