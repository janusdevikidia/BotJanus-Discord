from __future__ import annotations

import os
import pathlib
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

# --- Salon de transfert des logs BotJanus ---
# Optionnel : si non renseigné, le transfert des messages et la création
# des fils de logs en temps réel sont simplement désactivés.
_log_guild_id = os.environ.get("LOG_GUILD_ID")
LOG_GUILD_ID: int | None = int(_log_guild_id) if _log_guild_id else None

_log_channel_id = os.environ.get("LOG_CHANNEL_ID")
LOG_CHANNEL_ID: int | None = int(_log_channel_id) if _log_channel_id else None

# --- Divers ---
# Nom du script qui nécessite des paramètres supplémentaires (langue/catégorie/portail)
PORTAL_SCRIPT_NAME: str = "portal.py"

# Chemin de la base SQLite locale du bot (liste blanche + verrou).
# Par défaut, toujours à côté de ce fichier, peu importe le dossier depuis
# lequel Python est lancé (VS Code, terminal, service systemd, etc.).
_default_db_path = str(pathlib.Path(__file__).parent / "botjanus_discord.db")
DB_PATH: str = os.environ.get("BOT_DB_PATH") or _default_db_path

# Délai minimum (secondes) entre deux actions Lancer/Arrêter, pour éviter
# le spam de clics vers l'API Flask. Modifiable via .env si besoin.
ACTION_COOLDOWN_SECONDS: int = int(os.environ.get("ACTION_COOLDOWN_SECONDS", "10"))

# Fréquence (secondes) à laquelle le bot vérifie l'état du script pour
# mettre à jour son statut Discord ("Regarde 🟢 Actif (script.py)" etc.).
PRESENCE_REFRESH_SECONDS: int = int(os.environ.get("PRESENCE_REFRESH_SECONDS", "20"))

# --- Notifications push (ntfy.sh) ---
# Serveur ntfy à utiliser (par défaut l'instance publique ntfy.sh).
# Peut être remplacé par une instance auto-hébergée via NTFY_SERVER.
NTFY_SERVER: str = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

# Topic pour les notifications "normales" : Lancement / Arrêt d'un script.
NTFY_TOPIC_STATUS: str = os.environ.get("NTFY_TOPIC_STATUS", "votre_topic_status")

# Topic pour les alertes : ligne de log contenant "error", "warning", etc.
NTFY_TOPIC_URGENT: str = os.environ.get("NTFY_TOPIC_URGENT", "salon_urgent")