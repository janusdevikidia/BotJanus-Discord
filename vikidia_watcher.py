from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord

log = logging.getLogger("botjanus_discord.vikidia_watcher")

API_URL = "https://fr.vikidia.org/w/api.php"
WIKI_BASE = "https://fr.vikidia.org/wiki/"
PARIS_TZ = ZoneInfo("Europe/Paris")

# Fichier d'état local (dernières pages/sections vues), toujours à côté de ce fichier.
STATE_PATH = pathlib.Path(__file__).parent / "vikidia_watch_state.json"

HEADERS = {"User-Agent": "BotJanus-VikidiaWatcher/1.0 (bot Discord de Vikidia)"}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# --- Catégories surveillées : une nouvelle page dans la catégorie = une notification ---
CATEGORY_SOURCES = [
    {
        "key": "ticket_en_attente",
        "title": "Catégorie:Ticket en attente",
        "label": "🎫 Nouveau ticket en attente",
        "color": discord.Color.gold(),
    },
    {
        "key": "suppression_immediate",
        "title": "Catégorie:Suppression immédiate",
        "label": "🗑️ Nouvelle demande de suppression immédiate",
        "color": discord.Color.red(),
    },
    {
        "key": "vote_a_traiter",
        "title": "Catégorie:Vote à traiter",
        "label": "🗳️ Nouveau vote à traiter",
        "color": discord.Color.blurple(),
    },
]

# --- Pages mensuelles surveillées section par section (une nouvelle section = une notification) ---
# {month} est remplacé par AAAA_MM (mois courant, heure de Paris) à chaque vérification.
SECTION_SOURCES = [
    {
        "key": "demandes_admins",
        "title_template": "Vikidia:Demandes aux administrateurs/{month}",
        "label": "📮 Nouvelle demande aux administrateurs",
        "color": discord.Color.orange(),
    },
    {
        "key": "alerte",
        "title_template": "Vikidia:Alerte/{month}",
        "label": "🚨 Nouvelle alerte",
        "color": discord.Color.dark_red(),
    },
    {
        "key": "bulletin_admins",
        "title_template": "Vikidia:Bulletin des administrateurs/{month}",
        "label": "📋 Nouveau message au bulletin des administrateurs",
        "color": discord.Color.teal(),
    },
]


def _current_month_slug() -> str:
    now = datetime.now(PARIS_TZ)
    return f"{now.year}_{now.month:02d}"


def _page_url(title: str) -> str:
    return WIKI_BASE + title.replace(" ", "_")


# --- Persistance de l'état (simple fichier JSON, pas besoin d'une vraie base pour ce module) ---

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("État de vikidia_watcher illisible, on repart de zéro.")
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.error("Impossible d'écrire l'état de vikidia_watcher : %s", e)


# --- Appels à l'API MediaWiki de Vikidia ---

async def _get_category_members(session: aiohttp.ClientSession, category_title: str) -> list[str] | None:
    """Renvoie les titres des pages actuellement dans la catégorie, ou None si l'appel a échoué."""
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category_title,
        "cmlimit": "500",
        "format": "json",
    }
    try:
        async with session.get(API_URL, params=params, headers=HEADERS) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            members = data.get("query", {}).get("categorymembers", [])
            return [m["title"] for m in members]
    except Exception as e:
        log.warning("Échec de la requête catégorie %s : %s", category_title, e)
        return None


async def _get_top_level_sections(session: aiohttp.ClientSession, page_title: str) -> list[str] | None:
    """Renvoie les titres des sections de premier niveau (== Titre ==) d'une page.
    Renvoie None si la page est injoignable ou n'existe pas encore (ex : le mois n'a pas
    encore été créé sur le wiki) — ce n'est pas traité comme une erreur bloquante."""
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "sections",
        "format": "json",
    }
    try:
        async with session.get(API_URL, params=params, headers=HEADERS) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if "error" in data:
                return None
            sections = data.get("parse", {}).get("sections", [])
            return [s["line"] for s in sections if s.get("toclevel") == 1]
    except Exception as e:
        log.warning("Échec de la requête sections %s : %s", page_title, e)
        return None


# --- Envoi Discord ---

async def _get_channel(client: discord.Client, channel_id: int) -> discord.abc.Messageable | None:
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except discord.HTTPException as e:
            log.error("Impossible de récupérer le salon de veille Vikidia (%s) : %s", channel_id, e)
            return None
    return channel


async def _notify(
    channel: discord.abc.Messageable, label: str, title: str, url: str, color: discord.Color
) -> None:
    embed = discord.Embed(title=label, description=f"[{title}]({url})", color=color)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as e:
        log.error("Échec de l'envoi de la notification Vikidia : %s", e)


# --- Vérification principale ---

async def check_all(client: discord.Client, channel_id: int) -> None:
    """À appeler périodiquement (voir VIKIDIA_WATCH_INTERVAL_SECONDS). Compare l'état actuel
    de chaque source suivie à l'état enregistré et notifie les nouveautés dans le salon
    configuré. La toute première fois qu'une source est vue (démarrage initial, ou nouveau
    mois pour les pages mensuelles), son état est simplement enregistré sans notification,
    pour ne pas spammer avec tout l'historique déjà présent."""
    channel = await _get_channel(client, channel_id)
    if channel is None:
        return

    state = _load_state()

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        # --- Catégories ---
        for source in CATEGORY_SOURCES:
            current = await _get_category_members(session, source["title"])
            if current is None:
                continue

            key = source["key"]
            if key in state:
                known = set(state[key])
                for title in current:
                    if title not in known:
                        await _notify(channel, source["label"], title, _page_url(title), source["color"])
            state[key] = current

        # --- Pages mensuelles par section ---
        month = _current_month_slug()
        for source in SECTION_SOURCES:
            page_title = source["title_template"].format(month=month)
            current = await _get_top_level_sections(session, page_title)
            if current is None:
                continue

            key = f"{source['key']}:{month}"
            if key in state:
                known = set(state[key])
                for section_title in current:
                    if section_title not in known:
                        anchor = section_title.replace(" ", "_")
                        await _notify(
                            channel,
                            source["label"],
                            section_title,
                            f"{_page_url(page_title)}#{anchor}",
                            source["color"],
                        )
            state[key] = current

    _save_state(state)
