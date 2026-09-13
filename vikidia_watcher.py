from __future__ import annotations

import html
import json
import logging
import pathlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord

log = logging.getLogger("botjanus_discord.vikidia_watcher")

API_URL = "https://fr.vikidia.org/w/api.php"
WIKI_BASE = "https://fr.vikidia.org/wiki/"
PARIS_TZ = ZoneInfo("Europe/Paris")

STATE_PATH = pathlib.Path(__file__).parent / "vikidia_watch_state.json"

HEADERS = {"User-Agent": "BotJanus-VikidiaWatcher/1.0 (bot Discord de Vikidia)"}
TIMEOUT = aiohttp.ClientTimeout(total=15)

# Nettoyage HTML et détection des états wikitext
RE_HTML_TAGS = re.compile(r"<[^>]+>")
RE_ETAT_FAIT = re.compile(r"é?tat\s*=\s*(?:<!--[\s\S]*?-->\s*)*fait\b", re.IGNORECASE)
RE_ETAT_FERME = re.compile(r"é?tat\s*=\s*(?:<!--[\s\S]*?-->\s*)*fermé\b", re.IGNORECASE)
RE_ETAT_REFUSE = re.compile(r"é?tat\s*=\s*(?:<!--[\s\S]*?-->\s*)*refusé?\b", re.IGNORECASE)

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


def _clean_title(text: str) -> str:
    """Supprime les balises HTML et décode les entités (ex: <span> et &amp;)."""
    cleaned = RE_HTML_TAGS.sub("", text)
    return html.unescape(cleaned).strip()


def _current_month_slug() -> str:
    now = datetime.now(PARIS_TZ)
    return f"{now.year}_{now.month:02d}"


def _page_url(title: str) -> str:
    return WIKI_BASE + title.replace(" ", "_")


# --- Persistance de l'état ---

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


def _normalize_state_key(state: dict, key: str) -> dict[str, dict]:
    """Migration Rétrocompatible : transforme une liste simple de titres en dict enrichi."""
    raw = state.get(key)
    if not raw:
        return {}
    if isinstance(raw, list):
        return {title: {"msg_id": None, "status": "active"} for title in raw}
    if isinstance(raw, dict):
        return raw
    return {}


# --- Requêtes API MediaWiki ---

async def _get_category_members(session: aiohttp.ClientSession, category_title: str) -> list[str] | None:
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


async def _page_exists(session: aiohttp.ClientSession, title: str) -> bool:
    params = {"action": "query", "titles": title, "format": "json"}
    try:
        async with session.get(API_URL, params=params, headers=HEADERS) as resp:
            if resp.status != 200:
                return True
            data = await resp.json()
            pages = data.get("query", {}).get("pages", {})
            for p_id, p_info in pages.items():
                if p_id == "-1" or "missing" in p_info:
                    return False
            return True
    except Exception as e:
        log.warning("Échec de vérification d'existence de la page %s : %s", title, e)
        return True


async def _get_page_wikitext(session: aiohttp.ClientSession, title: str) -> str | None:
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
    }
    try:
        async with session.get(API_URL, params=params, headers=HEADERS) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("parse", {}).get("wikitext", {}).get("*")
    except Exception as e:
        log.warning("Échec de la récupération du wikitext de %s : %s", title, e)
        return None


async def _get_sections_details(session: aiohttp.ClientSession, page_title: str) -> list[dict] | None:
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
            return [
                {
                    "index": s["index"],
                    "title": _clean_title(s["line"]),
                    "anchor": s.get("anchor", _clean_title(s["line"]).replace(" ", "_")),
                }
                for s in sections
                if s.get("toclevel") == 1
            ]
    except Exception as e:
        log.warning("Échec de la requête sections %s : %s", page_title, e)
        return None


async def _get_section_wikitext(session: aiohttp.ClientSession, page_title: str, section_index: str) -> str | None:
    params = {
        "action": "parse",
        "page": page_title,
        "section": section_index,
        "prop": "wikitext",
        "format": "json",
    }
    try:
        async with session.get(API_URL, params=params, headers=HEADERS) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("parse", {}).get("wikitext", {}).get("*")
    except Exception as e:
        log.warning("Échec de la récupération du wikitext de la section %s (%s) : %s", section_index, page_title, e)
        return None


