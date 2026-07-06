from __future__ import annotations

import time

from config import ACTION_COOLDOWN_SECONDS

_last_action_at: float = 0.0


def check_cooldown() -> float:
    """Renvoie 0 si une action est autorisée maintenant, sinon le nombre de
    secondes restantes avant la prochaine action autorisée."""
    elapsed = time.monotonic() - _last_action_at
    remaining = ACTION_COOLDOWN_SECONDS - elapsed
    return remaining if remaining > 0 else 0.0


def record_action() -> None:
    """À appeler juste avant de lancer une action (Lancer/Arrêter réel)."""
    global _last_action_at
    _last_action_at = time.monotonic()