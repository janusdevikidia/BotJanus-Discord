from __future__ import annotations

import discord

import database as db
import api_client
import cooldown
from config import PORTAL_SCRIPT_NAME


# ==========================================
# OUTILS COMMUNS
# ==========================================

def build_status_embed(status: dict) -> discord.Embed:
    running = status.get("running", False)
    script_name = status.get("script_name") or "Inactif"

    embed = discord.Embed(
        title="📊 Dashboard BotJanus — Vikidia",
        color=discord.Color.green() if running else discord.Color.red(),
    )
    if running:
        embed.add_field(
            name="État", value=f"🟢 En cours d'exécution\n**Script :** `{script_name}`", inline=False
        )
    else:
        embed.add_field(name="État", value="🔴 Arrêté", inline=False)
    return embed


class BaseAuthorizedView(discord.ui.View):
    """Vue de base qui restreint les clics aux utilisateurs autorisés."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not db.is_authorized(interaction.user.id):
            await interaction.response.send_message(
                "🚫 Tu n'es pas autorisé à utiliser ce bouton.", ephemeral=True
            )
            return False
        return True


class OwnerOnlyView(discord.ui.View):
    """Vue de base réservée exclusivement à l'administrateur (owner)."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from config import OWNER_DISCORD_ID
        if interaction.user.id != OWNER_DISCORD_ID:
            await interaction.response.send_message(
                "🚫 Réservé à l'administrateur.", ephemeral=True
            )
            return False
        return True


# ==========================================
# /dashboard
# ==========================================

class DashboardView(BaseAuthorizedView):
    def __init__(self, running: bool, script_name: str | None):
        super().__init__(timeout=300)
        self.script_name = script_name or "Inactif"

        if running:
            self.add_item(StopButton())
        else:
            self.add_item(StartButton())
        self.add_item(LogsButton())
        self.add_item(DeleteButton())


class StopButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⏹ Arrêter", style=discord.ButtonStyle.danger, custom_id="dashboard_stop")

    async def callback(self, interaction: discord.Interaction):
        if not await _consume_cooldown(interaction):
            return

        await interaction.response.defer()
        script_name = self.view.script_name
        success, message = await api_client.stop_script()
        if success:
            content = f"🛑 **{interaction.user.display_name}** a arrêté **{script_name}**."
            await interaction.client.update_presence()  # Actualisation instantanée
        else:
            content = f"⚠️ Échec de l'arrêt : {message}"
        await interaction.edit_original_response(content=content, embed=None, view=None)


class StartButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="▶ Lancer", style=discord.ButtonStyle.success, custom_id="dashboard_start")

    async def callback(self, interaction: discord.Interaction):
        # Le verrou est vérifié en temps réel (pas figé au moment de l'affichage)
        if db.get_lock() and interaction.user.id != _owner_id():
            await interaction.response.send_message(
                "🔒 Le lancement est verrouillé par l'administrateur.", ephemeral=True
            )
            return

        # On defer immédiatement : l'appel réseau vers PythonAnywhere peut
        # prendre plus de 3s (ex: réveil d'une appli en veille), au-delà
        # desquelles Discord considère l'interaction comme expirée.
        await interaction.response.defer()

        scripts = await api_client.get_scripts()
        if scripts is None:
            await interaction.edit_original_response(
                content="⚠️ Impossible de récupérer la liste des scripts depuis le dashboard.",
                embed=None, view=None,
            )
            return
        if not scripts:
            await interaction.edit_original_response(
                content="⚠️ Aucun script disponible sur le dashboard.", embed=None, view=None
            )
            return

        view = ScriptSelectView(scripts)
        await interaction.edit_original_response(content=None, view=view)


class ScriptSelectView(BaseAuthorizedView):
    def __init__(self, scripts: list[dict]):
        super().__init__(timeout=120)
        self.add_item(ScriptSelect(scripts))
        self.add_item(CancelButton())


class ScriptSelect(discord.ui.Select):
    def __init__(self, scripts: list[dict]):
        self.active_map = {s["filename"]: s["is_active"] for s in scripts}
        options = [
            discord.SelectOption(
                label=s["filename"],
                description="Actif sur le site" if s["is_active"] else "Désactivé sur le site web",
                emoji="🟢" if s["is_active"] else "⚪",
            )
            for s in scripts[:25]
        ]
        super().__init__(placeholder="Choisis le script à lancer…", options=options)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]

        # Un script désactivé sur le site web ne peut être lancé que par l'admin.
        if not self.active_map.get(choice, True) and interaction.user.id != _owner_id():
            await interaction.response.send_message(
                "🚫 Ce script est désactivé sur le dashboard web. Seul l'administrateur peut le lancer.",
                ephemeral=True,
            )
            return

        if not await _consume_cooldown(interaction):
            return

        if choice == PORTAL_SCRIPT_NAME:
            await interaction.response.send_modal(PortalModal(choice, interaction.message))
            return

        await interaction.response.defer()
        await _launch_and_report(interaction, choice)


class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Annuler", style=discord.ButtonStyle.secondary, custom_id="dashboard_cancel")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        status = await api_client.get_status()
        if status is None:
            await interaction.edit_original_response(
                content="⚠️ Impossible de recontacter le dashboard.", embed=None, view=None
            )
            return
        embed = build_status_embed(status)
        view = DashboardView(running=status["running"], script_name=status.get("script_name"))
        await interaction.edit_original_response(content=None, embed=embed, view=view)


