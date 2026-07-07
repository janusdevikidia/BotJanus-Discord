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
)
import database as db
import api_client
from views import DashboardView, build_status_embed, AdminView, build_admin_embed

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


@bot.hybrid_command(
    name="dashboard",
    description="Affiche l'état du robot Vikidia et permet de le lancer/arrêter",
)
async def dashboard(ctx: commands.Context):
    await _delete_invoking_message(ctx)
    # Ack immédiat : l'appel vers PythonAnywhere peut dépasser les 3s
    # tolérées par Discord avant qu'il considère l'interaction comme expirée.
    await ctx.defer()

    if not db.is_authorized(ctx.author.id):
        await _deny(
            ctx,
            "🚫 Tu n'es pas autorisé à utiliser cette commande. "
            "Demande à l'administrateur de t'ajouter à la liste blanche via `/admin`.",
        )
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