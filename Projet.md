# Trading Signal Discord Bot

## 1. Objectif du projet

Construire une application de trading assisté qui utilise TradingView comme moteur d'analyse et de génération de signaux, puis transmet ces signaux à un serveur Discord.

Le système doit permettre de :

1. définir une ou plusieurs stratégies dans TradingView avec Pine Script ;
2. recevoir les alertes TradingView via webhook HTTPS ;
3. vérifier et valider chaque signal côté backend ;
4. empêcher les doublons et les signaux invalides ;
5. enregistrer tous les signaux dans une base de données ;
6. envoyer automatiquement les signaux dans Discord sous forme d'Embed ;
7. conserver un historique complet ;
8. calculer des statistiques de performance ;
9. proposer un mode paper trading pour évaluer les stratégies ;
10. permettre de suivre les performances par stratégie, actif et timeframe.

## 2. Règle fondamentale du projet

Le système est un système de **signalisation uniquement**.

Il ne doit jamais :

* se connecter à un broker ;
* se connecter à un exchange ;
* placer un ordre ;
* modifier un ordre ;
* annuler un ordre ;
* gérer une position réelle ;
* utiliser une clé API de trading ;
* effectuer automatiquement un achat ou une vente.

Le seul rôle du système est :

```text
ANALYSE
   ↓
SIGNAL
   ↓
VALIDATION
   ↓
STOCKAGE
   ↓
DISCORD
   ↓
L'utilisateur décide lui-même de prendre ou non le trade.
```

Aucune fonctionnalité future ne doit contourner cette règle.

---

# 3. Architecture générale

Architecture cible :

```text
                    ┌──────────────────────┐
                    │      TRADINGVIEW     │
                    │                      │
                    │    Pine Strategy     │
                    │                      │
                    │ BTC / ETH / GOLD     │
                    │ 1m / 5m / 15m / 1h   │
                    └──────────┬───────────┘
                               │
                               │ HTTPS POST
                               │ Webhook
                               ▼
                    ┌──────────────────────┐
                    │       FASTAPI        │
                    │                      │
                    │ Webhook Receiver     │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   SIGNAL PROCESSOR   │
                    │                      │
                    │ Authentication       │
                    │ Validation           │
                    │ Deduplication        │
                    │ Risk checks          │
                    └──────────┬───────────┘
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
                 ▼                           ▼
       ┌──────────────────┐        ┌──────────────────┐
       │   PostgreSQL     │        │   Discord Bot    │
       │                  │        │                  │
       │ Signals          │        │ Signal Embeds    │
       │ Strategies       │        │ Commands         │
       │ Statistics       │        │ Status           │
       │ Paper trades     │        │ Statistics       │
       └──────────────────┘        └──────────────────┘
```

---

# 4. Technologies obligatoires

## Backend

Utiliser :

```text
Python 3.12+
FastAPI
Uvicorn
Pydantic
SQLAlchemy
Alembic
PostgreSQL
```

## Discord

Utiliser :

```text
discord.py
```

## Déploiement

Utiliser :

```text
Docker
Docker Compose
```

## Tests

Utiliser :

```text
pytest
pytest-asyncio
httpx
```

## Configuration

Utiliser :

```text
python-dotenv
```

ou une solution équivalente basée sur les variables d'environnement.

---

# 5. Structure du projet

Créer cette structure :

```text
trading-discord-bot/
│
├── app/
│   ├── __init__.py
│   ├── main.py
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── tradingview.py
│   │
│   ├── discord/
│   │   ├── __init__.py
│   │   ├── bot.py
│   │   ├── commands.py
│   │   └── embeds.py
│   │
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── schemas.py
│   │   ├── validator.py
│   │   ├── processor.py
│   │   └── deduplication.py
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   ├── models.py
│   │   └── repository.py
│   │
│   ├── paper_trading/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   ├── positions.py
│   │   └── statistics.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── discord_service.py
│   │   └── signal_service.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py
│       └── time.py
│
├── tests/
│   ├── test_webhook.py
│   ├── test_validation.py
│   ├── test_deduplication.py
│   ├── test_database.py
│   ├── test_discord.py
│   └── test_paper_trading.py
│
├── migrations/
│
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

# 6. Phase 0 — Préparation

Avant de programmer, installer :

```text
Python 3.12+
Git
Docker
Docker Compose
VS Code ou IDE équivalent
```

Créer le dépôt Git.

Initialiser :

```bash
git init
```

Créer immédiatement :

```text
.gitignore
.env.example
README.md
```

Le fichier `.env` ne doit jamais être commit.

---

# 7. Phase 1 — Configuration du projet

Créer un système de configuration centralisé.

Variables prévues :

```env
APP_ENV=development

DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=

DISCORD_SIGNALS_CHANNEL_ID=
DISCORD_LOGS_CHANNEL_ID=

TRADINGVIEW_WEBHOOK_SECRET=

DATABASE_URL=

LOG_LEVEL=INFO
```

Ne jamais écrire les valeurs secrètes directement dans le code.

Créer :

```text
app/config/settings.py
```

avec une classe de configuration typée.

---

# 8. Phase 2 — Création de l'application Discord

Créer une application dans le Discord Developer Portal.

Créer le bot.

Récupérer son token.

Le token doit être uniquement stocké dans :

```text
.env
```

Inviter le bot dans le serveur.

Permissions minimales :

```text
View Channels
Send Messages
Embed Links
Read Message History
```

Ne pas utiliser :

```text
Administrator
```

si ce n'est pas nécessaire.

---

# 9. Phase 3 — Premier démarrage du bot Discord

Créer :

```text
app/discord/bot.py
```

Le bot doit :

1. démarrer ;
2. se connecter à Discord ;
3. afficher dans les logs qu'il est connecté ;
4. synchroniser les commandes slash ;
5. répondre à `/status`.

Commande :

```text
/status
```

Réponse :

```text
🟢 Bot opérationnel

Environment: development
Database: connected
Discord: connected
```

## Critère de validation

La commande `/status` doit fonctionner depuis Discord.

Ne pas passer à la phase suivante avant que ce soit fonctionnel.

---

# 10. Phase 4 — FastAPI

Créer l'application FastAPI.

Créer un endpoint de test :

```text
GET /health
```

Réponse :

```json
{
  "status": "ok"
}
```

Le serveur doit fonctionner localement.

Exemple :

```text
http://localhost:8000/health
```

## Critère de validation

Le endpoint doit répondre correctement.

---

# 11. Phase 5 — Webhook TradingView

Créer :

```text
POST /webhook/tradingview
```

Ce endpoint recevra les alertes TradingView.

Format attendu :

```json
{
  "secret": "SECRET",
  "strategy": "momentum_v1",
  "symbol": "BTCUSDT",
  "exchange": "BINANCE",
  "timeframe": "15",
  "action": "BUY",
  "price": 104532.42,
  "stop_loss": 103800.00,
  "take_profit": 106000.00,
  "timestamp": "2026-08-22T20:00:00Z"
}
```

---

# 12. Phase 6 — Schéma Pydantic

Créer un modèle Pydantic :

```text
TradingViewSignal
```

Champs :

```text
secret
strategy
symbol
exchange
timeframe
action
price
stop_loss
take_profit
timestamp
```

Règles :

```text
action ∈ BUY, SELL
price > 0
stop_loss > 0
take_profit > 0
strategy non vide
symbol non vide
timeframe non vide
timestamp valide
```

---

# 13. Phase 7 — Authentification

Le webhook doit vérifier le secret.

Si le secret est incorrect :

```text
HTTP 401
```

ou une réponse équivalente appropriée.

Ne jamais écrire le secret dans les logs.

Ne jamais retourner le secret dans les erreurs.

Prévoir une solution permettant de remplacer le secret sans modifier le code.

---

# 14. Phase 8 — Validation métier

Créer :

```text
app/signals/validator.py
```

Vérifier :

```text
Stratégie autorisée
Symbole autorisé
Exchange autorisé
Timeframe autorisé
Action valide
Prix valide
Stop loss valide
Take profit valide
Timestamp suffisamment récent
```

Exemple de liste blanche :

```text
BTCUSDT
ETHUSDT
XAUUSD
```

Timeframes :

```text
5
15
30
60
240
D
```

Ces listes doivent être configurables.

---

# 15. Phase 9 — Validation de cohérence

Pour un BUY :

```text
stop_loss < entry_price
take_profit > entry_price
```

Pour un SELL :

```text
stop_loss > entry_price
take_profit < entry_price
```

Si la configuration est incohérente :

```text
signal rejeté
```

Le rejet doit être enregistré dans les logs.

---

# 16. Phase 10 — Déduplication

Chaque signal doit avoir un identifiant unique.

Construire un identifiant basé sur :

```text
strategy
symbol
timeframe
timestamp de bougie
action
```

Exemple :

```text
momentum_v1:BTCUSDT:15:1755892800:BUY
```

Créer une contrainte unique dans PostgreSQL.

Si TradingView envoie deux fois le même signal :

```text
premier → accepté
second → ignoré
```

Il ne doit jamais générer deux messages Discord.

---

# 17. Phase 11 — PostgreSQL

Utiliser PostgreSQL.

Créer au minimum les tables suivantes :

```text
strategies
signals
paper_positions
paper_trades
```

---

# 18. Table strategies

Champs recommandés :

```text
id
name
version
description
enabled
created_at
updated_at
```

Exemple :

```text
momentum_v1
trend_following_v2
gold_breakout_v1
```

---

# 19. Table signals

Champs recommandés :

```text
id
signal_uid
strategy_id
symbol
exchange
timeframe
action
entry_price
stop_loss
take_profit
risk_reward
signal_timestamp
received_at
status
discord_message_id
created_at
```

Statuts possibles :

```text
RECEIVED
VALIDATED
SENT
REJECTED
DUPLICATE
ERROR
```

---

# 20. Calcul du Risk/Reward

Le backend peut calculer automatiquement :

Pour BUY :

```text
risk = entry - stop_loss
reward = take_profit - entry
RR = reward / risk
```

Pour SELL :

```text
risk = stop_loss - entry
reward = entry - take_profit
RR = reward / risk
```

Exemple :

```text
Entry = 100
SL = 98
TP = 104

