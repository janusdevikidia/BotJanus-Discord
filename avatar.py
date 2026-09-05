import argparse
import asyncio
import pathlib
import discord
from config import DISCORD_BOT_TOKEN

# Configuration de la commande en ligne de terminal
parser = argparse.ArgumentParser(description="Changer l'avatar du bot Discord")
parser.add_argument(
    "-i", "--image", 
    type=str, 
    required=True, 
    help="Nom ou chemin de l'image (ex: image.jpg, avatar.png)"
)
args = parser.parse_args()

# Gestion du chemin de l'image
image_path = pathlib.Path(args.image)
if not image_path.is_absolute():
    image_path = pathlib.Path(__file__).parent / image_path

client = discord.Client(intents=discord.Intents.default())

@client.event
async def on_ready():
    print(f"Connecté en tant que {client.user}")

    if not image_path.exists():
        print(f"Erreur : Impossible de trouver le fichier '{image_path}'")
        await client.close()
        return

    try:
        with open(image_path, "rb") as image_file:
            await client.user.edit(avatar=image_file.read())
            print(f"Avatar mis à jour avec succès avec '{image_path.name}' !")
    except Exception as e:
        print(f"Erreur lors de la mise à jour : {e}")
    finally:
        await client.close()

client.run(DISCORD_BOT_TOKEN)
