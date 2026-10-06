from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord

import api_client
import database as db
from config import LOG_GUILD_ID, LOG_CHANNEL_ID, SERVICE_LOG_GUILD_ID, SERVICE_LOG_CHANNEL_ID

log = logging.getLogger("botjanus_discord")

THREAD_MAX_AGE_DAYS = 2
LOG_POLL_LIMIT = 200  # nb de lignes de logs récupérées à chaque sondage (pour retrouver les nouvelles)
THREAD_AUTO_ARCHIVE_MINUTES = 4320  # 3 jours : on laisse notre propre nettoyage (2 jours) agir avant l'archivage Discord


def _log_channel_configured() -> bool:
    return LOG_CHANNEL_ID is not None


async def _get_log_channel(client: discord.Client) -> discord.TextChannel | None:
    if not _log_channel_configured():
        return None

    channel = client.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(LOG_CHANNEL_ID)
        except discord.HTTPException:
            log.warning("Impossible de récupérer le salon de logs (LOG_CHANNEL_ID=%s).", LOG_CHANNEL_ID)
            return None

    if LOG_GUILD_ID is not None and getattr(channel, "guild", None) and channel.guild.id != LOG_GUILD_ID:
        log.warning("Le salon LOG_CHANNEL_ID n'appartient pas au serveur LOG_GUILD_ID configuré.")
        return None

    return channel


