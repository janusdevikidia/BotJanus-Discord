from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from config import DB_PATH

LOCK_KEY = "lock_launch"

# NOTE : l'ancienne liste blanche locale (table whitelist, is_authorized) a été retirée.
# Les droits sont désormais entièrement gérés côté dashboard Flask : un utilisateur doit
# lier son compte avec /auth puis disposer d'un rôle suffisant (voir auth_check.py).
# Seul OWNER_DISCORD_ID (géré directement dans auth_check.py) garde un accès total sans liaison.


def init_db() -> None:
    with _connect() as conn:
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS log_threads (
                thread_id TEXT PRIMARY KEY,
                channel_id TEXT,
                guild_id TEXT,
                script_name TEXT,
                created_at TEXT,
                last_log_line TEXT
            )
        """)
        conn.commit()


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# --- Verrou de lancement ---
# NOTE : ce verrou est local au robot Discord et distinct du verrou "lock_launch" du
# dashboard web (table settings côté Flask). Les deux ne sont volontairement pas unifiés
# ici ; à surveiller si tu veux un jour un verrou unique partagé entre les deux surfaces.

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


# --- Fils de logs en temps réel ---

def add_log_thread(
    thread_id: int, channel_id: int, guild_id: int, script_name: str, created_at: str
) -> None:
    """Enregistre un nouveau fil de suivi de logs (créé au lancement d'un script)."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO log_threads "
            "(thread_id, channel_id, guild_id, script_name, created_at, last_log_line) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(thread_id), str(channel_id), str(guild_id), script_name, created_at, None),
        )
        conn.commit()


def get_log_threads() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, channel_id, guild_id, script_name, created_at, last_log_line "
            "FROM log_threads"
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_log_thread() -> dict | None:
    """Renvoie le fil de logs le plus récemment créé (correspond au script actuellement
    actif, puisqu'un seul script peut tourner à la fois)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT thread_id, channel_id, guild_id, script_name, created_at, last_log_line "
            "FROM log_threads ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def update_log_thread_last_line(thread_id: int, last_line: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE log_threads SET last_log_line = ? WHERE thread_id = ?",
            (last_line, str(thread_id)),
        )
        conn.commit()


def remove_log_thread(thread_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM log_threads WHERE thread_id = ?", (str(thread_id),))
        conn.commit()
