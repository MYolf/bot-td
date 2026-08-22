# bot-td — Bot Discord de signaux de trading (TradingView)

Système de **signalisation uniquement** : TradingView analyse les marchés (BTC, ETH, or, etc.) avec des stratégies Pine Script, envoie les signaux détectés (BUY/SELL, entrée, Stop Loss, Take Profit, timeframe, stratégie) à un backend via webhook, qui les valide, les déduplique, les enregistre dans PostgreSQL et les publie dans un salon Discord.

> **Règle fondamentale** : ce système **ne passe jamais d'ordre**. Pas de broker, pas d'exchange, pas de clé API de trading. La décision de trader reste entièrement humaine.

## Architecture

```
TRADINGVIEW (Pine Strategy)
        |  HTTPS POST (webhook, secret partagé)
        v
FASTAPI (/webhook/tradingview)
        |
        v
SIGNAL PROCESSOR (auth, validation, cohérence, déduplication)
        |
        +------------------+
        |                  |
        v                  v
   PostgreSQL          Discord Bot
  (signaux,             (embeds,
   stratégies,           commandes,
   paper trades,         stats)
   statistiques)
```

## Stack

- Python 3.12+, FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic
- PostgreSQL
- discord.py
- Docker / Docker Compose
- pytest, pytest-asyncio, httpx

## Structure (cible)

```
app/            # Application (config, api, discord, signals, database, services, ...)
tests/          # Tests pytest
migrations/     # Migrations Alembic
```

Le cahier des charges complet et l'ordre des phases se trouvent dans `Projet.md`.

## Installation (développement)

Prérequis : Python 3.12+, Docker Desktop, Git.

```bash
git clone <repo> && cd bot-td
python -m venv .venv
.venv\Scripts\activate        # Windows (bash : source .venv/Scripts/activate)
pip install -r requirements.txt
cp .env.example .env          # puis remplir les valeurs
```

## Démarrage

```bash
docker compose up -d          # PostgreSQL
alembic upgrade head          # migrations (première fois uniquement)
# Windows : le flag --loop est requis (psycopg async incompatible ProactorEventLoop)
uvicorn app.main:app --reload --loop app.main:selector_loop
```

## Avertissement

Ce projet est un outil d'information. Les signaux ne constituent pas un conseil financier. Aucune exécution d'ordre n'est effectuée par le système.
