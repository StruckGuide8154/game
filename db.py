"""
Database helpers: Redis as primary storage with background sync to SQLite backup.
"""
import os
import json
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import g

DATABASE = os.path.join(os.path.dirname(__file__), "game.db")

# Background executor for non-blocking DB writes
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="db_sync")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def _get_standalone_db():
    """Get a standalone DB connection for background threads."""
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT    UNIQUE NOT NULL,
            pw_hash     TEXT    NOT NULL,
            created_at  REAL    NOT NULL,
            is_admin    INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS game_states (
            user_id     INTEGER PRIMARY KEY REFERENCES users(id),
            state_json  TEXT    NOT NULL,
            updated_at  REAL   NOT NULL
        )
    """)
    # Migrate: add is_admin column if missing
    try:
        db.execute("SELECT is_admin FROM users LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    db.commit()
    db.close()


def load_state(user_id, redis_client=None):
    """Load game state from Redis (primary) or SQLite (backup)."""
    from game_logic import migrate_state, default_state

    # Try Redis first (primary storage)
    if redis_client:
        try:
            data = redis_client.get(f"game:state:{user_id}")
            if data:
                st = json.loads(data)
                migrate_state(st)
                return st
        except Exception:
            pass

    # Fallback to SQLite
    db = get_db()
    row = db.execute("SELECT state_json FROM game_states WHERE user_id=?", (user_id,)).fetchone()
    if row:
        st = json.loads(row["state_json"])
        migrate_state(st)
        # Sync to Redis in background if we loaded from SQLite
        if redis_client:
            _sync_to_redis_bg(user_id, st, redis_client)
        return st
    return default_state()


def _sync_to_redis_bg(user_id, st, redis_client):
    """Background sync state to Redis."""
    def _sync():
        try:
            redis_client.set(f"game:state:{user_id}", json.dumps(st), ex=86400 * 30)
        except Exception:
            pass
    _executor.submit(_sync)


def _sync_to_sqlite_bg(user_id, state_json):
    """Background sync state to SQLite."""
    def _sync():
        try:
            db = _get_standalone_db()
            now = time.time()
            db.execute(
                """INSERT INTO game_states (user_id, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (user_id, state_json, now),
            )
            db.commit()
            db.close()
        except Exception:
            pass
    _executor.submit(_sync)


def save_state(user_id, st, redis_client=None):
    """Save game state: Redis immediately (primary), SQLite in background (backup)."""
    state_json = json.dumps(st)

    # Save to Redis immediately (primary storage) - this is fast
    if redis_client:
        try:
            redis_client.set(f"game:state:{user_id}", state_json, ex=86400 * 30)
        except Exception:
            # If Redis fails, fall back to synchronous SQLite save
            db = get_db()
            now = time.time()
            db.execute(
                """INSERT INTO game_states (user_id, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (user_id, state_json, now),
            )
            db.commit()
            return

    # Sync to SQLite in background (backup storage)
    _sync_to_sqlite_bg(user_id, state_json)
