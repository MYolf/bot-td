# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Langue

Toutes les communications, commits, commentaires et documentation sont en français.

## Règle absolue

**Aucune exécution automatique de trade.** Pas de broker, pas d'exchange, pas de clé API de trading, pas d'ordre (placement, modification, annulation), pas de gestion de position réelle. Le système s'arrête au signal Discord ; la décision de trading est entièrement humaine. Ne jamais ajouter de variables comme `BINANCE_API_KEY`, `BROKER_API_KEY`, `TRADING_ACCOUNT_ID`.

## Principes de développement

- Sécurité d'abord (secrets uniquement en variables d'environnement, jamais dans le code, les logs ou les réponses d'erreur)
- Architecture modulaire (couche HTTP mince, logique métier dans `app/signals/` + `app/services/`, accès données derrière un repository)
- Typage strict partout (Python 3.12+, Pydantic, SQLAlchemy 2.x `Mapped`)
- Tests obligatoires (pytest doit passer avant chaque déploiement et avant de passer à la phase suivante)
- Logs présents à chaque étape du pipeline, sans jamais contenir de secret
- Validation stricte de toute entrée (webhook = frontière non fiable)
- Éviter les duplications (déduplication par `signal_uid` avec contrainte UNIQUE en base)
- Documenter les décisions importantes

## Skills Claude Code

Des Skills spécialisés sont versionnés dans `.claude/skills/`. Ils contiennent les règles détaillées de chaque domaine — **les consulter avant de travailler sur le domaine concerné** :

| Skill             | Domaine                                                        |
|-------------------|----------------------------------------------------------------|
| `tradingview`     | Alertes et webhooks TradingView, payload JSON, sécurité du canal |
| `pine-script`     | Stratégies Pine, `alert_message`, MTF, repainting/lookahead    |
| `strategy-design` | Analyse technique, confluence, cahier des charges de stratégie |
| `backtesting`     | Métriques de performance, biais, out-of-sample, walk-forward   |
| `signal-engine`   | Pipeline métier : auth, validation, déduplication, statuts, RR |
| `fastapi`         | Couche HTTP, endpoint webhook, codes HTTP, tests httpx         |
| `discord-bot`     | discord.py, slash commands, embeds, permissions, rate limits   |
| `postgresql`      | SQLAlchemy, Alembic, contraintes, repository pattern           |
| `testing`         | pytest / pytest-asyncio / httpx, catalogue de tests obligatoire |

Ne pas modifier les Skills sans raison ; en cas de contradiction, `Projet.md` et ce fichier priment.

## Projet

Bot Discord de signaux de trading : TradingView (stratégies Pine Script) envoie des alertes via webhook HTTPS vers un backend FastAPI, qui valide, déduplique, stocke dans PostgreSQL et publie les signaux dans Discord. Paper trading et statistiques pour évaluer les stratégies.

**Règle fondamentale (non négociable)** : ce système est un système de **signalisation uniquement**. Il ne doit jamais se connecter à un broker/exchange, passer/modifier/annuler un ordre, gérer une position réelle ou utiliser une clé API de trading. Aucune fonctionnalité future ne doit contourner cette règle. Ne jamais ajouter de variables comme `BINANCE_API_KEY`, `BROKER_API_KEY`, `TRADING_ACCOUNT_ID`.

## État actuel

Le dépôt est un scaffold initial : README.md, Projet.md (cahier des charges complet), .env.example, .gitignore. Le code (`app/`, `tests/`, `migrations/`, requirements.txt, Dockerfile, docker-compose.yml) reste à créer en suivant les phases de Projet.md.

## Workflow de développement imposé (Projet.md §48-50)

Travailler **phase par phase**, dans l'ordre exact défini dans `Projet.md` (PHASE 0 → PHASE 30). Pour chaque phase :

1. Expliquer ce qui va être créé
2. Créer les fichiers nécessaires
3. Implémenter
4. Écrire les tests
5. Lancer les tests et corriger
6. Vérifier manuellement
7. Seulement ensuite passer à la phase suivante

Ne jamais casser une fonctionnalité existante pour en ajouter une nouvelle. Une phase est terminée seulement quand : code fonctionnel, tests passants, erreurs gérées, logs présents, secrets protégés, comportement vérifié manuellement.

## Commandes (prévues)

```bash
# Environnement (Windows, bash)
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt

# Infrastructure
docker compose up -d                              # PostgreSQL

# Application (API + bot Discord dans un seul process)
uvicorn app.main:app --reload

# Tests
pytest
pytest tests/test_validation.py                   # un fichier
pytest tests/test_validation.py::test_nom         # un test

# Migrations (Alembic — jamais modifier les tables directement)
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture cible

```
TradingView (Pine Strategy, webhook HTTPS POST avec secret partagé)
    → FastAPI POST /webhook/tradingview
    → Signal Processor : auth (secret → 401), validation (listes blanches configurables),
      cohérence (BUY: SL < entry < TP ; SELL: TP < entry < SL), déduplication
    → PostgreSQL (strategies, signals, paper_positions, paper_trades)
    → Discord Bot (embeds de signaux, commandes slash : /status, /lastsignal, /signals, /stats, /strategy)
```

Pipeline strict : auth → validation → déduplication → PostgreSQL → Discord. Un doublon ne doit jamais générer deux messages Discord.

## Structure du code (à créer selon Projet.md §5)

- `app/config/settings.py` — configuration typée, tout via variables d'environnement (python-dotenv)
- `app/api/tradingview.py` — endpoint webhook
- `app/signals/` — schemas Pydantic, validator, processor, deduplication
- `app/database/` — SQLAlchemy (models, repository) + Alembic dans `migrations/`
- `app/discord/` — bot.py, commands.py, embeds.py (discord.py)
- `app/services/` — discord_service.py, signal_service.py
- `app/paper_trading/` — engine, positions, statistics (simulation locale uniquement)
- `tests/` — pytest + pytest-asyncio + httpx

## Points techniques clés

- Python 3.12+, FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic, PostgreSQL, discord.py, Docker Compose.
- Déduplication : identifiant unique `strategy:symbol:timeframe:timestamp_bougie:action` avec contrainte unique en base.
- Statuts des signaux : RECEIVED, VALIDATED, SENT, REJECTED, DUPLICATE, ERROR.
- Risk/Reward calculé côté backend (BUY : risk = entry − SL, reward = TP − entry ; SELL inversé). Paper trading en multiples de R.
- Secrets uniquement dans `.env` (jamais commité, jamais loggé, jamais retournés dans une erreur). Webhook secret : HTTP 401 si invalide.
- Bot Discord : permissions minimales (View Channels, Send Messages, Embed Links, Read Message History), jamais ADMINISTRATOR.
- Multi-actifs / multi-timeframes / multi-stratégies uniquement via configuration, sans modification du code.
- Priorités : fiabilité > sécurité > exactitude des signaux > absence de doublons > traçabilité > statistiques.
