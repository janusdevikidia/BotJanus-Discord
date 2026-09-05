import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from config import (
    DISCORD_BOT_TOKEN,
    DISCORD_GUILD_ID,
    COMMAND_PREFIX,
    OWNER_DISCORD_ID,
    PRESENCE_REFRESH_SECONDS,
    PORTAL_SCRIPT_NAME,
    LOG_POLL_ACTIVE_SECONDS,
    LOG_POLL_IDLE_SECONDS,
    QUEUE_CHECK_SECONDS,
)
import database as db
import api_client
import auth_check
import cooldown
import log_forwarding
import queue_manager
from views import (
    DashboardView,
    build_status_embed,
    AdminView,
    build_admin_embed,
    PortalModal,
    QueueAddView,
    QueueView,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("botjanus_discord")

intents = discord.Intents.default()
intents.message_content = True  # requis pour lire !dashboard / !admin

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


async def _build_presence_text() -> str:
    status = await api_client.get_status()
    if status is None:
        return "⚠️ Dashboard injoignable"
    if status.get("running"):
        return f"🟢 Actif ({status.get('script_name') or '?'})"
    return "🔴 Arrêté"


# Nouvelle fonction attachée à l'instance de bot pour forcer l'actualisation
async def update_presence() -> None:
    text = await _build_presence_text()
    await bot.change_presence(activity=discord.CustomActivity(name=text, state=text))

bot.update_presence = update_presence


@tasks.loop(seconds=PRESENCE_REFRESH_SECONDS)
async def refresh_presence():
    await bot.update_presence()


_current_log_poll_interval = LOG_POLL_IDLE_SECONDS


@tasks.loop(seconds=LOG_POLL_IDLE_SECONDS)
async def refresh_log_threads():
    """Poste les nouvelles lignes de logs de BotJanus dans les fils actifs, poste un
    récapitulatif quand un script se termine, et supprime les fils de plus de 2 jours.

    Le rythme est dynamique : LOG_POLL_ACTIVE_SECONDS (rapide, quasi temps réel) tant
    qu'au moins un fil suit un script en cours, LOG_POLL_IDLE_SECONDS (repos) sinon,
    pour ne pas marteler l'API Flask pour rien."""
    global _current_log_poll_interval

    await log_forwarding.poll_log_threads(bot)
    await log_forwarding.cleanup_old_threads(bot)

    has_active_thread = bool(db.get_log_threads(active_only=True))
    target = LOG_POLL_ACTIVE_SECONDS if has_active_thread else LOG_POLL_IDLE_SECONDS
    if target != _current_log_poll_interval:
        _current_log_poll_interval = target
        refresh_log_threads.change_interval(seconds=target)
        log.info("Polling des logs ajusté à %ss (fil actif : %s).", target, has_active_thread)


@tasks.loop(seconds=QUEUE_CHECK_SECONDS)
async def process_queue():
    """Vérifie régulièrement si un script vient de se libérer pour dépiler et
    lancer automatiquement la prochaine tâche de la file d'attente (/queue)."""
    await queue_manager.run_worker_tick(bot)


@bot.event
async def on_ready():
    db.init_db()
    try:
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            log.info(f"{len(synced)} commande(s) synchronisée(s) sur le serveur {DISCORD_GUILD_ID} (instantané).")
        else:
            synced = await bot.tree.sync()
            log.info(f"{len(synced)} commande(s) synchronisée(s) globalement (peut prendre jusqu'à 1h à apparaître).")
    except Exception as e:
        log.error(f"Erreur de synchronisation des commandes : {e}")

    if not refresh_presence.is_running():
        refresh_presence.start()

    if not refresh_log_threads.is_running():
        refresh_log_threads.start()

    if not process_queue.is_running():
        process_queue.start()

    log.info(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")


async def _delete_invoking_message(ctx: commands.Context) -> None:
    """Supprime le message '!commande' de l'utilisateur (sans effet en slash)."""
    if ctx.interaction is None:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


async def _deny(ctx: commands.Context, message: str) -> None:
    """Répond en éphémère si slash, sinon envoie un message qui s'autodétruit."""
    if ctx.interaction is not None:
        await ctx.send(message, ephemeral=True)
    else:
        warning = await ctx.send(message)
        await asyncio.sleep(8)
        try:
            await warning.delete()
        except discord.HTTPException:
            pass


async def _consume_cooldown(ctx: commands.Context) -> bool:
    """Vérifie le cooldown anti-spam avant une action Lancer/Arrêter réelle (/start, /stop)."""
    remaining = cooldown.check_cooldown()
    if remaining > 0:
        await _deny(ctx, f"⏳ Merci d'attendre encore {remaining:.0f}s avant de relancer une action (anti-spam).")
        return False
    cooldown.record_action()
    return True


@bot.tree.command(
    name="auth",
    description="Lie ton compte Discord à ton compte Vikidia (nécessaire pour lancer/arrêter le robot)",
)
async def auth_command(interaction: discord.Interaction):
    # En commande slash, le premier paramètre est un discord.Interaction (et plus un commands.Context)
    await interaction.response.defer(ephemeral=True)

    url = await api_client.start_discord_link(interaction.user.id, str(interaction.user))
    if not url:
        await interaction.followup.send(
            "⚠️ Impossible de contacter le dashboard pour générer le lien de liaison. Réessaie plus tard.",
            ephemeral=True
        )
        return

    message = (
        "🔗 Clique sur ce lien pour connecter ton compte Discord à ton compte Vikidia "
        f"(lien à usage unique, valable quelques minutes) :\n{url}\n\n"
        "Une fois connecté, tu pourras utiliser `/dashboard`, `/start` et `/stop` si ton compte "
        "wiki dispose des droits nécessaires (autopatrol, patroller, sysop ou bureaucrat)."
    )
    
    await interaction.followup.send(message, ephemeral=True)
    
@bot.hybrid_command(
    name="dashboard",
    description="Affiche l'état du robot Vikidia et permet de le lancer/arrêter",
)
async def dashboard(ctx: commands.Context):
    await _delete_invoking_message(ctx)
    # Ack immédiat : l'appel vers PythonAnywhere peut dépasser les 3s
    # tolérées par Discord avant qu'il considère l'interaction comme expirée.
    await ctx.defer()

    authorized, perms = await auth_check.check_discord_authorized(ctx.author.id)
    if not authorized:
        await _deny(ctx, auth_check.denial_message(perms))
        return

    status = await api_client.get_status()
    if status is None:
        await _deny(ctx, "⚠️ Impossible de contacter le dashboard Flask. Vérifie qu'il est bien en ligne.")
        return

    embed = build_status_embed(status)
    view = DashboardView(running=status["running"], script_name=status.get("script_name"))
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="admin",
    description="Gérer les utilisateurs autorisés et le verrou de lancement (réservé à l'administrateur)",
)
async def admin(ctx: commands.Context):
    await _delete_invoking_message(ctx)
    await ctx.defer()

    if ctx.author.id != OWNER_DISCORD_ID:
        await _deny(ctx, "🚫 Cette commande est réservée à l'administrateur.")
        return

    embed = build_admin_embed()
    view = AdminView()
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="start",
    description="Lance directement un script (ne fonctionne pas si un script est déjà actif)",
)
@discord.app_commands.describe(script="Nom du fichier script à lancer (ex: monscript.py)")
async def start(ctx: commands.Context, script: str):
    await _delete_invoking_message(ctx)

    authorized, perms = await auth_check.check_discord_authorized(ctx.author.id)
    if not authorized:
        await _deny(ctx, auth_check.denial_message(perms))
        return

    if db.get_lock() and ctx.author.id != OWNER_DISCORD_ID:
        await _deny(ctx, "🔒 Le lancement est verrouillé par l'administrateur.")
        return

    # portal.py nécessite des paramètres supplémentaires : formulaire disponible uniquement en slash.
    if script == PORTAL_SCRIPT_NAME:
        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            if not await _consume_cooldown(ctx):
                return
            await ctx.interaction.response.send_modal(PortalModal(script, None))
            return
        await _deny(
            ctx,
            "⚠️ `portal.py` nécessite des paramètres supplémentaires (langue/catégorie/portail) : "
            "utilise `/start` (slash) pour remplir le formulaire.",
        )
        return

    await ctx.defer()

    scripts = await api_client.get_scripts()
    if scripts is None:
        await _deny(ctx, "⚠️ Impossible de récupérer la liste des scripts depuis le dashboard.")
        return
    match = next((s for s in scripts if s["filename"] == script), None)
    if match is None:
        await _deny(
            ctx,
            f"⚠️ Script inconnu : `{script}`. Utilise `/start` pour voir la liste proposée automatiquement.",
        )
        return
    if not match.get("is_active", True) and ctx.author.id != OWNER_DISCORD_ID:
        await _deny(ctx, "🚫 Ce script est désactivé sur le dashboard web. Seul l'administrateur peut le lancer.")
        return

    status = await api_client.get_status()
    if status is None:
        await _deny(ctx, "⚠️ Impossible de contacter le dashboard Flask.")
        return
    if status.get("running"):
        if db.get_queue_disabled():
            await _deny(
                ctx,
                f"⚠️ Un script est déjà actif : **{status.get('script_name')}**. "
                "La file d'attente est en plus désactivée par l'administrateur : réessaie plus tard.",
            )
            return
        position = db.count_queue() + 1
        view = QueueAddView(
            script=script, extra=None, discord_id=ctx.author.id, username=ctx.author.display_name
        )
        await ctx.send(
            f"⚠️ Un script est déjà actif : **{status.get('script_name')}**. "
            f"Ajouter **{script}** à la file d'attente (position {position}) ?",
            view=view,
        )
        return

    if not await _consume_cooldown(ctx):
        return

    success, message = await api_client.start_script(script, username=ctx.author.display_name)
    if success:
        content = f"✅ **{ctx.author.display_name}** a lancé **{script}**."
        await bot.update_presence()  # Actualisation instantanée
    else:
        content = f"⚠️ Échec du lancement : {message}"
    await ctx.send(content)

    # Transfert dans le salon de logs, avec fil de suivi si le lancement a réussi.
    await log_forwarding.forward_action_message(
        bot, content=content, script_name=script, create_thread=success,
    )


