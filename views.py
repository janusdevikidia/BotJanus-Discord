from __future__ import annotations

from datetime import datetime, timezone

import discord

import database as db
import api_client
import auth_check
import cooldown
import log_forwarding
import queue_manager
from config import PORTAL_SCRIPT_NAME, LOG_CHANNEL_ID


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

    queue_len = db.count_queue()
    if queue_len:
        embed.add_field(
            name="File d'attente", value=f"{queue_len} tâche(s) en attente (voir `/queue`)", inline=False
        )
    return embed


class BaseAuthorizedView(discord.ui.View):
    """Vue de base qui restreint les clics aux utilisateurs autorisés (compte Discord lié
    à un compte Vikidia avec un rôle suffisant, ou administrateur du robot)."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        authorized, perms = await auth_check.check_discord_authorized(interaction.user.id)
        if not authorized:
            await interaction.response.send_message(auth_check.denial_message(perms), ephemeral=True)
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
            self.add_item(QueueOpenButton())
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

        # Transfert dans le salon de logs (pas de fil pour un Arrêt).
        await log_forwarding.forward_action_message(
            interaction.client, content=content, script_name=script_name, create_thread=False,
        )


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


class QueueOpenButton(discord.ui.Button):
    """Bouton affiché quand un script tourne déjà : ouvre la carte publique de la
    file d'attente (/queue), visible et utilisable par tout le monde comme le dashboard."""

    def __init__(self):
        super().__init__(label="📑 File d'attente", style=discord.ButtonStyle.primary, custom_id="dashboard_queue")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        status = await api_client.get_status()
        embed = queue_manager.build_queue_embed(status)
        view = QueueView()
        await interaction.followup.send(embed=embed, view=view)


class ScriptSelectView(BaseAuthorizedView):
    def __init__(self, scripts: list[dict], mode: str = "launch", origin_message: discord.Message | None = None):
        super().__init__(timeout=120)
        self.add_item(ScriptSelect(scripts, mode, origin_message))
        self.add_item(CancelButton())


