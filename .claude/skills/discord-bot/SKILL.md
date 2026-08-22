---
name: discord-bot
description: >-
  Bot Discord avec discord.py : intents, slash commands, embeds, envoi dans un
  salon par ID, permissions minimales, rate limits, gestion d'erreurs et
  architecture asynchrone. Utiliser ce skill dès qu'on travaille sur
  app/discord/ (bot, commandes, embeds) ou app/services/discord_service.py.
---

# Bot Discord (discord.py)

## Rôle dans ce projet

Le bot **affiche** : embeds de signaux, statut, statistiques. Il ne prend
aucune décision de trading et n'exécute rien. Il tourne **dans le même
process** que FastAPI (lancé dans le lifespan, voir skill `fastapi`).

## Structure

```text
app/discord/bot.py       classe du bot (commands.Bot), connexion, sync des commandes
app/discord/commands.py  slash commands (/status, /lastsignal, /signals, /stats, /strategy)
app/discord/embeds.py    construction des embeds (signaux, stats)
app/services/discord_service.py  interface envoi utilisée par le Signal Engine
```

`discord_service` encapsule totalement discord.py : le Signal Engine ne
manipule **jamais** d'objets Discord directement. C'est aussi ce qui permet
de le mocker dans les tests.

## Connexion et intents

- discord.py 2.x : `discord.Client` / `commands.Bot` avec intents explicites.
- **Intents minimaux** : `guilds` et `messages` si nécessaire. Pas besoin de
  `presences`, pas de `members` (privileged) pour ce projet.
- Connexion avec `bot.start(TOKEN)` dans une tâche asyncio ; à l'arrêt,
  `await bot.close()`.
- Synchronisation des commandes slash : `await bot.tree.sync()` (au démarrage,
  éventuellement limité à la guild `DISCORD_GUILD_ID` pour un reflet
  immédiat en développement).
- Le token vient **uniquement** de `.env` (`DISCORD_BOT_TOKEN`), jamais dans
  le code ni les logs.

## Permissions

Inviter le bot avec les permissions minimales :

```text
View Channels
Send Messages
Embed Links
Read Message History
```

**Jamais ADMINISTRATOR** sans raison impérative.

## Envoi des signaux

- Salon cible par ID : `DISCORD_SIGNALS_CHANNEL_ID`, salon de logs techniques
  optionnel : `DISCORD_LOGS_CHANNEL_ID`.
- Récupérer le salon et envoyer :

```python
channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
message = await channel.send(embed=embed)
```

- **Toujours stocker `discord_message_id`** après envoi (traçabilité du
  statut `SENT`).
- L'envoi ne doit jamais lever une exception non gérée vers le pipeline :
  échec => statut `ERROR` en base + log (voir skill `signal-engine`).

## Embeds de signaux

Lisible sur mobile, champs nommés, couleurs et titres normalisés :

```text
🟢 LONG SIGNAL  /  🔴 SHORT SIGNAL   (vert / rouge)
Symbole : BTCUSDT
Strategy : Momentum V1
Timeframe : 15m
Entry : 104532.42
Stop Loss : 103800.00
Take Profit : 106000.00
Risk/Reward : 1:2
Signal Time : 22:14:03 UTC
```

- Prix formatés de façon lisible (séparateurs, pas 10 décimales).
- Fuseau horaire affiché explicitement (UTC).
- Pas de mention `@everyone`, pas de ping sur les signaux.

## Slash commands prévues

| Commande     | Contenu                                                     |
|--------------|-------------------------------------------------------------|
| `/status`    | Bot / Database / Webhook / Environment (réponses privées ou ephemeral) |
| `/lastsignal`| Dernier signal (embed)                                      |
| `/signals`   | Derniers signaux (liste limitée, ex. 10, éventuellement filtrable) |
| `/stats`     | Statistiques de performance (voir skill `backtesting` : win rate, avg R, total R, drawdown — avec les réserves d'usage) |
| `/strategy`  | Stratégies actives (nom, version, enabled)                  |

Règles :

- Réponses `ephemeral=True` quand c'est du "back-office" (status, stats).
- Toute commande qui interroge la DB passe par le **repository** (voir skill
  `postgresql`), jamais de SQL inline dans les commandes.
- Chaque commande a un handler d'erreurs (voir ci-dessous).

## Rate limits et erreurs

- discord.py gère les rate limits automatiquement pour les requêtes
  standards ; ne pas spammer d'envois dans des boucles serrées.
- `CommandTree.error` : handler global — log + message utilisateur sobre
  ("Une erreur est survenue") ; jamais de stacktrace en chat public.
- `on_error` / tâches de fond : toute exception doit être loggée, pas
  avalée.
- En cas de Discord indisponible au moment d'un signal : le signal reste en
  base (`ERROR`), retente plus tard ou notifiera via les logs.

## Architecture asynchrone

- Tout est `async` ; aucune opération bloquante (les requêtes DB passent par
  SQLAlchemy async, voir skill `postgresql`).
- Un seul loop (celui de FastAPI/Uvicorn) : ne pas créer de threads pour
  Discord, utiliser `asyncio.create_task` depuis le lifespan.
- Événements utiles : `setup_hook` (init async), `on_ready` (log "connected").

## Critères de validation

- [ ] Intent et permissions minimaux, pas d'ADMINISTRATOR.
- [ ] Token uniquement via variable d'environnement, jamais loggé.
- [ ] Le Signal Engine ne dépend d'aucun objet discord.py (service injecté).
- [ ] `discord_message_id` stocké après chaque envoi réussi.
- [ ] Embeds conformes au format (titres, champs, UTC, lisible mobile).
- [ ] Handler d'erreurs global sur les commandes ; échecs loggés.
