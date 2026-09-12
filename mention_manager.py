from __future__ import annotations

import discord

import database as db


LEVEL_EMOJIS = {
    "mean": "😠",
    "normal": "😐",
    "nice": "😊",
}


def build_mention_embed() -> discord.Embed:
    payload = db.get_all_mention_messages()
    overrides = db.get_mention_user_overrides()

    embed = discord.Embed(title="🗣️ Gestion des réponses de mention", color=discord.Color.blurple())
    for level in db.get_mention_levels():
        messages = payload.get(level, [])
        preview = "\n".join(f"• {m}" for m in messages[:5]) if messages else "Aucun message défini."
        embed.add_field(
            name=f"{LEVEL_EMOJIS.get(level, '▪')} {level.title()}",
            value=preview[:1024],
            inline=False,
        )

    if overrides:
        override_lines = [
            f"<@{row['discord_user_id']}> → {row['level']}" for row in overrides[:10]
        ]
        embed.add_field(
            name="Overrides utilisateurs",
            value="\n".join(override_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="Overrides utilisateurs",
            value="Aucun utilisateur forcé sur un niveau spécifique.",
            inline=False,
        )

    return embed


class MentionManagerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(MentionLevelSelect())
        self.add_item(MentionAddButton())
        self.add_item(MentionRemoveButton())
        self.add_item(MentionOverrideUserButton())
        self.add_item(MentionDeleteButton())


class MentionLevelSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=f"{LEVEL_EMOJIS[level]} {level.title()}", value=level)
            for level in db.get_mention_levels()
        ]
        super().__init__(placeholder="Choisir un niveau…", options=options, custom_id="mention_level_select")

    async def callback(self, interaction: discord.Interaction):
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return

        level = self.values[0]
        await interaction.response.send_modal(MentionAddModal(level=level))


class MentionAddButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="➕ Ajouter un message", style=discord.ButtonStyle.success, custom_id="mention_add")

    async def callback(self, interaction: discord.Interaction):
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return
        await interaction.response.send_modal(MentionAddModal())


class MentionRemoveButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🗑 Retirer un message", style=discord.ButtonStyle.danger, custom_id="mention_remove")

    async def callback(self, interaction: discord.Interaction):
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return
        await interaction.response.send_modal(MentionRemoveModal())


class MentionOverrideUserButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="👤 Forcer un niveau", style=discord.ButtonStyle.primary, custom_id="mention_override_user")

    async def callback(self, interaction: discord.Interaction):
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return
        await interaction.response.send_modal(MentionOverrideModal())


class MentionDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✖", style=discord.ButtonStyle.secondary, custom_id="mention_delete")

    async def callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


class MentionAddModal(discord.ui.Modal, title="Ajouter un message de mention"):
    def __init__(self, level: str | None = None):
        super().__init__()
        self.level = level
        self.level_input = discord.ui.TextInput(
            label="Niveau",
            placeholder="mean / normal / nice",
            default=level or "normal",
            required=True,
            max_length=20,
        )
        self.message_input = discord.ui.TextInput(
            label="Message à ajouter",
            placeholder="Ex: Tu peux pas venir me parler comme ça",
            style=discord.TextStyle.long,
            required=True,
            max_length=500,
        )
        self.add_item(self.level_input)
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return

        level = self.level_input.value.strip().lower()
        if level not in db.get_mention_levels():
            await interaction.response.send_message("⚠️ Niveau invalide. Utilise `mean`, `normal` ou `nice`.", ephemeral=True)
            return

        text = self.message_input.value.strip()
        if not text:
            await interaction.response.send_message("⚠️ Le message ne peut pas être vide.", ephemeral=True)
            return

        db.add_mention_message(level, text)
        await interaction.response.send_message(f"✅ Message ajouté pour le niveau `{level}`.", ephemeral=True)


class MentionRemoveModal(discord.ui.Modal, title="Retirer un message de mention"):
    level_input = discord.ui.TextInput(label="Niveau", placeholder="mean / normal / nice", required=True, max_length=20)
    message_input = discord.ui.TextInput(
        label="Message exact à retirer",
        style=discord.TextStyle.long,
        required=True,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return

        level = self.level_input.value.strip().lower()
        if level not in db.get_mention_levels():
            await interaction.response.send_message("⚠️ Niveau invalide. Utilise `mean`, `normal` ou `nice`.", ephemeral=True)
            return

        removed = db.remove_mention_message(level, self.message_input.value)
        if removed:
            await interaction.response.send_message("✅ Message retiré.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Aucun message correspondant trouvé.", ephemeral=True)


class MentionOverrideModal(discord.ui.Modal, title="Forcer un niveau pour un membre"):
    user_input = discord.ui.TextInput(label="Mention du membre", placeholder="@pseudo", required=True)
    level_input = discord.ui.TextInput(label="Niveau", placeholder="mean / normal / nice", required=True, max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return

        user_value = self.user_input.value.strip()
        try:
            user_id = int(user_value.replace("<@", "").replace(">", "").replace("!", ""))
        except ValueError:
            await interaction.response.send_message("⚠️ Mention invalide. Utilise @pseudo ou un ID Discord.", ephemeral=True)
            return

        level = self.level_input.value.strip().lower()
        if level not in db.get_mention_levels():
            await interaction.response.send_message("⚠️ Niveau invalide. Utilise `mean`, `normal` ou `nice`.", ephemeral=True)
            return

        db.set_mention_user_level(user_id, level)
        await interaction.response.send_message(f"✅ <@{user_id}> est désormais en niveau `{level}`.", ephemeral=True)
