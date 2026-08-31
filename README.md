# bot-td — Bot Discord de signaux de trading

Système de **signalisation uniquement** : une source de signaux détecte les opportunités (BUY/SELL, entrée, Stop Loss, Take Profit, timeframe, stratégie) et les envoie à un backend via webhook, qui les valide, les déduplique, les enregistre dans PostgreSQL et les publie dans un salon Discord.

Deux sources possibles, au choix (JSON identique) :
- **Moteur local** (`engine/`, par défaut) : portage Python de la stratégie Momentum V1, alimenté par les bougies publiques Binance — gratuit, sans compte ni clé, tourne sur le serveur ;
- **TradingView** : stratégies Pine Script (`pine/`) avec alertes webhook (nécessite un abonnement).

> **Règle fondamentale** : ce système **ne passe jamais d'ordre**. Pas de broker, pas d'exchange, pas de clé API de trading. La décision de trader reste entièrement humaine.

## Architecture

```
MOTEUR LOCAL (engine/)                TRADINGVIEW (Pine Strategy)
bougies Binance publiques             alerte webhook (abonnement)
        |  POST interne                       |  HTTPS POST (secret partagé)
        +---------------+--------------------+
                        v
              FASTAPI (/webhook/tradingview)
                        |
                        v
      SIGNAL PROCESSOR (auth, validation, cohérence, déduplication)
                        |
            +-----------+--------------------+
            |                                |
            v                                v
       PostgreSQL                       Discord Bot
      (signaux, stratégies,             (embeds,
       paper trades, statistiques)       commandes, stats)
```

## Stack

- Python 3.12+, FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic
- PostgreSQL
- discord.py
- Docker / Docker Compose
- pytest, pytest-asyncio, httpx

## Structure

```
app/            # Backend (config, api, discord, signals, database, services, paper_trading)
engine/         # Moteur de signaux local (indicateurs, stratégie, position simulée, boucle)
pine/           # Stratégies Pine Script TradingView
tests/          # Tests pytest (157)
migrations/     # Migrations Alembic
```

Le cahier des charges complet et l'ordre des phases se trouvent dans `Projet.md` ; l'avancement détaillé dans `AVANCEMENT.md`.

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
