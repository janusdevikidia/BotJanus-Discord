from __future__ import annotations

import sqlite3
from datetime import datetime
from contextlib import contextmanager

from config import DB_PATH, OWNER_DISCORD_ID

LOCK_KEY = "lock_launch"


def is_authorized(user_id: int) -> bool:
    """L'admin (owner) est toujours autorisé, même s'il n'est pas dans la liste blanche."""
    return user_id == OWNER_DISCORD_ID or is_whitelisted(user_id)


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whitelist (
                discord_id TEXT PRIMARY KEY,
                username TEXT,
                added_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (LOCK_KEY, "0"),
        )
        conn.commit()


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# --- Liste blanche ---

def is_whitelisted(discord_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM whitelist WHERE discord_id = ?", (str(discord_id),)
        ).fetchone()
        return row is not None


def add_to_whitelist(discord_id: int, username: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO whitelist (discord_id, username, added_at) VALUES (?, ?, ?)",
            (str(discord_id), username, datetime.now().isoformat()),
        )
        conn.commit()


def remove_from_whitelist(discord_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM whitelist WHERE discord_id = ?", (str(discord_id),))
        conn.commit()


def get_whitelist() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT discord_id, username, added_at FROM whitelist ORDER BY added_at"
        ).fetchall()
        return [dict(r) for r in rows]


# --- Verrou de lancement ---

def get_lock() -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (LOCK_KEY,)
        ).fetchone()
        return bool(row) and row["value"] == "1"


def set_lock(locked: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (LOCK_KEY, "1" if locked else "0"),
        )
        conn.commit()
