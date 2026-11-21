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
        # Keyed by owner_user_id (not connection_id) so a block survives the
        # person disconnecting and reconnecting the bot, which issues a new
        # connection_id each time.
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocked_users (
                owner_user_id INTEGER PRIMARY KEY,
                blocked_at TEXT
            )
            """
        )
        # Columns added after the initial release - kept as migrations so an
        # already-running deployment's data doesn't need to be wiped.
        _ensure_column("connections", "owner_username", "TEXT")
        _ensure_column("connections", "owner_first_name", "TEXT")
        _ensure_column("connections", "plan", "TEXT DEFAULT 'free'")
        _ensure_column("connections", "first_connected_at", "TEXT")
        _ensure_column("messages", "sender_username", "TEXT")


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
        result = dict(zip(cols, row))
    result["blocked"] = is_user_blocked(result["owner_user_id"])
    return result


def set_plan(connection_id, plan):
    with _lock, _conn:
        _conn.execute("UPDATE connections SET plan=? WHERE connection_id=?", (plan, connection_id))


_STATS_SELECT = """
    SELECT
        c.rowid AS rid,
        c.connection_id,
        c.owner_user_id,
        c.owner_chat_id,
        c.owner_username,
        c.owner_first_name,
        c.is_enabled,
        c.plan,
        c.first_connected_at,
        c.updated_at,
        EXISTS(SELECT 1 FROM blocked_users b WHERE b.owner_user_id = c.owner_user_id) AS blocked,
        (SELECT COUNT(*) FROM messages m WHERE m.connection_id = c.connection_id) AS message_count,
        (SELECT COUNT(*) FROM edit_history e WHERE e.connection_id = c.connection_id) AS edit_count,
        (SELECT COUNT(*) FROM messages m WHERE m.connection_id = c.connection_id AND m.deleted = 1) AS delete_count,
        (SELECT COUNT(*) FROM messages m WHERE m.connection_id = c.connection_id AND m.media_path IS NOT NULL) AS media_count
    FROM connections c
"""


def list_connections_with_stats():
    """One row per connected user, with usage counts, for the admin panel."""
    with _lock:
        cur = _conn.execute(_STATS_SELECT + " ORDER BY c.first_connected_at DESC")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_connection_with_stats(rid):
    """Look up by SQLite rowid - short enough to fit in Telegram's 64-byte
    button callback_data, unlike connection_id."""
    with _lock:
        cur = _conn.execute(_STATS_SELECT + " WHERE c.rowid=?", (rid,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


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


def is_user_blocked(owner_user_id):
    with _lock:
        cur = _conn.execute("SELECT 1 FROM blocked_users WHERE owner_user_id=?", (owner_user_id,))
        return cur.fetchone() is not None


def set_user_blocked(owner_user_id, blocked):
    with _lock, _conn:
        if blocked:
            _conn.execute(
                "INSERT INTO blocked_users (owner_user_id, blocked_at) VALUES (?, ?) "
                "ON CONFLICT(owner_user_id) DO NOTHING",
                (owner_user_id, datetime.now(timezone.utc).isoformat()),
            )
        else:
            _conn.execute("DELETE FROM blocked_users WHERE owner_user_id=?", (owner_user_id,))


def delete_connection(connection_id):
    """Purge a connection's stored data (messages, edit history, the
    connection record itself). Does not affect any block - use
    set_user_blocked for that."""
    with _lock, _conn:
        _conn.execute("DELETE FROM messages WHERE connection_id=?", (connection_id,))
        _conn.execute("DELETE FROM edit_history WHERE connection_id=?", (connection_id,))
        _conn.execute("DELETE FROM connections WHERE connection_id=?", (connection_id,))


# deleted column values:
#   0 = live (still in the real chat, as far as we know)
#   1 = the sender deleted it (what the bot was built to catch)
#   2 = the admin deleted it from the real chat via this panel
# Both 1 and 2 count as "gone", so the re-notify guard (if row["deleted"])
# skips them; only 1 is counted as a "delete caught" in the stats.

def list_senders(connection_id):
    """People who messaged this owner and still have live messages, most
    messages first. Bare sender_name/username come from the newest message
    thanks to SQLite's single-MAX() bare-column rule."""
    with _lock:
        cur = _conn.execute(
            """
            SELECT sender_id,
                   COUNT(*) AS cnt,
                   sender_name,
                   sender_username,
                   MAX(date) AS newest
            FROM messages
            WHERE connection_id=? AND sender_id IS NOT NULL AND deleted=0
            GROUP BY sender_id
            ORDER BY cnt DESC, newest DESC
            """,
            (connection_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_sender_summary(connection_id, sender_id):
    with _lock:
        cur = _conn.execute(
            """
            SELECT COUNT(*) AS cnt, sender_name, sender_username, MAX(date) AS newest
            FROM messages
            WHERE connection_id=? AND sender_id=? AND deleted=0
            """,
            (connection_id, sender_id),
        )
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def get_sender_live_messages(connection_id, sender_id):
    """Live (deleted=0) messages from this sender - the ones we can still
    ask Telegram to delete."""
    with _lock:
        cur = _conn.execute(
            "SELECT chat_id, msg_id, media_path FROM messages "
            "WHERE connection_id=? AND sender_id=? AND deleted=0",
            (connection_id, sender_id),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def set_messages_deleted_state(connection_id, chat_id, msg_ids, state):
    if not msg_ids:
        return
    placeholders = ",".join("?" for _ in msg_ids)
    with _lock, _conn:
        _conn.execute(
            f"UPDATE messages SET deleted=? WHERE connection_id=? AND chat_id=? AND msg_id IN ({placeholders})",
            [state, connection_id, chat_id, *msg_ids],
        )


def upsert_message(
    connection_id, chat_id, msg_id, sender_id, sender_name, chat_title, text, media_type, media_path, date,
    sender_username=None,
):
    with _lock, _conn:
        _conn.execute(
            """
            INSERT INTO messages (
                connection_id, chat_id, msg_id, sender_id, sender_name, sender_username,
                chat_title, text, media_type, media_path, date, deleted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(connection_id, chat_id, msg_id) DO UPDATE SET
                text=excluded.text,
                media_type=COALESCE(excluded.media_type, messages.media_type),
                media_path=COALESCE(excluded.media_path, messages.media_path),
                sender_username=COALESCE(excluded.sender_username, messages.sender_username)
            """,
            (
                connection_id, chat_id, msg_id, sender_id, sender_name, sender_username,
                chat_title, text, media_type, media_path, date,
            ),
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