Risk = 2
Reward = 4

RR = 2.0
```

Afficher :

```text
Risk/Reward: 1:2
```

---

# 21. Phase 12 — Envoi Discord

Créer un service :

```text
app/services/discord_service.py
```

Lorsqu'un signal valide arrive :

```text
TradingView
    ↓
FastAPI
    ↓
Validation
    ↓
Database
    ↓
Discord
```

Le bot doit publier un Embed.

---

# 22. Format Discord

Pour un BUY :

```text
🟢 LONG SIGNAL

BTCUSDT

Strategy:
Momentum V1

Timeframe:
15m

Entry:
104532.42

Stop Loss:
103800.00

Take Profit:
106000.00

Risk/Reward:
1:2

Signal Time:
22:14:03
```

Pour un SELL :

```text
🔴 SHORT SIGNAL

BTCUSDT

Strategy:
Momentum V1

Timeframe:
15m

Entry:
104532.42

Stop Loss:
105200.00

Take Profit:
103200.00

Risk/Reward:
1:2

Signal Time:
22:14:03
```

Le message doit être lisible sur téléphone.

---

# 23. Phase 13 — Tester le pipeline complet

Avant TradingView, utiliser un JSON manuel.

Tester :

```text
POST /webhook/tradingview
```

avec un signal fictif.

Le résultat attendu :

```text
JSON reçu
    ↓
validation
    ↓
PostgreSQL
    ↓
Discord
```

Le signal doit apparaître dans Discord.

---

# 24. Phase 14 — Pine Script

Créer la première stratégie TradingView.

Utiliser Pine Script version actuelle supportée par TradingView.

La première stratégie doit rester volontairement simple.

Exemple conceptuel :

```text
EMA 50
EMA 200
RSI 14
MACD
```

BUY lorsque :

```text
EMA50 > EMA200
RSI > seuil
MACD bullish
```

SELL lorsque :

```text
EMA50 < EMA200
RSI < seuil
MACD bearish
```

La stratégie doit être configurable.

---

# 25. Phase 15 — Éviter le repainting

La stratégie doit être conçue pour éviter :

```text
future leak
lookahead
repainting
```

Pour les premiers tests, privilégier les signaux confirmés à la clôture de la bougie.

Ne pas considérer comme valide une stratégie uniquement parce que son graphique historique semble excellent.

---

# 26. Phase 16 — Alertes TradingView

Créer une alerte TradingView utilisant le webhook.

URL :

```text
https://DOMAIN/webhook/tradingview
```

Le message doit être un JSON valide.

Exemple :

```json
{
  "secret": "YOUR_SECRET",
  "strategy": "momentum_v1",
  "symbol": "{{ticker}}",
  "exchange": "{{exchange}}",
  "timeframe": "{{interval}}",
  "action": "BUY",
  "price": "{{close}}",
  "timestamp": "{{timenow}}"
}
```

Le système doit ensuite calculer ou recevoir les informations nécessaires concernant SL/TP selon la conception de la stratégie.

---

# 27. Phase 17 — Connexion TradingView réelle

Tester :

```text
TradingView
    ↓
