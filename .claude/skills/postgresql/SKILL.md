---
name: postgresql
description: >-
  Persistance avec PostgreSQL, SQLAlchemy 2.x et Alembic : modèles, migrations,
  contraintes uniques (déduplication des signaux), indexes, transactions,
  repository pattern et bonnes pratiques de stockage. Utiliser ce skill dès
  qu'on travaille sur app/database/, migrations/, le schéma des tables ou
  les requêtes du bot.
---

# PostgreSQL — SQLAlchemy et Alembic

## Rôle

La base est la **source de vérité** : historique complet des signaux,
stratégies, paper trading, statistiques. La déduplication repose sur une
contrainte UNIQUE en base (pas en mémoire : l'app peut redémarrer).

## Stack

- SQLAlchemy 2.x, style déclaratif `Mapped` / `mapped_column`, `DeclarativeBase`.
- Driver async : `postgresql+psycopg` (psycopg 3) via
  `postgresql+psycopg://user:pass@host:5432/db` dans `DATABASE_URL`.
- Alembic pour toutes les migrations.
- Sessions/engines créés dans `app/database/database.py` (engine async +
  `async_sessionmaker`), fermés proprement dans le lifespan FastAPI.

## Tables du projet

```text
strategies        id, name (unique), version, description, enabled, created_at, updated_at
signals           id, signal_uid (UNIQUE), strategy_id (FK), symbol, exchange, timeframe,
                  action, entry_price, stop_loss, take_profit, risk_reward,
                  signal_timestamp, received_at, status, discord_message_id, created_at
paper_positions   id, signal_id (FK), status (OPEN/CLOSED), opened_at, closed_at, result_r
paper_trades      id, paper_position_id (FK), exit_reason (TP/SL), exit_price, closed_at
```

Conventions :

- Types monétaires/prix : `Numeric(precision, scale)` (ex. `Numeric(20, 8)` pour
  le crypto) — **jamais Float** pour les prix.
- Timestamps : `DateTime(timezone=True)` en **UTC**.
- `action` : contrainte `CHECK` ou enum applicatif (`BUY`/`SELL`).
- `status` : valeurs `RECEIVED, VALIDATED, SENT, REJECTED, DUPLICATE, ERROR`.

## Modèles (exemple de style)

```python
class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_uid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(4))
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    ...
```

## Indexes et contraintes

- `UNIQUE (signal_uid)` : cœur de la déduplication (voir skill
  `signal-engine`). L'insertion en conflit => `IntegrityError` intercepté =>
  statut `DUPLICATE`.
- Index sur les colonnes de filtrage des stats : `strategy_id`, `symbol`,
  `timeframe`, `action`, `signal_timestamp`.
- Clés étrangères explicites ; pas de cascade silencieuse (historique
  immuable : on désactive une stratégie via `enabled`, on ne la supprime pas).

## Transactions

- Une unité de travail = une session ; commit explicite à la fin du pipeline
  d'un signal (insertion signal + changement de statut).
- Ne jamais garder une session ouverte à travers un appel réseau Discord :
  ouvrir, écrire, fermer **avant** la notification.
- `IntegrityError` sur `signal_uid` => rollback propre + chemin DUPLICATE.

## Repository pattern

```text
app/database/models.py      modèles SQLAlchemy (aucune logique métier)
app/database/repository.py  classe(s) SignalRepository / StrategyRepository :
                            toutes les requêtes SQL derrière des méthodes typées
```

- Le Signal Engine et le bot Discord dépendent du **repository**, jamais de
  sessions/Query directs : testable avec DB de test ou mocks.
- Pas de SQL brut si SQLAlchemy suffit ; requêtes de stats agrégées
  (win rate, sum R, drawdown) possibles en SQL propre via le repository.

## Alembic

- Init une seule fois : `alembic init migrations`.
- URL de connexion lue depuis la configuration (pas en dur dans
  `alembic.ini`).
- Workflow :

```bash
alembic revision --autogenerate -m "add signals table"
alembic upgrade head
```

- **Règles** : jamais de modification directe des tables de production ; tout
  changement de schéma = une migration ; vérifier le script autogénéré avant
  de l'appliquer (autogenerate n'est pas infaillible, notamment pour les
  contraintes).

## Bonnes pratiques

- Pas de `SELECT *` ; ne sélectionner que les colonnes utiles (les commandes
  Discord n'ont pas besoin de tout le row).
- Pagination pour `/signals` (`ORDER BY signal_timestamp DESC LIMIT n`).
- Mots de passe uniquement dans `.env` ; jamais dans le code, les logs, ni
  les migrations.
- Backup PostgreSQL avant toute migration de production (cf. Projet.md).
- Docker Compose fournit PostgreSQL en développement : `docker compose up -d`.

## Critères de validation

- [ ] Contrainte UNIQUE effective sur `signal_uid` (test d'insertion double).
- [ ] Prix en `Numeric`, timestamps en `DateTime(timezone=True)` UTC.
- [ ] Toutes les requêtes passent par le repository.
- [ ] Toute évolution de schéma passe par une migration Alembic vérifiée.
- [ ] Indexes présents sur les colonnes de filtrage des statistiques.
