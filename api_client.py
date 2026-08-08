from __future__ import annotations

import aiohttp

from config import FLASK_API_URL, FLASK_API_KEY

HEADERS = {"X-API-Key": FLASK_API_KEY, "Content-Type": "application/json"}
TIMEOUT = aiohttp.ClientTimeout(total=10)


async def get_status() -> dict | None:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(f"{FLASK_API_URL}/api/status", headers=HEADERS) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception:
        return None


async def get_scripts() -> list[dict] | None:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(f"{FLASK_API_URL}/api/scripts", headers=HEADERS) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("scripts", [])
    except Exception:
        return None


async def get_logs(limit: int = 20) -> list[str] | None:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(
                f"{FLASK_API_URL}/api/logs", headers=HEADERS, params={"limit": limit}
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("logs", [])
    except Exception:
        return None


async def start_script(choice: str, username: str, extra: dict | None = None) -> tuple[bool, str]:
    payload = {"choice": choice, "username": username}
    if extra:
        payload.update(extra)
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(f"{FLASK_API_URL}/api/start", headers=HEADERS, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("success"):
                    return True, data.get("message", "Lancé.")
                return False, data.get("error", f"Erreur HTTP {resp.status}")
    except Exception as e:
        return False, f"Erreur de connexion au dashboard : {e}"


async def stop_script() -> tuple[bool, str]:
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(f"{FLASK_API_URL}/api/stop", headers=HEADERS) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("success"):
                    return True, data.get("message", "Arrêté.")
                return False, data.get("error", f"Erreur HTTP {resp.status}")
    except Exception as e:
        return False, f"Erreur de connexion au dashboard : {e}"


async def start_discord_link(discord_id: int, discord_username: str) -> str | None:
    """Demande au dashboard un lien de liaison à usage unique (compte Discord -> compte
    Vikidia), utilisé par la commande /auth. Renvoie l'URL à envoyer à l'utilisateur, ou
    None en cas d'échec."""
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(
                f"{FLASK_API_URL}/api/discord/link/start", headers=HEADERS,
                json={"discord_id": discord_id, "discord_username": discord_username},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("url")
    except Exception:
        return None


async def get_discord_permissions(discord_id: int) -> dict | None:
    """Interroge le dashboard pour savoir si discord_id est lié à un compte wiki et si ce
    compte a les droits nécessaires (rôle Collaborateur/Admin). None si injoignable."""
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(
                f"{FLASK_API_URL}/api/discord/permissions", headers=HEADERS,
                params={"discord_id": discord_id},
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception:
        return None