Internet
    ↓
HTTPS
    ↓
FastAPI
    ↓
PostgreSQL
    ↓
Discord
```

Ne pas utiliser de tunnel temporaire en production.

Utiliser un véritable domaine HTTPS.

---

# 28. Phase 18 — Gestion des erreurs

Le système doit gérer :

```text
TradingView inaccessible
Webhook invalide
Secret invalide
JSON invalide
PostgreSQL indisponible
Discord indisponible
Signal doublon
Signal expiré
Configuration invalide
```

Chaque erreur doit être loggée.

Les logs ne doivent jamais contenir :

```text
Discord token
Webhook secret
Database password
Autres secrets
```

---

# 29. Phase 19 — Logs

Format recommandé :

```text
2026-08-22 22:14:03 INFO TradingView webhook received
2026-08-22 22:14:03 INFO Signal validated
2026-08-22 22:14:03 INFO Signal stored
2026-08-22 22:14:04 INFO Discord notification sent
```

Pour un rejet :

```text
2026-08-22 22:15:01 WARNING Signal rejected
reason=invalid_stop_loss
```

Utiliser des logs structurés si possible.

---

# 30. Phase 20 — Commandes Discord

Implémenter :

## /status

Afficher :

```text
Bot: ONLINE
Database: ONLINE
Webhook: ONLINE
Environment: PRODUCTION
```

## /lastsignal

Afficher le dernier signal.

## /signals

Afficher les derniers signaux.

## /stats

Afficher les statistiques disponibles.

## /strategy

Afficher les stratégies actives.

---

# 31. Phase 21 — Paper Trading

Créer un moteur de simulation.

Important :

Le paper trading n'est **pas** une connexion à un broker.

Il s'agit uniquement d'une simulation locale basée sur les signaux.

Architecture :

```text
Signal
   ↓
Paper Trading Engine
   ↓
Simulation
   ↓
Position virtuelle
   ↓
Résultat
   ↓
Database
```

---

# 32. Paper trading : fonctionnement

Lorsqu'un BUY arrive :

```text
Entry = signal.entry_price
SL = signal.stop_loss
TP = signal.take_profit
```

Créer une position virtuelle.

Le moteur doit ensuite déterminer si le prix atteint :

```text
SL
```

ou :

```text
TP
```

Le résultat est enregistré.

---

# 33. Résultats en R

Utiliser le concept de R.

Exemple :

```text
Entry = 100
SL = 98
TP = 104
```

Le risque est :

```text
2
```

Si TP est atteint :

```text
+2R
```

Si SL est atteint :

```text
-1R
```

Cela permet de comparer les performances indépendamment du montant engagé.

---

# 34. Statistiques

Le système doit calculer :

```text
Total signals
Winning trades
Losing trades
Win rate
Average R
Total R
Best trade
Worst trade
Maximum drawdown
Profit factor
Average reward
Average risk
```

Et les filtrer par :

```text
Strategy
Symbol
Timeframe
Action
Date range
```

---

# 35. Exemple Discord /stats

Afficher quelque chose comme :

```text
📊 PERFORMANCE

Strategy:
Momentum V1

Period:
Last 30 days

Signals:
127

Win Rate:
61.4%

Average R:
+0.42R

Total:
+53.3R

Max Drawdown:
-8.2R

Long:
+34.1R