@start.autocomplete("script")
async def start_script_autocomplete(interaction: discord.Interaction, current: str):
    authorized, _perms = await auth_check.check_discord_authorized(interaction.user.id)
    if not authorized:
        return []
    scripts = await api_client.get_scripts()
    if not scripts:
        return []
    current_lower = current.lower()
    choices = []
    for s in scripts:
        name = s["filename"]
        if current_lower and current_lower not in name.lower():
            continue
        emoji = "🟢" if s.get("is_active") else "⚪"
        choices.append(discord.app_commands.Choice(name=f"{emoji} {name}"[:100], value=name))
    return choices[:25]


@bot.hybrid_command(
    name="stop",
    description="Arrête le script actuellement actif",
)
async def stop_command(ctx: commands.Context):
    await _delete_invoking_message(ctx)
    await ctx.defer()

    authorized, perms = await auth_check.check_discord_authorized(ctx.author.id)
    if not authorized:
        await _deny(ctx, auth_check.denial_message(perms))
        return

    status = await api_client.get_status()
    if status is None:
        await _deny(ctx, "⚠️ Impossible de contacter le dashboard Flask.")
        return
    if not status.get("running"):
        await _deny(ctx, "ℹ️ Aucun script n'est actuellement actif.")
        return

    if not await _consume_cooldown(ctx):
        return

    script_name = status.get("script_name") or "Inactif"
    success, message = await api_client.stop_script()
    if success:
        content = f"🛑 **{ctx.author.display_name}** a arrêté **{script_name}**."
        await bot.update_presence()  # Actualisation instantanée
    else:
        content = f"⚠️ Échec de l'arrêt : {message}"
    await ctx.send(content)

    # Transfert dans le salon de logs (pas de fil pour un Arrêt).
    await log_forwarding.forward_action_message(
        bot, content=content, script_name=script_name, create_thread=False,
    )


