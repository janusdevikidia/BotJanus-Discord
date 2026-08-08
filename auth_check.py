from __future__ import annotations

import api_client
from config import OWNER_DISCORD_ID

UNLINKED_MESSAGE = "🚫 Tu dois d'abord lier ton compte Discord à ton compte Vikidia avec `/auth`."
UNAUTHORIZED_MESSAGE = (
    "🚫 Ton compte Vikidia n'a pas (encore) les droits nécessaires "
    "(autopatrol, patrouilleur, administrateur ou bureaucrate) pour lancer ou arrêter le robot."
)
BANNED_MESSAGE = "🚫 Ton compte est banni sur le dashboard BotJanus."
UNREACHABLE_MESSAGE = "⚠️ Impossible de contacter le dashboard pour vérifier tes droits. Réessaie plus tard."


async def check_discord_authorized(discord_id: int) -> tuple[bool, dict]:
    """Renvoie (autorisé, infos). infos peut contenir linked/authorized/banned/role/username,
    ou {"error": "unreachable"} si le dashboard n'a pas pu être contacté."""
    if discord_id == OWNER_DISCORD_ID:
        return True, {"linked": True, "authorized": True, "owner": True}

    perms = await api_client.get_discord_permissions(discord_id)
    if perms is None:
        return False, {"error": "unreachable"}
    return bool(perms.get("authorized")), perms


def denial_message(perms: dict) -> str:
    """Message à afficher à l'utilisateur pour expliquer pourquoi l'action est refusée."""
    if perms.get("error") == "unreachable":
        return UNREACHABLE_MESSAGE
    if not perms.get("linked"):
        return UNLINKED_MESSAGE
    if perms.get("banned"):
        return BANNED_MESSAGE
    return UNAUTHORIZED_MESSAGE
