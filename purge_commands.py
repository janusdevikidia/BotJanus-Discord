import asyncio

import discord
from discord.ext import commands

from config import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}. Purge en cours…")

    # Purge globale (au cas où des commandes auraient été synchronisées globalement).
    bot.tree.clear_commands(guild=None)
    synced_global = await bot.tree.sync()
    print(f"Commandes globales après purge : {len(synced_global)} (devrait être 0).")

    # Purge sur le serveur configuré, si renseigné.
    if DISCORD_GUILD_ID:
        guild = discord.Object(id=DISCORD_GUILD_ID)
        bot.tree.clear_commands(guild=guild)
        synced_guild = await bot.tree.sync(guild=guild)
        print(f"Commandes de guilde après purge : {len(synced_guild)} (devrait être 0).")

    print("Purge terminée. Relance maintenant bot.py normalement pour resynchroniser proprement.")
    await bot.close()


asyncio.run(bot.start(DISCORD_BOT_TOKEN))