Short:
+19.2R
```

---

# 36. Phase 22 — Multi-actifs

Le système doit supporter plusieurs actifs sans modification du code.

Exemples :

```text
BTCUSDT
ETHUSDT
XAUUSD
EURUSD
```

Ajouter les actifs uniquement via configuration.

---

# 37. Phase 23 — Multi-timeframes

Supporter :

```text
5m
15m
30m
1h
4h
1D
```

Le timeframe doit être enregistré avec chaque signal.

Les statistiques doivent pouvoir être filtrées par timeframe.

---

# 38. Phase 24 — Multi-stratégies

Le système doit pouvoir recevoir :

```text
momentum_v1
trend_v1
breakout_v1
gold_v1
```

Chaque signal doit indiquer sa stratégie.

Exemple :

```text
Strategy:
Gold Breakout V1
```

---

# 39. Phase 25 — Multi-timeframe analysis

Prévoir la possibilité d'une stratégie utilisant plusieurs unités de temps.

Exemple :

```text
4H = tendance
1H = confirmation
15m = entrée
```

Le signal final ne doit être généré que si toutes les conditions requises sont satisfaites.

---

# 40. Phase 26 — Scoring

Ajouter éventuellement un score de qualité du signal.

Exemple :

```text
Trend       +20
Momentum    +20
MACD        +15
Volume      +15
Structure   +20
Higher TF   +10
----------------
TOTAL       100
```

Afficher :

```text
Signal Score: 85/100
```

Important :

Le score est un indicateur interne de qualité.

Il ne doit pas être présenté comme :

```text
85% de chance de gagner
```

sauf si une véritable calibration statistique a été effectuée.

---

# 41. Phase 27 — Tests automatisés

Créer des tests pour :

```text
Webhook valide
Webhook invalide
Secret invalide
JSON invalide
BUY valide
SELL valide
BUY avec SL incorrect
SELL avec SL incorrect
TP incorrect
Timestamp expiré
Signal doublon
Database failure
Discord failure
Paper trade BUY
Paper trade SELL
TP atteint
SL atteint
```

Objectif :

```text
pytest
```

doit passer avant chaque déploiement.

---

# 42. Phase 28 — Docker

Créer :

```text
Dockerfile
docker-compose.yml
```

Services :

```text
app
postgres
```

Le système doit pouvoir démarrer avec :

```bash
docker compose up -d
```

---

# 43. Phase 29 — Migration database

Utiliser Alembic.

Ne jamais modifier directement les tables de production.

Chaque changement de schéma doit passer par une migration.

Exemple :

```bash
alembic revision --autogenerate -m "add signals table"
alembic upgrade head
```

---

# 44. Phase 30 — Production

Avant production :

```text
✓ HTTPS
✓ domaine
✓ secrets configurés
✓ PostgreSQL sauvegardé
✓ logs actifs
✓ tests passants
✓ Discord connecté
✓ webhook testé
✓ TradingView testé
✓ déduplication testée
✓ paper trading fonctionnel
```

---

# 45. Sécurité

Obligatoire :

```text
Secrets uniquement dans .env
HTTPS obligatoire
Pas de secrets dans Git
Pas de secrets dans les logs
Permissions Discord minimales
Validation des entrées
Rate limiting du webhook si nécessaire
Déduplication
Validation des timestamps
```

Ne jamais utiliser :

```text
ADMINISTRATOR
```

pour le bot sans nécessité absolue.

---

# 46. Ce que le système ne doit JAMAIS contenir

Ne pas implémenter :

```text
Broker API
Exchange API
Trading API
Order placement
Order cancellation
Position management réel
Leverage automatique
Automatic buying
Automatic selling
Automatic closing
```

Ne pas ajouter de variables telles que :

```text
BINANCE_API_KEY
BINANCE_SECRET
BROKER_API_KEY
TRADING_ACCOUNT_ID
```

Le projet n'a pas besoin de ces informations.

---

# 47. Workflow final

Le workflow final doit être exactement :

```text
                    TRADINGVIEW
                         │
                         │
                    Pine Strategy
                         │
                         ▼
                       ALERT
                         │
                         │ HTTPS POST
                         ▼
                  FASTAPI WEBHOOK
                         │
                         ▼
                    AUTHENTICATION
                         │
                         ▼
                     VALIDATION
                         │
                         ▼
                    DEDUPLICATION
                         │
                         ▼
                     POSTGRESQL
                         │
                         ▼
                  DISCORD SERVICE
                         │
                         ▼
                  📈 SIGNAL DISCORD
                         │
                         ▼
                 👤 UTILISATEUR
                         │
                         ▼
              DÉCISION MANUELLE
