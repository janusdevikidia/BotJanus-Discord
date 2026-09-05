from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

LOCK_KEY = "lock_launch"
QUEUE_LOCK_KEY = "queue_disabled"

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
                last_log_line TEXT,
                active INTEGER DEFAULT 1
            )
        """)
        # Migration : les bases créées avant l'ajout du suivi de fin d'exécution
        # n'ont pas la colonne "active". On l'ajoute si besoin, sans rien casser.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(log_threads)")}
        if "active" not in existing_cols:
            conn.execute("ALTER TABLE log_threads ADD COLUMN active INTEGER DEFAULT 1")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_name TEXT NOT NULL,
                discord_user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                extra_params TEXT,
                created_at TEXT NOT NULL
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
            "(thread_id, channel_id, guild_id, script_name, created_at, last_log_line, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (str(thread_id), str(channel_id), str(guild_id), script_name, created_at, None),
        )
        conn.commit()


def get_log_threads(active_only: bool = False) -> list[dict]:
    query = (
        "SELECT thread_id, channel_id, guild_id, script_name, created_at, last_log_line, active "
        "FROM log_threads"
    )
    if active_only:
        query += " WHERE active = 1"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
        return [dict(r) for r in rows]


def get_latest_log_thread() -> dict | None:
    """Renvoie le fil de logs actif le plus récent (correspond au script actuellement
    en cours de suivi, puisqu'un seul script peut tourner à la fois)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT thread_id, channel_id, guild_id, script_name, created_at, last_log_line, active "
            "FROM log_threads WHERE active = 1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def deactivate_log_thread(thread_id: int) -> None:
    """Marque un fil comme terminé (le script suivi a fini de tourner) : il sort du
    polling rapide mais reste consultable jusqu'à son nettoyage (2 jours)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE log_threads SET active = 0 WHERE thread_id = ?", (str(thread_id),)
        )
        conn.commit()


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


# --- File d'attente de scripts (/queue) ---

def add_to_queue(
    script_name: str,
    discord_user_id: int,
    username: str,
    extra_params: dict | None,
    created_at: str,
) -> int:
    """Ajoute une tâche en fin de file. Renvoie son id (utile pour /queue cancel)."""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO queue (script_name, discord_user_id, username, extra_params, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                script_name,
                str(discord_user_id),
                username,
                json.dumps(extra_params) if extra_params else None,
                created_at,
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_queue() -> list[dict]:
    """Renvoie la file dans l'ordre de passage (la plus ancienne tâche en premier)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, script_name, discord_user_id, username, extra_params, created_at "
            "FROM queue ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def count_queue() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM queue").fetchone()
        return row["c"] if row else 0


def remove_queue_item(queue_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
        conn.commit()
        return cur.rowcount > 0


def pop_next_queue_item() -> dict | None:
    """Retire et renvoie la tâche la plus ancienne de la file, ou None si elle est vide."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, script_name, discord_user_id, username, extra_params, created_at "
            "FROM queue ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM queue WHERE id = ?", (row["id"],))
        conn.commit()
        return dict(row)


def clear_queue() -> int:
    """Vide entièrement la file d'attente (action admin). Renvoie le nombre de
    tâches supprimées."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM queue")
        conn.commit()
        return cur.rowcount


def get_queue_disabled() -> bool:
    """Renvoie True si l'administrateur a désactivé la file d'attente : plus aucun
    ajout n'est accepté et le worker ne dépile plus automatiquement (les tâches déjà
    présentes restent en attente jusqu'à réactivation)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (QUEUE_LOCK_KEY,)
        ).fetchone()
        return bool(row) and row["value"] == "1"


def set_queue_disabled(disabled: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (QUEUE_LOCK_KEY, "1" if disabled else "0"),
        )
        conn.commit()
