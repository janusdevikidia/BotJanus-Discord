"""
Chargement de la configuration depuis le fichier .env.
Ne contient aucun secret en dur : tout vient du .env que tu remplis toi-même.
"""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Variable d'environnement manquante : {name}. "
            f"Vérifie ton fichier .env (voir .env.example)."
        )
    return value


# --- Discord ---
DISCORD_BOT_TOKEN: str = _require("DISCORD_BOT_TOKEN")
OWNER_DISCORD_ID: int = int(_require("OWNER_DISCORD_ID"))
COMMAND_PREFIX: str = os.environ.get("COMMAND_PREFIX", "!")

# Optionnel : si renseigné, les commandes slash se synchronisent instantanément
# sur ce serveur (pratique en dev). Sinon, synchronisation globale (~1h de délai).
_guild_id = os.environ.get("DISCORD_GUILD_ID")
DISCORD_GUILD_ID: int | None = int(_guild_id) if _guild_id else None

# --- API Flask (dashboard PythonAnywhere) ---
FLASK_API_URL: str = _require("FLASK_API_URL").rstrip("/")
FLASK_API_KEY: str = _require("FLASK_API_KEY")

# --- Divers ---
# Nom du script qui nécessite des paramètres supplémentaires (langue/catégorie/portail)
PORTAL_SCRIPT_NAME: str = "portal.py"

# Chemin de la base SQLite locale du bot (liste blanche + verrou)
import pathlib

_default_db_path = str(pathlib.Path(__file__).parent / "botjanus_discord.db")
DB_PATH: str = os.environ.get("BOT_DB_PATH") or _default_db_path