class ScriptSelect(discord.ui.Select):
    def __init__(
        self,
        scripts: list[dict],
        mode: str = "launch",
        origin_message: discord.Message | None = None,
    ):
        self.mode = mode  # "launch" ou "queue"
        # En mode "queue" : message de la carte publique /queue à rafraîchir après ajout.
        self.origin_message = origin_message
        self.active_map = {s["filename"]: s["is_active"] for s in scripts}
        options = [
            discord.SelectOption(
                label=s["filename"],
                description="Actif sur le site" if s["is_active"] else "Désactivé sur le site web",
                emoji="🟢" if s["is_active"] else "⚪",
            )
            for s in scripts[:25]
        ]
        placeholder = (
            "Choisis le script à ajouter à la file…" if mode == "queue" else "Choisis le script à lancer…"
        )
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]

        # Un script désactivé sur le site web ne peut être lancé que par l'admin.
        if not self.active_map.get(choice, True) and interaction.user.id != _owner_id():
            await interaction.response.send_message(
                "🚫 Ce script est désactivé sur le dashboard web. Seul l'administrateur peut le lancer.",
                ephemeral=True,
            )
            return

        if self.mode == "queue":
            if db.get_queue_disabled():
                await interaction.response.edit_message(
                    content="🔒 La file d'attente est désactivée par l'administrateur.", view=None
                )
                return
            if choice == PORTAL_SCRIPT_NAME:
                await interaction.response.send_modal(
                    PortalModal(choice, None, queue_mode=True, queue_card_message=self.origin_message)
                )
                return
            position = db.count_queue() + 1
            db.add_to_queue(
                script_name=choice,
                discord_user_id=interaction.user.id,
                username=interaction.user.display_name,
                extra_params=None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            await interaction.response.edit_message(
                content=f"➕ **{choice}** ajouté à la file d'attente (position {position}).",
                view=None,
            )
            await _refresh_message_if_possible(self.origin_message)
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

    def __init__(
        self,
        choice: str,
        original_message: discord.Message | None,
        queue_mode: bool = False,
        queue_card_message: discord.Message | None = None,
    ):
        super().__init__()
        self.choice = choice
        self.original_message = original_message
        self.queue_mode = queue_mode
        # Carte publique /queue à rafraîchir après ajout (mode file d'attente uniquement).
        self.queue_card_message = queue_card_message

    async def on_submit(self, interaction: discord.Interaction):
        extra = {
            "arg_lang": self.arg_lang.value,
            "arg_cat": self.arg_cat.value,
            "arg_portal": self.arg_portal.value,
        }

        # Mode explicite "ajouter à la file" (choisi depuis le bouton File d'attente).
        if self.queue_mode:
            if db.get_queue_disabled():
                await interaction.response.send_message(
                    "🔒 La file d'attente est désactivée par l'administrateur.", ephemeral=True
                )
                return
            position = db.count_queue() + 1
            db.add_to_queue(
                script_name=self.choice,
                discord_user_id=interaction.user.id,
                username=interaction.user.display_name,
                extra_params=extra,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            await interaction.response.send_message(
                f"➕ **{self.choice}** ajouté à la file d'attente (position {position}).", ephemeral=True
            )
            await _refresh_message_if_possible(self.queue_card_message)
            return

        # Mode lancement direct, mais un script a pu démarrer entre-temps (le
        # formulaire prend du temps à remplir) : on bascule alors sur la file.
        status = await api_client.get_status()
        if status and status.get("running"):
            position = db.count_queue() + 1
            db.add_to_queue(
                script_name=self.choice,
                discord_user_id=interaction.user.id,
                username=interaction.user.display_name,
                extra_params=extra,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            content = (
                f"⚠️ Un script est déjà actif (**{status.get('script_name')}**) : **{self.choice}** "
                f"a été ajouté à la file d'attente (position {position})."
            )
            if self.original_message is not None:
                await self.original_message.edit(content=content, embed=None, view=None)
            else:
                await interaction.response.send_message(content, ephemeral=True)
            return

        if not await _consume_cooldown(interaction):
            return

        await interaction.response.defer()
        success, message = await api_client.start_script(
            self.choice, username=interaction.user.display_name, extra=extra
        )
        if success:
            content = f"✅ **{interaction.user.display_name}** a lancé **{self.choice}**."
            await interaction.client.update_presence()  # Actualisation instantanée
        else:
            content = f"⚠️ Échec du lancement : {message}"

        if self.original_message is not None:
            await self.original_message.edit(content=content, embed=None, view=None)
        else:
            await interaction.followup.send(content)

        # Transfert dans le salon de logs, avec fil de suivi si le lancement a réussi.
        await log_forwarding.forward_action_message(
            interaction.client, content=content, script_name=self.choice, create_thread=success,
        )


async def _launch_and_report(interaction: discord.Interaction, choice: str):
    success, message = await api_client.start_script(choice, username=interaction.user.display_name)
    if success:
        content = f"✅ **{interaction.user.display_name}** a lancé **{choice}**."
        await interaction.client.update_presence()  # Actualisation instantanée
    else:
        content = f"⚠️ Échec du lancement : {message}"
    await interaction.edit_original_response(content=content, embed=None, view=None)

    # Transfert dans le salon de logs, avec fil de suivi si le lancement a réussi.
    await log_forwarding.forward_action_message(
        interaction.client, content=content, script_name=choice, create_thread=success,
    )


class LogsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="📋 Logs", style=discord.ButtonStyle.secondary, custom_id="dashboard_logs")

    async def callback(self, interaction: discord.Interaction):
        status = await api_client.get_status()
        running = bool(status and status.get("running"))
        warning = "" if status is not None else "⚠️ Impossible de contacter le dashboard Flask (statut inconnu).\n"

        thread_entry = db.get_latest_log_thread() if running else None

        if thread_entry:
            content = f"{warning}📡 Fil de logs en cours : <#{thread_entry['thread_id']}>"
        elif LOG_CHANNEL_ID:
            content = f"{warning}📋 Aucun script actif pour le moment. Historique des logs : <#{LOG_CHANNEL_ID}>"
        else:
            content = f"{warning}⚠️ Aucun salon de logs n'est configuré (variable LOG_CHANNEL_ID manquante)."

        await interaction.response.send_message(content, ephemeral=True)


class DeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✖", style=discord.ButtonStyle.secondary, custom_id="dashboard_delete")

    async def callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


def _owner_id() -> int:
    from config import OWNER_DISCORD_ID
    return OWNER_DISCORD_ID


async def _refresh_message_if_possible(message: discord.Message | None) -> None:
    """Rafraîchit la carte publique /queue (embed + boutons) après un ajout ou un
    retrait, si on a une référence vers ce message. Silencieux en cas d'échec
    (message supprimé entre-temps, permissions, etc.)."""
    if message is None:
        return
    try:
        status = await api_client.get_status()
        await message.edit(embed=queue_manager.build_queue_embed(status), view=QueueView())
    except discord.HTTPException:
        pass


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


class QueueAddView(BaseAuthorizedView):
    """Proposée par /start quand le script demandé ne peut pas être lancé
    immédiatement (un autre script tourne déjà) : confirmation avant ajout à la file."""

    def __init__(self, script: str, extra: dict | None, discord_id: int, username: str):
        super().__init__(timeout=120)
        self.script = script
        self.extra = extra
        self.discord_id = discord_id
        self.username = username

    @discord.ui.button(label="➕ Ajouter à la file", style=discord.ButtonStyle.primary, custom_id="queue_add_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message(
                "🚫 Seul l'auteur de la commande peut confirmer l'ajout.", ephemeral=True
            )
            return
        position = db.count_queue() + 1
        db.add_to_queue(
            script_name=self.script,
            discord_user_id=self.discord_id,
            username=self.username,
            extra_params=self.extra,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await interaction.response.edit_message(
            content=f"➕ **{self.script}** ajouté à la file d'attente (position {position}).", view=None
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary, custom_id="queue_add_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message(
                "🚫 Seul l'auteur de la commande peut annuler.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="❌ Ajout à la file annulé.", view=None)


# ==========================================
# /queue — carte unifiée (publique, comme /dashboard)
# ==========================================

class QueueView(BaseAuthorizedView):
    """Carte publique unique pour gérer la file d'attente : ajout, retrait via menu
    déroulant, et actions admin (vider la file / activer-désactiver)."""

    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(QueueAddButton())
        queue = db.get_queue()
        if queue:
            self.add_item(QueueRemoveSelect(queue))
        self.add_item(QueueClearButton())
        self.add_item(QueueToggleButton(db.get_queue_disabled()))
        self.add_item(QueueDeleteButton())


class QueueAddButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="➕ Ajouter une tâche", style=discord.ButtonStyle.success, custom_id="queue_add")

    async def callback(self, interaction: discord.Interaction):
        if db.get_queue_disabled():
            await interaction.response.send_message(
                "🔒 La file d'attente est désactivée par l'administrateur.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        scripts = await api_client.get_scripts()
        if not scripts:
            await interaction.followup.send(
                "⚠️ Impossible de récupérer la liste des scripts depuis le dashboard.", ephemeral=True
            )
            return
        view = ScriptSelectView(scripts, mode="queue", origin_message=interaction.message)
        await interaction.followup.send(
            "Choisis le script à ajouter à la file d'attente :", view=view, ephemeral=True
        )


class QueueRemoveSelect(discord.ui.Select):
    """Menu déroulant listant les tâches en attente : plus besoin de connaître l'id."""

    def __init__(self, queue: list[dict]):
        options = [
            discord.SelectOption(
                label=f"{i}. {item['script_name']}"[:100],
                description=f"Demandé par {item['username']}"[:100],
                value=str(item["id"]),
            )
            for i, item in enumerate(queue, start=1)
        ][:25]
        super().__init__(placeholder="Retirer une tâche de la file…", options=options)

    async def callback(self, interaction: discord.Interaction):
        queue_id = int(self.values[0])
        item = next((i for i in db.get_queue() if i["id"] == queue_id), None)
        if item is None:
            await interaction.response.send_message(
                "⚠️ Cette tâche n'est plus dans la file (déjà retirée ou lancée).", ephemeral=True
            )
            return
        if str(interaction.user.id) != item["discord_user_id"] and interaction.user.id != _owner_id():
            await interaction.response.send_message(
                "🚫 Tu ne peux retirer que tes propres tâches de la file (sauf administrateur).",
                ephemeral=True,
            )
            return

        db.remove_queue_item(queue_id)
        await _refresh_queue_view(interaction)


class QueueClearButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="🗑 Vider la file (admin)", style=discord.ButtonStyle.danger, custom_id="queue_clear"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != _owner_id():
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return
        db.clear_queue()
        await _refresh_queue_view(interaction)


class QueueToggleButton(discord.ui.Button):
    def __init__(self, disabled: bool):
        label = "🔓 Réactiver la file (admin)" if disabled else "🔒 Désactiver la file (admin)"
        style = discord.ButtonStyle.success if disabled else discord.ButtonStyle.danger
        super().__init__(label=label, style=style, custom_id="queue_toggle")

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != _owner_id():
            await interaction.response.send_message("🚫 Réservé à l'administrateur.", ephemeral=True)
            return
        db.set_queue_disabled(not db.get_queue_disabled())
        await _refresh_queue_view(interaction)


class QueueDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✖", style=discord.ButtonStyle.secondary, custom_id="queue_delete")

    async def callback(self, interaction: discord.Interaction):
        await interaction.message.delete()


async def _refresh_queue_view(interaction: discord.Interaction) -> None:
    status = await api_client.get_status()
    embed = queue_manager.build_queue_embed(status)
    view = QueueView()
    await interaction.response.edit_message(embed=embed, view=view)


# ==========================================
# /admin
# ==========================================

def build_admin_embed() -> discord.Embed:
    locked = db.get_lock()

    embed = discord.Embed(title="🛠 Administration BotJanus Discord", color=discord.Color.blurple())
    embed.add_field(
        name="Verrou de lancement",
        value="🔒 Verrouillé (toi seul peux lancer/arrêter)" if locked else "🔓 Déverrouillé",
        inline=False,
    )
    embed.add_field(
        name="Accès aux commandes",
        value=(
            "Géré depuis le dashboard : chacun lie son compte avec `/auth`, puis a besoin "
            "d'un rôle wiki suffisant (autopatrol, patroller, sysop ou bureaucrat) pour "
            "pouvoir lancer/arrêter BotJanus."
        ),
        inline=False,
    )
    return embed


class AdminView(OwnerOnlyView):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(LockToggleButton(db.get_lock()))
        self.add_item(AdminDeleteButton())


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