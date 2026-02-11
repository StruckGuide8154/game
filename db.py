"""
Database helpers: Redis as primary storage with background sync to SQLite backup.
"""
import os
import json
import time
import sqlite3
import logging
from concurrent.futures import ThreadPoolExecutor

from flask import g

# Set up logging for debugging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

DATABASE = os.path.join(os.path.dirname(__file__), "game.db")
logger.info(f"Database path: {DATABASE}")

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
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_listings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id   INTEGER NOT NULL REFERENCES users(id),
            seller_name TEXT    NOT NULL,
            player_json TEXT    NOT NULL,
            price       REAL   NOT NULL,
            listed_at   REAL   NOT NULL,
            status      TEXT   NOT NULL DEFAULT 'active'
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_offers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id    INTEGER NOT NULL REFERENCES trade_listings(id),
            buyer_id      INTEGER NOT NULL REFERENCES users(id),
            buyer_name    TEXT    NOT NULL,
            offer_amount  REAL   NOT NULL,
            created_at    REAL   NOT NULL,
            status        TEXT   NOT NULL DEFAULT 'pending'
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

    logger.debug(f"load_state called for user_id={user_id}, redis_client={redis_client is not None}")

    # Try Redis first (primary storage)
    if redis_client:
        try:
            key = f"game:state:{user_id}"
            data = redis_client.get(key)
            logger.debug(f"Redis GET {key}: {'found data' if data else 'no data'}")
            if data:
                st = json.loads(data)
                migrate_state(st)
                logger.info(f"Loaded state from Redis for user {user_id}")
                return st
        except Exception as e:
            logger.error(f"Redis load failed for user {user_id}: {e}")

    # Fallback to SQLite
    logger.debug(f"Falling back to SQLite for user {user_id}")
    db = get_db()
    row = db.execute("SELECT state_json FROM game_states WHERE user_id=?", (user_id,)).fetchone()
    if row:
        st = json.loads(row["state_json"])
        migrate_state(st)
        logger.info(f"Loaded state from SQLite for user {user_id}")
        # Sync to Redis in background if we loaded from SQLite
        if redis_client:
            logger.debug(f"Syncing SQLite state to Redis for user {user_id}")
            _sync_to_redis_bg(user_id, st, redis_client)
        return st

    logger.info(f"No existing state for user {user_id}, returning default")
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
    """Save game state. Redis primary with background SQLite, or SQLite-only if no Redis."""
    state_json = json.dumps(st)
    logger.debug(f"save_state called for user_id={user_id}, redis_client={redis_client is not None}, state_size={len(state_json)}")

    if redis_client:
        # Redis available: save to Redis immediately, SQLite in background
        try:
            key = f"game:state:{user_id}"
            redis_client.set(key, state_json, ex=86400 * 30)
            logger.info(f"Saved state to Redis for user {user_id}")
            # Background sync to SQLite as backup
            _sync_to_sqlite_bg(user_id, state_json)
            return
        except Exception as e:
            logger.error(f"Redis save failed for user {user_id}: {e}, falling back to SQLite")

    # No Redis or Redis failed: save to SQLite synchronously (required for persistence)
    logger.debug(f"Saving to SQLite synchronously for user {user_id}")
    db = get_db()
    now = time.time()
    db.execute(
        """INSERT INTO game_states (user_id, state_json, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
        (user_id, state_json, now),
    )
    db.commit()
    logger.info(f"Saved state to SQLite for user {user_id}")


# ---------------------------------------------------------------------------
# User account storage (Redis primary with SQLite backup)
# ---------------------------------------------------------------------------
def save_user_to_redis(redis_client, user_id, username, pw_hash, is_admin, created_at):
    """Save user account to Redis."""
    if not redis_client:
        return False
    try:
        user_data = {
            "id": user_id,
            "username": username,
            "pw_hash": pw_hash,
            "is_admin": is_admin,
            "created_at": created_at,
        }
        # Store by username for login lookup
        redis_client.set(f"user:name:{username}", json.dumps(user_data), ex=86400 * 365)
        # Store by ID for ID lookup
        redis_client.set(f"user:id:{user_id}", json.dumps(user_data), ex=86400 * 365)
        # Add to user set for enumeration
        redis_client.sadd("users:all", username)
        logger.info(f"Saved user {username} (id={user_id}) to Redis")
        return True
    except Exception as e:
        logger.error(f"Failed to save user to Redis: {e}")
        return False


def load_user_by_username(redis_client, username):
    """Load user by username from Redis first, then SQLite."""
    # Try Redis first
    if redis_client:
        try:
            data = redis_client.get(f"user:name:{username}")
            if data:
                user = json.loads(data)
                logger.debug(f"Loaded user {username} from Redis")
                return user
        except Exception as e:
            logger.error(f"Redis user lookup failed: {e}")

    # Fallback to SQLite
    db = get_db()
    row = db.execute(
        "SELECT id, username, pw_hash, is_admin, created_at FROM users WHERE username=?",
        (username,)
    ).fetchone()
    if row:
        user = {
            "id": row["id"],
            "username": row["username"],
            "pw_hash": row["pw_hash"],
            "is_admin": row["is_admin"],
            "created_at": row["created_at"],
        }
        # Sync to Redis if available
        if redis_client:
            save_user_to_redis(redis_client, user["id"], user["username"],
                               user["pw_hash"], user["is_admin"], user["created_at"])
        return user
    return None


def check_username_exists(redis_client, username):
    """Check if username exists in Redis or SQLite."""
    # Check Redis first
    if redis_client:
        try:
            if redis_client.exists(f"user:name:{username}"):
                return True
        except Exception as e:
            logger.error(f"Redis username check failed: {e}")

    # Check SQLite
    db = get_db()
    row = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    return row is not None


def sync_users_from_redis(redis_client):
    """Sync all users from Redis to SQLite on startup."""
    if not redis_client:
        logger.info("No Redis client, skipping user sync")
        return 0

    try:
        usernames = redis_client.smembers("users:all")
        if not usernames:
            logger.info("No users in Redis to sync")
            return 0

        db = _get_standalone_db()
        synced = 0
        for username_bytes in usernames:
            username = username_bytes.decode() if isinstance(username_bytes, bytes) else username_bytes
            try:
                data = redis_client.get(f"user:name:{username}")
                if not data:
                    continue
                user = json.loads(data)

                # Check if user exists in SQLite
                existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                if existing:
                    # Update existing user
                    db.execute(
                        "UPDATE users SET pw_hash=?, is_admin=? WHERE username=?",
                        (user["pw_hash"], user.get("is_admin", 0), username)
                    )
                else:
                    # Insert new user with specific ID
                    db.execute(
                        "INSERT INTO users (id, username, pw_hash, created_at, is_admin) VALUES (?, ?, ?, ?, ?)",
                        (user["id"], user["username"], user["pw_hash"],
                         user.get("created_at", time.time()), user.get("is_admin", 0))
                    )
                synced += 1
            except Exception as e:
                logger.error(f"Failed to sync user {username}: {e}")

        db.commit()
        db.close()
        logger.info(f"Synced {synced} users from Redis to SQLite")
        return synced
    except Exception as e:
        logger.error(f"User sync from Redis failed: {e}")
        return 0