@bot.hybrid_command(
    name="queue",
    description="Affiche et gère la file d'attente de scripts (ajout, retrait, admin)",
)
async def queue_command(ctx: commands.Context):
    await _delete_invoking_message(ctx)
    await ctx.defer()

    authorized, perms = await auth_check.check_discord_authorized(ctx.author.id)
    if not authorized:
        await _deny(ctx, auth_check.denial_message(perms))
        return

    status = await api_client.get_status()
    embed = queue_manager.build_queue_embed(status)
    view = QueueView()
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="ping",
    description="Vérifie que le bot et le dashboard Flask répondent",
)
async def ping(ctx: commands.Context):
    await _delete_invoking_message(ctx)
    await ctx.defer()

    discord_latency_ms = round(bot.latency * 1000)

    start = time.monotonic()
    status = await api_client.get_status()
    flask_latency_ms = round((time.monotonic() - start) * 1000)

    if status is None:
        flask_line = "🔴 Injoignable"
        color = discord.Color.red()
    else:
        flask_line = f"🟢 OK ({flask_latency_ms} ms)"
        color = discord.Color.green()

    embed = discord.Embed(title="🏓 Pong !", color=color)
    embed.add_field(name="Bot Discord", value=f"🟢 {discord_latency_ms} ms", inline=False)
    embed.add_field(name="Dashboard Flask", value=flask_line, inline=False)
    await ctx.send(embed=embed)


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)