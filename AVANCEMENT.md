# Avancement du projet

> Fichier mis à jour au fil des phases. ✅ = fait, 🔧 = en cours, ⏳ = à faire, 👤 = à faire de TON côté.

## ✅ Terminé (par Claude)

- **Phase 0 — Préparation**
  - Ancien code Node.js archivé dans la branche `archive/nodejs-scaffold` et retiré de `main`
  - `.gitignore` (Python + secrets), `.env.example`, `README.md` créés
- **Phase 1 — Configuration**
  - `app/config/settings.py` : configuration typée, secrets obligatoires, listes blanches configurables
  - `requirements.txt`, environnement virtuel `.venv` créé et installé
- **Phase 2 — Application Discord**
  - App créée dans le Developer Portal, bot invité avec permissions minimales, token dans `.env` (reset suite à une exposition accidentelle dans `.env.example`, corrigée sans commit)
- **Phase 3 — Bot Discord + `/status`**
  - `app/discord/bot.py` : `SignalBot(commands.Bot)`, intents minimaux (`guilds`), sync des commandes limitée à la guild, handler d'erreurs global (log + message sobre, jamais de stacktrace)
  - `app/discord/commands.py` : `/status` en ephemeral (Environment, Database, Discord)
  - Démarrage dans le lifespan FastAPI (`asyncio.create_task`), arrêt propre au shutdown ; `DISCORD_ENABLED=false` permet de désactiver le bot (tests)
  - Vérifié manuellement : bot connecté (`bot-td-signals`), `/status` fonctionnel dans Discord
- **Phase 4 — FastAPI**
  - `app/main.py` : application FastAPI, `GET /health` → `{"status": "ok"}` (testé avec un vrai serveur)
- **Phase 5 — Webhook TradingView**
  - `app/api/tradingview.py` : `POST /webhook/tradingview`
- **Phase 6 — Schéma Pydantic**
  - `app/signals/schemas.py` : modèle `TradingViewSignal` (prix en texte acceptés, timeframes normalisés "1H"→"60")
- **Phase 7 — Authentification**
  - Secret comparé en temps constant, `401` si invalide, secret jamais loggé ni retourné
- **Phase 8 — Validation métier**
  - `app/signals/validator.py` : listes blanches (stratégie/symbole/exchange/timeframe) + fraîcheur du timestamp
- **Phase 9 — Cohérence**
  - BUY : SL < entrée < TP ; SELL : TP < entrée < SL, sinon rejet loggé avec code de raison
- **Phase 10 — Déduplication**
  - `app/signals/deduplication.py` : `signal_uid` = `strategy:symbol:timeframe:timestamp_bougie:action`
  - Contrainte UNIQUE en base ; conflit => rollback propre + statut DUPLICATE, jamais deux messages Discord
- **Phase 11 — PostgreSQL**
  - `docker-compose.yml` : PostgreSQL 16 (volume persistant, healthcheck)
  - `app/database/` : `database.py` (engine async + sessions), `models.py` (strategies, signals, paper_positions, paper_trades ; prix `Numeric(20,8)`, timestamps UTC, CHECK status/action), `repository.py` (pattern repository, RR calculé côté backend)
  - Alembic initialisé (`migrations/`), URL lue depuis la config, migration initiale appliquée
  - Webhook branché : validation -> insertion (VALIDATED) -> 503 propre si DB indisponible
  - **Windows** : lancement avec `--loop app.main:selector_loop` (psycopg async incompatible ProactorEventLoop, imposé par uvicorn)
  - Vérifié manuellement : 2 envois identiques -> `accepted` puis `duplicate`, une seule ligne en base
- **Phase 12 — Envoi Discord (embeds)**
  - `app/discord/embeds.py` : embeds conformes Projet.md §22 (🟢 LONG / 🔴 SHORT, prix lisibles, RR `1:2`, heure UTC, lisible mobile)
  - `app/services/discord_service.py` : encapsule totalement discord.py (interface `SignalNotifier` injectable/mockable), `DiscordSendError` maîtrisé
  - Repository : `mark_sent` (statut SENT + `discord_message_id` stocké), `mark_error`
  - Pipeline : persistance AVANT notification ; échec Discord => signal conservé en base (statut ERROR)
- **Phase 13 — Pipeline complet vérifié**
  - JSON manuel -> validation -> PostgreSQL -> Discord : BUY + SELL envoyés (statut SENT, message_id réels), doublon ignoré, embeds confirmés visuellement dans Discord
- **Tests : 55/55 passent** (`pytest`, dont 14 sur embeds/notification)

## 🔧 En cours / à venir (par Claude)

- **Phase 14-15** — Première stratégie Pine Script + anti-repainting
- **Phase 16-17** — Alertes TradingView réelles + connexion
- **Phase 18+** — Error handling, logging, commandes Discord, paper trading, production

## 👤 Ce qu'il ME reste à faire (de ton côté)

Rien de bloquant pour l'instant ✅ (app Discord créée, bot invité, Docker installé, `.env` rempli).
Les prochaines actions utilisateur seront indiquées ici au fil des phases.

## 📌 Rappels importants

- Le système **ne passe jamais d'ordre** : signalisation uniquement
- Les secrets vont **uniquement** dans `.env` (jamais commités, jamais montrés)
- Commandes utiles : `pytest` (tests), `uvicorn app.main:app --reload --loop app.main:selector_loop` (API + bot, Windows)
- Tester le webhook localement :
  ```bash
  curl -X POST http://localhost:8000/webhook/tradingview -H "Content-Type: application/json" \
    -d '{"secret":"MON_SECRET","strategy":"momentum_v1","symbol":"BTCUSDT","exchange":"BINANCE","timeframe":"15","action":"BUY","price":104532.42,"stop_loss":103800,"take_profit":106000,"timestamp":"<heure UTC actuelle>"}'
  ```
