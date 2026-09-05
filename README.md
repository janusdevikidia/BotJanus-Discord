# BotJanus-Discord

**BotJanus-Discord** est un robot Discord qui fait le pont entre le dashboard Flask de BotJanus et votre serveur Discord. Il permet de piloter et de surveiller l'exécution des scripts pour Vikidia directement depuis des commandes et interfaces interactives sur Discord.

---

## Fonctionnalités

* **Contrôle des scripts** : Lancement (`/start`) et arrêt (`/stop`) des scripts hébergés sur le dashboard Flask.
* **File d'attente (`/queue`)** : Prise en charge et enchaînement automatique des exécutions lorsqu'un script est déjà actif.
* **Suivi en direct** : Transmission réactive des logs d'exécution dans un fil de discussion Discord dédié.
* **Sécurité & Authentification** : Contrôle d'accès basé sur la liaison des comptes Discord et Vikidia (`/auth`).
* **Panneau d'administration** : Gestion du verrou de lancement et surveillance du système (`/admin`).

---

## Commandes principales

| Commande | Description |
| :--- | :--- |
| `/auth` | Associe ton compte Discord à ton compte Vikidia pour obtenir les accès. |
| `/dashboard` | Affiche l'état en direct du robot et les boutons de contrôle rapide. |
| `/start [script]` | Lance un script spécifique (ou ouvre un formulaire pour les paramètres). |
| `/stop` | Arrête l'exécution du script en cours. |
| `/queue` | Affiche l'état de la file d'attente, ajoute ou retire un élément de cette dernière. |
| `/admin` | Ouvre le panneau de verrouillage (réservé à l'administrateur). |
| `/ping` | Affiche la latence du bot Discord et la réponse de l'API Flask. |

---

## Configuration

1. Cloner le dépôt et installer les dépendances :
   ```bash
   pip install -r requirements.txt

2. Créer votre fichier `.env` à la racine
   ```env
    DISCORD_BOT_TOKEN=votre_token_discord
    OWNER_DISCORD_ID=votre_id_discord
    FLASK_API_URL=[https://votre-dashboard.pythonanywhere.com](https://votre-dashboard.pythonanywhere.com)
    FLASK_API_KEY=votre_cle_api
    LOG_GUILD_ID=id_serveur_logs
    LOG_CHANNEL_ID=id_salon_logs

3. Lancer le bot en lançant le fichier python `bot.py`

