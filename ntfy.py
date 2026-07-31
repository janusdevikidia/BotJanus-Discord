from __future__ import annotations

import logging
import re

import aiohttp

from config import NTFY_SERVER, NTFY_TOPIC_STATUS, NTFY_TOPIC_URGENT

log = logging.getLogger("botjanus_discord")

TIMEOUT = aiohttp.ClientTimeout(total=10)

# Mots-clés (insensibles à la casse) qui déclenchent une alerte sur le topic "urgent".
# Recherche par sous-chaîne : "errors" matche "error", "erreurs" matche "erreur", etc.
URGENT_KEYWORDS = [
    "error", "erreur",
    "warning", "attention",
    "critical", "critique",
    "fatal",
    "exception", "traceback",
    "échec", "echec", "failed", "failure",
    "denied", "refusé", "refuse",
]

_MARKDOWN_RE = re.compile(r"[*_`]")


def strip_markdown(text: str) -> str:
    """Retire la mise en forme Discord (**gras**, `code`, etc.) avant envoi en notif push."""
    return _MARKDOWN_RE.sub("", text)


def find_alert_keywords(line: str) -> list[str]:
    """Renvoie la liste des mots-clés d'alerte trouvés dans une ligne (peut être vide)."""
    lower = line.lower()
    return [kw for kw in URGENT_KEYWORDS if kw in lower]


async def _send(
    topic: str,
    message: str,
    *,
    title: str | None = None,
    priority: str | None = None,
    tags: str | None = None,
) -> None:
    if not topic:
        return

    headers: dict[str, str] = {}
    if title:
        # ntfy exige de l'ASCII dans les headers HTTP ; on encode le reste en UTF-8 via un header dédié.
        headers["Title"] = title.encode("utf-8").decode("latin-1", errors="ignore")
    if priority:
        headers["Priority"] = priority
    if tags:
        headers["Tags"] = tags

    url = f"{NTFY_SERVER}/{topic}"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(url, data=message.encode("utf-8"), headers=headers) as resp:
                if resp.status >= 300:
                    log.warning("ntfy : échec de l'envoi sur '%s' (HTTP %s).", topic, resp.status)
    except Exception as e:
        log.error("ntfy : erreur de connexion sur '%s' : %s", topic, e)


async def notify_status(message: str) -> None:
    """Notif discrète pour un Lancement/Arrêt de script (topic 'botjanus')."""
    await _send(NTFY_TOPIC_STATUS, strip_markdown(message), title="BotJanus", tags="robot")


async def notify_urgent(script_name: str, matched_lines: list[str]) -> None:
    """Notif prioritaire quand une ligne de log contient un mot-clé d'alerte (topic 'botjanus-urgent')."""
    body = "\n".join(matched_lines[-10:])  # on limite pour ne pas noyer la notif
    if len(matched_lines) > 10:
        body = f"(+{len(matched_lines) - 10} autres lignes)\n{body}"
    await _send(
        NTFY_TOPIC_URGENT,
        body,
        title=f"🚨 BotJanus — alerte logs ({script_name})",
        priority="urgent",
        tags="rotating_light,warning",
    )
