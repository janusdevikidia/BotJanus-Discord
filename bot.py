import asyncio
import logging

import discord
from discord.ext import commands

from config import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, COMMAND_PREFIX, OWNER_DISCORD_ID
import database as db
import api_client
from views import DashboardView, build_status_embed, AdminView, build_admin_embed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("botjanus_discord")

intents = discord.Intents.default()
intents.message_content = True  # requis pour lire !dashboard / !admin

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)


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


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)