class PortalModal(discord.ui.Modal, title="Paramètres de portal.py"):
    arg_lang = discord.ui.TextInput(label="Langue", placeholder="fr", required=True, max_length=10)
    arg_cat = discord.ui.TextInput(label="Catégorie", placeholder="Nom de la catégorie", required=True)
    arg_portal = discord.ui.TextInput(label="Portail", placeholder="Nom du portail", required=True)

    def __init__(self, choice: str, original_message: discord.Message):
        super().__init__()
        self.choice = choice
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction):
        if not await _consume_cooldown(interaction):
            return

        await interaction.response.defer()
        extra = {
            "arg_lang": self.arg_lang.value,
            "arg_cat": self.arg_cat.value,
            "arg_portal": self.arg_portal.value,
        }
        success, message = await api_client.start_script(
            self.choice, username=interaction.user.display_name, extra=extra
        )
        if success:
            content = f"✅ **{interaction.user.display_name}** a lancé **{self.choice}**."
            await interaction.client.update_presence()  # Actualisation instantanée
        else:
            content = f"⚠️ Échec du lancement : {message}"
        await self.original_message.edit(content=content, embed=None, view=None)


async def _launch_and_report(interaction: discord.Interaction, choice: str):
    success, message = await api_client.start_script(choice, username=interaction.user.display_name)
    if success:
        content = f"✅ **{interaction.user.display_name}** a lancé **{choice}**."
        await interaction.client.update_presence()  # Actualisation instantanée
    else:
        content = f"⚠️ Échec du lancement : {message}"
    await interaction.edit_original_response(content=content, embed=None, view=None)


class LogsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📋 Logs", style=discord.ButtonStyle.secondary, custom_id="dashboard_logs")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        logs = await api_client.get_logs(limit=20)
        if logs is None:
            await interaction.followup.send("⚠️ Impossible de récupérer les logs.", ephemeral=True)
            return
        if not logs:
            await interaction.followup.send("Aucun log disponible pour le moment.", ephemeral=True)
            return

        text = "\n".join(logs)
        # Discord limite un message à 2000 caractères : on garde la fin (le plus récent)
        if len(text) > 1900:
            text = "…\n" + text[-1900:]
        await interaction.followup.send(f"```\n{text}\n```", ephemeral=True)


class DeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✖", style=discord.ButtonStyle.secondary, custom_id="dashboard_delete")

    async def callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


def _owner_id() -> int:
    from config import OWNER_DISCORD_ID
    return OWNER_DISCORD_ID


async def _consume_cooldown(interaction: discord.Interaction) -> bool:
    """Vérifie le cooldown anti-spam avant une action Lancer/Arrêter réelle."""
    remaining = cooldown.check_cooldown()
    if remaining > 0:
        await interaction.response.send_message(
            f"⏳ Merci d'attendre encore {remaining:.0f}s avant de relancer une action (anti-spam).",
            ephemeral=True,
        )
        return False
    cooldown.record_action()
    return True


# ==========================================
# /admin
# ==========================================

def build_admin_embed() -> discord.Embed:
    locked = db.get_lock()
    whitelist = db.get_whitelist()

    embed = discord.Embed(title="🛠 Administration BotJanus Discord", color=discord.Color.blurple())
    embed.add_field(
        name="Verrou de lancement",
        value="🔒 Verrouillé (toi seul peux lancer/arrêter)" if locked else "🔓 Déverrouillé",
        inline=False,
    )
    if whitelist:
        names = "\n".join(f"• {w['username']} (`{w['discord_id']}`)" for w in whitelist)
    else:
        names = "*(liste blanche vide)*"
    embed.add_field(name=f"Liste blanche ({len(whitelist)})", value=names, inline=False)
    return embed


class AdminView(OwnerOnlyView):
    def __init__(self):
        super().__init__(timeout=300)
        whitelist = db.get_whitelist()
        self.add_item(AddWhitelistSelect())
        self.add_item(RemoveWhitelistSelect(whitelist))
        self.add_item(LockToggleButton(db.get_lock()))
        self.add_item(AdminDeleteButton())


class AddWhitelistSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="➕ Ajouter un utilisateur à la liste blanche",
            min_values=1,
            max_values=1,
            custom_id="admin_add_whitelist",
        )

    async def callback(self, interaction: discord.Interaction):
        user = self.values[0]
        db.add_to_whitelist(user.id, str(user))
        await _refresh_admin(interaction)


class RemoveWhitelistSelect(discord.ui.Select):
    def __init__(self, whitelist: list[dict]):
        if whitelist:
            options = [
                discord.SelectOption(label=w["username"], value=w["discord_id"])
                for w in whitelist[:25]
            ]
            disabled = False
        else:
            options = [discord.SelectOption(label="(liste blanche vide)", value="__none__")]
            disabled = True
        super().__init__(
            placeholder="➖ Retirer un utilisateur de la liste blanche",
            options=options,
            disabled=disabled,
            custom_id="admin_remove_whitelist",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] != "__none__":
            db.remove_from_whitelist(self.values[0])
        await _refresh_admin(interaction)


class LockToggleButton(discord.ui.Button):
    def __init__(self, locked: bool):
        label = "🔓 Déverrouiller le lancement" if locked else "🔒 Verrouiller le lancement (sauf moi)"
        style = discord.ButtonStyle.success if locked else discord.ButtonStyle.danger
        super().__init__(label=label, style=style, custom_id="admin_toggle_lock")

    async def callback(self, interaction: discord.Interaction):
        db.set_lock(not db.get_lock())
        await _refresh_admin(interaction)


class AdminDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✖", style=discord.ButtonStyle.secondary, custom_id="admin_delete")

    async def callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


async def _refresh_admin(interaction: discord.Interaction):
    embed = build_admin_embed()
    view = AdminView()
    await interaction.response.edit_message(embed=embed, view=view)