# Procédure de migration de base de données (Phase 29)

Projet.md §43 : **ne jamais modifier directement les tables**. Chaque changement
de schéma passe par une migration Alembic, testée et réversible.

## Règles

1. **App arrêtée** pendant la migration (`docker compose stop app`) — l'application
   ne doit jamais tourner contre un schéma en cours de modification.
2. **Backup obligatoire avant toute migration** (même en dev).
3. Toute migration est d'abord validée sur la base de développement.
4. Un rollback peut **perdre les données des colonnes supprimées** — le backup est
   la seule vraie garantie de retour arrière.

## Historique des migrations

| Révision | Description |
|---|---|
| `5d0c3c19d403` | Tables initiales (strategies, signals, paper_positions, paper_trades) |
| `a1f4c8e27b91` | Colonne `signals.score` (Phase 26 — score de qualité optionnel) |

## Appliquer les migrations (déploiement)

```bash
# 1. Arrêter l'application (postgres reste up)
docker compose stop app

# 2. Backup (le dossier backups/ est ignoré par git)
mkdir -p backups
docker exec bot-td-postgres pg_dump -U bot_td -d bot_td > backups/backup_$(date +%Y%m%d_%H%M).sql

# 3. Vérifier l'état actuel avant / après
alembic current

# 4. Appliquer
alembic upgrade head

# 5. Vérifier : révision à head + application démarre sans erreur
alembic current          # doit afficher "(head)"
docker compose start app
curl http://localhost:8000/health
```

Note : le conteneur `app` exécute `alembic upgrade head` à son démarrage
(voir Dockerfile) — un simple `docker compose up -d` applique donc aussi les
migrations, mais toujours **après un backup** en production.

## Créer une nouvelle migration

Après modification des modèles SQLAlchemy (`app/database/models.py`) :

```bash
alembic revision --autogenerate -m "description courte"
# Relire le fichier généré dans migrations/versions/ (autogenerate n'est pas infaillible)
alembic downgrade -1 && alembic upgrade head   # cycle aller-retour de validation en dev
```

## Rollback (retour arrière)

```bash
docker compose stop app
alembic downgrade -1        # annule LA dernière migration (voir avertissement règle 4)
alembic current
docker compose start app
```

## Restaurer un backup (dernier recours)

```bash
docker compose stop app
docker exec -i bot-td-postgres psql -U bot_td -d bot_td < backups/backup_YYYYMMDD_HHMM.sql
docker compose start app
```

## Validation faite en Phase 29 (2026-08-25, base dev)

- Backup `pg_dump` → `downgrade -1` (colonne `score` supprimée proprement)
  → `upgrade head` (recréée) → données intactes, app redémarrée saine
- Comportement constaté : les valeurs de la colonne supprimée sont perdues au
  downgrade (NULL après retour arrière) — conforme à la règle 4
