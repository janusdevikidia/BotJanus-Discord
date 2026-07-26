from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord

import api_client
import database as db
from config import LOG_GUILD_ID, LOG_CHANNEL_ID

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


async def poll_log_threads(client: discord.Client) -> None:
    """À appeler régulièrement (toutes les minutes) : récupère les nouvelles lignes
    de logs de BotJanus pour chaque fil actif et les poste dans le fil correspondant."""
    for entry in db.get_log_threads():
        thread = await _fetch_thread(client, entry["thread_id"])
        if thread is None:
            # Le fil a disparu côté Discord (supprimé manuellement, etc.) : on nettoie la DB.
            db.remove_log_thread(entry["thread_id"])
            continue

        logs = await api_client.get_logs(limit=LOG_POLL_LIMIT)
        if not logs:
            continue

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
                continue
            db.update_log_thread_last_line(entry["thread_id"], logs[-1])


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