```

Le système s'arrête à :

```text
📈 SIGNAL DISCORD
```

La décision de trading reste entièrement humaine.

---

# 48. Règle de développement

L'IA de développement doit travailler **phase par phase**.

Elle ne doit pas essayer de générer tout le projet en une seule réponse.

Pour chaque phase :

1. expliquer ce qui va être créé ;
2. créer les fichiers nécessaires ;
3. implémenter la fonctionnalité ;
4. écrire les tests ;
5. lancer les tests ;
6. corriger les erreurs ;
7. vérifier manuellement le résultat ;
8. seulement ensuite passer à la phase suivante.

À chaque étape, conserver le projet fonctionnel.

Ne jamais casser une fonctionnalité existante pour en ajouter une nouvelle.

---

# 49. Definition of Done

Une phase est considérée comme terminée uniquement lorsque :

```text
[ ] Le code fonctionne
[ ] Les tests passent
[ ] Les erreurs sont gérées
[ ] Les logs sont présents
[ ] Les secrets sont protégés
[ ] La documentation est mise à jour
[ ] Le comportement est vérifié manuellement
```

---

# 50. Ordre obligatoire de réalisation

L'IA doit suivre exactement cet ordre :

```text
PHASE 0  → Préparation
PHASE 1  → Configuration
PHASE 2  → Discord
PHASE 3  → /status
PHASE 4  → FastAPI
PHASE 5  → Webhook
PHASE 6  → Pydantic
PHASE 7  → Authentification
PHASE 8  → Validation
PHASE 9  → Cohérence
PHASE 10 → Déduplication
PHASE 11 → PostgreSQL
PHASE 12 → Discord Signals
PHASE 13 → Pipeline complet
PHASE 14 → Pine Strategy
PHASE 15 → Anti-repainting
PHASE 16 → TradingView Alerts
PHASE 17 → Connexion réelle
PHASE 18 → Error Handling
PHASE 19 → Logging
PHASE 20 → Discord Commands
PHASE 21 → Paper Trading
PHASE 22 → Multi-assets
PHASE 23 → Multi-timeframes
PHASE 24 → Multi-strategies
PHASE 25 → Multi-timeframe analysis
PHASE 26 → Signal scoring
PHASE 27 → Tests complets
PHASE 28 → Docker
PHASE 29 → Database migrations
PHASE 30 → Production
```

---

# 51. Résultat final attendu

À la fin du projet, je dois pouvoir :

1. ouvrir TradingView ;
2. sélectionner BTC, ETH, GOLD ou un autre actif configuré ;
3. sélectionner un timeframe ;
4. appliquer ma stratégie Pine ;
5. attendre qu'une condition soit remplie ;
6. TradingView génère une alerte ;
7. l'alerte arrive sur mon backend ;
8. le backend vérifie le signal ;
9. le signal est enregistré ;
10. le bot Discord publie un Embed ;
11. je vois le signal dans Discord ;
12. je décide moi-même si je veux prendre le trade ;
13. le paper trading permet ensuite de mesurer ce qui aurait été obtenu ;
14. les statistiques permettent d'évaluer objectivement la stratégie.

Le système ne prend **jamais** de position réelle à ma place.

---

# 52. Priorité absolue

Priorités du projet :

```text
1. Fiabilité
2. Sécurité
3. Exactitude des signaux
4. Absence de doublons
5. Traçabilité
6. Statistiques
7. Facilité de maintenance
8. Interface Discord
9. Performance
```

Ne pas sacrifier la fiabilité pour ajouter rapidement des fonctionnalités.

---

# 53. Principe final

Le projet doit rester un système de **trading signals + analytics + paper trading**, et non devenir un système d'exécution automatique.

Architecture :

```text
TradingView
    ↓
Signal
    ↓
Backend
    ↓
Validation
    ↓
Database
    ↓
Discord
    ↓
Utilisateur
    ↓
Décision manuelle
```

C'est l'architecture de référence du projet.