async def forward_action_message(
    client: discord.Client,
    *,
    content: str,
    script_name: str,
    create_thread: bool,
) -> None:
    """Transfère le message d'action (Lancer/Arrêter) dans le salon de logs.
    Si create_thread est vrai (uniquement pour un Lancement réussi), crée en plus
    un fil dédié qui recevra les logs de BotJanus en temps réel."""
    channel = await _get_log_channel(client)
    if channel is None:
        return

    try:
        message = await channel.send(content)
    except discord.HTTPException as e:
        log.error("Échec de l'envoi du message dans le salon de logs : %s", e)
        return

    if not create_thread:
        return

    try:
        thread = await message.create_thread(
            name=f"Logs — {script_name}"[:100],
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except discord.HTTPException as e:
        log.error("Échec de la création du fil de logs : %s", e)
        return

    db.add_log_thread(
        thread_id=thread.id,
        channel_id=channel.id,
        guild_id=channel.guild.id if channel.guild else 0,
        script_name=script_name,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        await thread.send("📡 Suivi des logs en direct (rafraîchi toutes les minutes)…")
    except discord.HTTPException:
        pass


def _split_for_discord(text: str, limit: int = 1900) -> list[str]:
    """Découpe un texte en morceaux <= limit caractères en essayant de ne pas couper une ligne en deux."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
                current = ""
            if len(line) > limit:
                # Une ligne unique dépasse déjà la limite : découpe brutale.
                for i in range(0, len(line), limit):
                    chunks.append(line[i:i + limit])
            else:
                current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _fetch_thread(client: discord.Client, thread_id: str) -> discord.Thread | None:
    thread = client.get_channel(int(thread_id))
    if thread is not None:
        return thread
    try:
        return await client.fetch_channel(int(thread_id))
    except discord.HTTPException:
        return None


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if hours or minutes:
        parts.append(f"{minutes}min")
    parts.append(f"{secs}s")
    return " ".join(parts)


async def poll_log_threads(client: discord.Client) -> None:
    """À appeler régulièrement : récupère les nouvelles lignes de logs de BotJanus pour
    chaque fil ACTIF (script en cours de suivi) et les poste dans le fil correspondant.
    Ne touche pas aux fils déjà marqués terminés (active=0) : ceux-ci restent
    consultables mais ne sont plus sondés tant qu'ils ne sont pas nettoyés."""
    active_threads = db.get_log_threads(active_only=True)
    if not active_threads:
        return

    # Un seul appel de statut pour tous les fils actifs (il n'y en a de toute façon
    # normalement qu'un seul à la fois, un script à la fois pouvant tourner).
    status = await api_client.get_status()

    for entry in active_threads:
        thread = await _fetch_thread(client, entry["thread_id"])
        if thread is None:
            # Le fil a disparu côté Discord (supprimé manuellement, etc.) : on nettoie la DB.
            db.remove_log_thread(entry["thread_id"])
            continue

        logs = await api_client.get_logs(limit=LOG_POLL_LIMIT)
        if logs:
            last_line = entry.get("last_log_line")
            new_lines = logs
            if last_line and last_line in logs:
                # Dernière occurrence connue -> on ne poste que ce qui vient après.
                idx = len(logs) - 1 - logs[::-1].index(last_line)
                new_lines = logs[idx + 1:]

            if new_lines:
                text = "\n".join(new_lines)
                try:
                    for chunk in _split_for_discord(text):
                        await thread.send(f"```\n{chunk}\n```")
                except discord.HTTPException as e:
                    log.error("Échec de l'envoi des logs dans le fil %s : %s", entry["thread_id"], e)
                else:
                    db.update_log_thread_last_line(entry["thread_id"], logs[-1])

        # Détection de fin d'exécution : le dashboard indique qu'aucun script ne tourne,
        # ou qu'un autre script a pris le relais entre-temps. On ne conclut rien si le
        # dashboard est injoignable (status is None) : on retentera au prochain sondage.
        if status is None:
            continue
        finished = (not status.get("running")) or status.get("script_name") != entry["script_name"]
        if not finished:
            continue

        created_at = datetime.fromisoformat(entry["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        duration = (datetime.now(timezone.utc) - created_at).total_seconds()
        try:
            await thread.send(f"🏁 **{entry['script_name']}** terminé (durée : {_format_duration(duration)}).")
        except discord.HTTPException:
            pass
        db.deactivate_log_thread(entry["thread_id"])


async def cleanup_old_threads(client: discord.Client) -> None:
    """Supprime les fils de logs créés il y a plus de THREAD_MAX_AGE_DAYS jours."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=THREAD_MAX_AGE_DAYS)

    for entry in db.get_log_threads():
        created_at = datetime.fromisoformat(entry["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at > cutoff:
            continue

        thread = await _fetch_thread(client, entry["thread_id"])
        if thread is not None:
            try:
                await thread.delete()
            except discord.HTTPException as e:
                log.error("Échec de la suppression du fil %s : %s", entry["thread_id"], e)

        db.remove_log_thread(entry["thread_id"])


# --- Logs des scripts "en continu" (serveur distant) ---------------------------------
# agent -> dashboard (/api/agent/sync) -> /api/services/logs -> ce bot -> salon SERVICE_LOG_CHANNEL_ID

async def _get_service_log_channel(client: discord.Client) -> discord.abc.Messageable | None:
    if SERVICE_LOG_CHANNEL_ID is None:
        return None
    channel = client.get_channel(SERVICE_LOG_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(SERVICE_LOG_CHANNEL_ID)
        except discord.HTTPException:
            log.warning("Impossible de récupérer le salon des logs continus (SERVICE_LOG_CHANNEL_ID=%s).",
                        SERVICE_LOG_CHANNEL_ID)
            return None
    if SERVICE_LOG_GUILD_ID is not None and getattr(channel, "guild", None) \
            and channel.guild.id != SERVICE_LOG_GUILD_ID:
        log.warning("Le salon SERVICE_LOG_CHANNEL_ID n'appartient pas au serveur SERVICE_LOG_GUILD_ID configuré.")
        return None
    return channel


async def poll_service_logs(client: discord.Client) -> None:
    """À appeler régulièrement : récupère auprès du dashboard les nouvelles lignes de logs des
    scripts continus et les poste dans le salon SERVICE_LOG_CHANNEL_ID, groupées par script.
    Le curseur (id de la dernière ligne postée) est conservé dans la base du bot : un
    redémarrage ne rejoue rien et ne perd rien. Au tout premier démarrage on repart du
    présent (pas de rejeu de l'historique)."""
    if SERVICE_LOG_CHANNEL_ID is None:
        return

    cursor = db.get_service_log_cursor()
    if cursor is None:
        data = await api_client.get_service_logs("latest")
        if data is not None:
            db.set_service_log_cursor(int(data.get("last_id", 0)))
        return

    channel = await _get_service_log_channel(client)
    if channel is None:
        return

    # On boucle tant que le dashboard renvoie un lot plein (rattrapage après une coupure).
    for _ in range(10):
        data = await api_client.get_service_logs(cursor)
        if data is None:
            return  # dashboard injoignable : on retentera au prochain sondage
        if data.get("reset"):
            # le dashboard a été réinitialisé : notre curseur est au-delà de son dernier id
            db.set_service_log_cursor(int(data.get("last_id", 0)))
            return
        lines = data.get("lines") or []
        if not lines:
            return

        # Groupes consécutifs d'un même script : un en-tête + un bloc de code par groupe.
        groups: list[tuple[str, list[str]]] = []
        for item in lines:
            label = str(item.get("label") or item.get("service") or "?")
            text = str(item.get("line", "")).replace("```", "'''")
            if groups and groups[-1][0] == label:
                groups[-1][1].append(text)
            else:
                groups.append((label, [text]))

        try:
            for label, texts in groups:
                for i, chunk in enumerate(_split_for_discord("\n".join(texts), limit=1800)):
                    header = f"📡 **{label}**\n" if i == 0 else ""
                    await channel.send(f"{header}```\n{chunk}\n```")
        except discord.HTTPException as e:
            log.error("Échec de l'envoi des logs continus dans le salon : %s", e)
            return  # curseur non avancé : le lot sera renvoyé au prochain sondage

        cursor = int(data.get("last_id", cursor))
        db.set_service_log_cursor(cursor)
        if len(lines) < 500:
            return



# --- Embeds start / restart / stop des scripts "en continu" -------------------------------

# action -> (couleur, emoji, titre si réussi, titre si échec)
_EVENT_STYLE = {
    "start":   (0x2ECC71, "🟢", "Script démarré",    "Échec du démarrage"),
    "stop":    (0xE74C3C, "🔴", "Script arrêté",     "Échec de l'arrêt"),
    "restart": (0x3498DB, "🔄", "Script redémarré",  "Échec du redémarrage"),
}
_ACTION_VERB = {"start": "le démarrage", "stop": "l'arrêt", "restart": "le redémarrage"}
_COLOR_FAILED = 0x992D22
_COLOR_EXPIRED = 0xF1C40F


def build_service_event_embed(event: dict) -> discord.Embed:
    """Construit l'embed d'un ordre start/stop/restart terminé (réussi, échoué ou expiré)."""
    action = str(event.get("action") or "?")
    status = str(event.get("status") or "?")
    label = str(event.get("label") or event.get("service") or "?")
    color, emoji, title_ok, title_ko = _EVENT_STYLE.get(action, (0x95A5A6, "⚙️", f"Ordre « {action} »", "Échec de l'ordre"))
    verb = _ACTION_VERB.get(action, f"l'ordre « {action} »")
    result = str(event.get("result") or "").strip()

    if status == "done":
        title = f"{emoji} {title_ok}"
        description = f"**{label}**"
        if result:
            description += f"\n> {result[:300]}"
    elif status == "failed":
        color, title = _COLOR_FAILED, f"❌ {title_ko}"
        description = f"**{label}** — {verb} a échoué."
        if result:
            description += f"\n```\n{result[:300]}\n```"
    else:  # expired
        color, title = _COLOR_EXPIRED, "⏳ Ordre expiré"
        description = f"**{label}** — {verb} n'a pas pu être confirmé par l'agent."
        if result:
            description += f"\n> {result[:300]}"

    done_at = float(event.get("done_at") or event.get("requested_at") or 0)
    requested_at = float(event.get("requested_at") or done_at)
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.fromtimestamp(done_at, tz=timezone.utc) if done_at else discord.utils.utcnow(),
    )
    embed.add_field(name="Script", value=f"`{event.get('service') or '?'}`", inline=True)
    embed.add_field(name="Demandé par", value=str(event.get("requested_by") or "—"), inline=True)
    if status == "done":
        embed.add_field(name="Traité en", value=_format_duration(done_at - requested_at), inline=True)
    embed.set_footer(text="BotJanus • Scripts continus")
    return embed


async def poll_service_events(client: discord.Client) -> None:
    """À appeler régulièrement : annonce par un embed chaque ordre start/stop/restart terminé
    sur un script continu, dans SERVICE_LOG_CHANNEL_ID. Même principe que les logs : curseur
    dans la base du bot, pas de rejeu de l'historique au premier démarrage."""
    if SERVICE_LOG_CHANNEL_ID is None:
        return

    cursor = db.get_service_event_cursor()
    if cursor is None:
        data = await api_client.get_service_events("latest")
        if data is not None:
            db.set_service_event_cursor(int(data.get("last_id", 0)))
        return

    channel = await _get_service_log_channel(client)
    if channel is None:
        return

    for _ in range(10):
        data = await api_client.get_service_events(cursor)
        if data is None:
            return
        if data.get("reset"):
            db.set_service_event_cursor(int(data.get("last_id", 0)))
            return
        events = data.get("events") or []
        if not events:
            return

        for event in events:
            try:
                await channel.send(embed=build_service_event_embed(event))
            except discord.HTTPException as e:
                log.error("Échec de l'envoi de l'embed d'événement continu : %s", e)
                return  # curseur non avancé sur cet événement : renvoyé au prochain sondage
            cursor = int(event["id"])
            db.set_service_event_cursor(cursor)
        if len(events) < 50:
            return
