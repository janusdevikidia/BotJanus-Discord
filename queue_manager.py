from __future__ import annotations

import json

import discord

import api_client
import database as db
import log_forwarding


def build_queue_embed(status: dict | None) -> discord.Embed:
    """Construit l'embed montré par /queue : script en cours + file d'attente ordonnée."""
    embed = discord.Embed(title="📑 File d'attente BotJanus", color=discord.Color.blurple())

    if status is None:
        embed.add_field(name="En cours", value="⚠️ Dashboard injoignable", inline=False)
    elif status.get("running"):
        embed.add_field(name="En cours", value=f"🟢 `{status.get('script_name')}`", inline=False)
    else:
        embed.add_field(name="En cours", value="🔴 Aucun script actif", inline=False)

    if db.get_queue_disabled():
        embed.add_field(
            name="⚠️ File d'attente désactivée",
            value="Les ajouts et le lancement automatique sont bloqués par l'administrateur.",
            inline=False,
        )

    queue = db.get_queue()
    if not queue:
        embed.add_field(name="File d'attente", value="Vide.", inline=False)
    else:
        lines = [
            f"**{i}.** `{item['script_name']}` — demandé par {item['username']} (id `{item['id']}`)"
            for i, item in enumerate(queue, start=1)
        ]
        embed.add_field(name=f"File d'attente ({len(queue)})", value="\n".join(lines), inline=False)

    return embed


async def run_worker_tick(client: discord.Client) -> None:
    """Appelé périodiquement (voir QUEUE_CHECK_SECONDS) : si aucun script ne tourne et
    que la file n'est pas vide, dépile la tâche la plus ancienne et la lance."""
    if db.get_queue_disabled():
        return
    if db.count_queue() == 0:
        return

    status = await api_client.get_status()
    if status is None or status.get("running"):
        return

    item = db.pop_next_queue_item()
    if item is None:
        return

    extra = json.loads(item["extra_params"]) if item["extra_params"] else None
    success, message = await api_client.start_script(
        item["script_name"], username=item["username"], extra=extra
    )

    mention = f"<@{item['discord_user_id']}>"
    if success:
        content = f"✅ (file d'attente) {mention} — **{item['script_name']}** vient d'être lancé automatiquement."
        await client.update_presence()  # Actualisation instantanée
    else:
        content = f"⚠️ (file d'attente) Échec du lancement de **{item['script_name']}** pour {mention} : {message}"

    # Transfert dans le salon de logs, avec fil de suivi si le lancement a réussi.
    await log_forwarding.forward_action_message(
        client, content=content, script_name=item["script_name"], create_thread=success,
    )