# --- Actions Discord ---

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
) -> int | None:
    embed = discord.Embed(title=label, description=f"[{title}]({url})", color=color)
    try:
        msg = await channel.send(embed=embed)
        return msg.id
    except discord.HTTPException as e:
        log.error("Échec de l'envoi de la notification Vikidia : %s", e)
        return None


async def _add_reaction(channel: discord.abc.Messageable, msg_id: int | None, emoji: str) -> None:
    if not msg_id:
        return
    try:
        msg = await channel.fetch_message(msg_id)
        await msg.add_reaction(emoji)
    except discord.HTTPException as e:
        log.error("Impossible d'ajouter la réaction %s au message %s : %s", emoji, msg_id, e)


# --- Fonction principale de vérification ---

async def check_all(client: discord.Client, channel_id: int) -> None:
    channel = await _get_channel(client, channel_id)
    if channel is None:
        return

    state = _load_state()

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        # --- 1. Traitement des Catégories ---
        for source in CATEGORY_SOURCES:
            key = source["key"]
            current_members = await _get_category_members(session, source["title"])
            if current_members is None:
                continue

            known = _normalize_state_key(state, key)
            current_set = set(current_members)

            # Démarrage initial : enregistrement sans notifier
            if key not in state:
                state[key] = {title: {"msg_id": None, "status": "active"} for title in current_members}
                continue

            # Nouveautés dans la catégorie
            for title in current_members:
                if title not in known:
                    msg_id = await _notify(channel, source["label"], title, _page_url(title), source["color"])
                    known[title] = {"msg_id": msg_id, "status": "active"}

            # Mise à jour des éléments suivis
            for title, info in list(known.items()):
                if info.get("status") != "active":
                    continue

                msg_id = info.get("msg_id")

                if key == "ticket_en_attente":
                    exists = await _page_exists(session, title)
                    if not exists:
                        await _add_reaction(channel, msg_id, "🗑️")
                        info["status"] = "deleted"
                    else:
                        wikitext = await _get_page_wikitext(session, title)
                        if wikitext:
                            if RE_ETAT_FERME.search(wikitext):
                                await _add_reaction(channel, msg_id, "✅")
                                info["status"] = "done"
                            elif RE_ETAT_REFUSE.search(wikitext):
                                await _add_reaction(channel, msg_id, "🗑️")
                                info["status"] = "refused"
                            elif title not in current_set:
                                await _add_reaction(channel, msg_id, "✅")
                                info["status"] = "done"
                        elif title not in current_set:
                            await _add_reaction(channel, msg_id, "✅")
                            info["status"] = "done"
                else:
                    if title not in current_set:
                        await _add_reaction(channel, msg_id, "✅")
                        info["status"] = "done"

            state[key] = known

        # --- 2. Traitement des Pages mensuelles par section ---
        month = _current_month_slug()
        for source in SECTION_SOURCES:
            page_title = source["title_template"].format(month=month)
            sections = await _get_sections_details(session, page_title)
            if sections is None:
                continue

            key = f"{source['key']}:{month}"
            known = _normalize_state_key(state, key)
            current_map = {s["title"]: (s["index"], s["anchor"]) for s in sections}

            # Démarrage initial / Nouveau mois : enregistrement sans notifier
            if key not in state:
                state[key] = {s["title"]: {"msg_id": None, "status": "active"} for s in sections}
                continue

            # Nouvelles sections apparues
            for s in sections:
                s_title = s["title"]
                s_anchor = s["anchor"]
                if s_title not in known:
                    msg_id = await _notify(
                        channel,
                        source["label"],
                        s_title,
                        f"{_page_url(page_title)}#{s_anchor}",
                        source["color"],
                    )
                    known[s_title] = {"msg_id": msg_id, "status": "active"}

            # Mise à jour des sections suivies
            for s_title, info in list(known.items()):
                if info.get("status") != "active":
                    continue

                msg_id = info.get("msg_id")

                if s_title not in current_map:
                    # Section supprimée/annulée
                    await _add_reaction(channel, msg_id, "🗑️")
                    info["status"] = "deleted"
                else:
                    sec_index, _ = current_map[s_title]
                    sec_text = await _get_section_wikitext(session, page_title, sec_index)
                    if sec_text:
                        if RE_ETAT_FAIT.search(sec_text):
                            await _add_reaction(channel, msg_id, "✅")
                            info["status"] = "done"
                        elif RE_ETAT_REFUSE.search(sec_text):
                            await _add_reaction(channel, msg_id, "🗑️")
                            info["status"] = "refused"

            state[key] = known

    _save_state(state)
