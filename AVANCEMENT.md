# Avancement du projet

> Fichier mis à jour au fil des phases. ✅ = fait, 🔧 = en cours, ⏳ = à faire, 👤 = à faire de TON côté.

## 📍 Point d'avancement (2026-08-31) — PROJET TERMINÉ 🎉

- **Phases 0 → 30 toutes terminées**, y compris la Phase 30 (Production/clôture) : checklist §44 de Projet.md intégralement satisfaite (voir ci-dessous)
- **Production EN LIGNE** depuis le 2026-08-29 : https://bot-td.duckdns.org (VPS OVH, HTTPS Caddy/Let's Encrypt)
- **Moteur de signaux local DÉPLOYÉ** le 2026-08-31 : conteneur `engine` (BTCUSDT + ETHUSDT, 15m) tournant sur le VPS, positions simulées reconstruites au démarrage — valeurs identiques au backtest au centime près, première clôture SL détectée en direct
- **Tests** : **157/157 passent** (`pytest`)
- **Phase 16 (alerte TradingView réelle)** : **optionnelle** à vie — le moteur local remplace TradingView tant qu'il n'y a pas d'abonnement (les stratégies Pine restent utilisables telles quelles si un abonnement est pris un jour)
- Pipeline complet et automatique : moteur local (ou TradingView, JSON identique) → FastAPI → PostgreSQL → Discord, avec déduplication, paper trading en R, score de qualité et 5 commandes slash. **Aucune commande à faire : les embeds arrivent seuls dans le salon des signaux.**

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
- **Phase 14 — Stratégie Pine `momentum_v1`**
  - `pine/momentum_v1.pine` (Pine v6) : EMA 50/200 + RSI 14 + MACD 12/26/9, tous paramètres configurables (inputs)
  - Signal à la transition uniquement (pas de spam) ; SL/TP en % ; `strategy.exit` systématique (jamais d'entrée sans sortie)
  - `alert_message` = JSON strictement conforme au schéma du webhook ; secret renseigné via input TradingView (jamais commité)
- **Phase 15 — Anti-repainting (intégré dès la conception)**
  - `calc_on_every_tick = false`, `process_orders_on_close = true`, aucun `request.security`
  - Conditions sur bougies fermées uniquement ; commission 0.1 % + slippage 2 ticks actifs dès le backtest
  - Validation en conditions réelles à faire en Phase 16-17 (alerte "Once Per Bar Close", comparaison Replay/historique)
- **Tests : 55/55 passent** (`pytest`, inchangés — le Pine se valide dans TradingView)
- **Phase 18 — Gestion des erreurs**
  - Codes HTTP alignés sur la règle TradingView : rejets métiers (symbole non autorisé, SL/TP incohérents, signal expiré...) en **200 + statut `rejected`** avec raison (TradingView traite un non-2xx comme un échec de l'alerte) ; secret invalide 401, JSON invalide 422, DB indisponible **500** (loggé avec tout le contexte métier, jamais le secret)
  - Handler d'exceptions global (`app/main.py`) : toute exception non gérée → 500 générique `internal_error` (aucun détail/stacktrace côté client, tout dans les logs serveur)
  - Tests : DB indisponible (500 + loggué), exception inattendue (handler global), secret webhook / token Discord / mot de passe DB jamais présents dans les logs (chemins 401, rejet, succès, 500)
- **Phase 19 — Logs**
  - Format déjà conforme (`app/utils/logging.py`, Phase 4) : `2026-08-22 22:14:03 INFO ...`, logger par module, niveau via `LOG_LEVEL`, bibliothèques tierces réduites au WARNING
  - Chaque étape du pipeline loggue avec contexte (received/validated/rejected/duplicated/stored/notified/error) — vérifié par les tests Phase 18
- **Phase 20 — Commandes Discord** (les cinq, toutes en `ephemeral`)
  - `/status` : Bot / Database (ping SQL réel `SELECT 1` → ONLINE/OFFLINE) / Webhook / Environment — format Projet.md §30
  - `/lastsignal` : dernier signal en embed (réutilise le format des signaux), message simple si aucun
  - `/signals limite:1-10 (défaut 5)` : liste compacte des derniers signaux (emoji, symbole, stratégie, timeframe, date UTC, statut)
  - `/stats` : total, par action, par statut, par stratégie (les stats de performance en R arrivent avec le paper trading, Phase 21)
  - `/strategy` : stratégies enregistrées (état actif/désactivé, version, nombre de signaux)
  - Lectures 100 % via le repository (`get_latest`, `count_all`, `count_by_*`, `list_all`) — jamais de SQL inline ; erreurs des commandes gérées par le handler global du bot (Phase 3)
- **Phase 21 — Paper trading (simulation locale)**
  - `app/paper_trading/engine.py` : chaque signal stocké ouvre une position virtuelle (entry/SL/TP du signal) ; le prix d'entrée de tout signal ultérieur sur le même symbole sert de prix de marché → SL ou TP atteint = clôture + résultat en R (TP → +RR, SL → −1R, Projet.md §33)
  - **Aucun appel réseau** : la simulation vit uniquement des signaux et de la base (règle absolue respectée) ; moteur initialisé dans le lifespan, branché au webhook en best-effort (un échec paper trading ne touche jamais le signal — testé)
  - `app/paper_trading/statistics.py` : win rate, total R, avg R (expectancy), best/worst, profit factor, max drawdown en R — fonctions pures
  - `PaperRepository` : open/close de positions, unicté un signal = au plus une position, résultats clôturés
  - `/stats` enrichi : champ "Paper trading (R)" (trades clôturés/ouverts, win rate, total/moyenne R, max drawdown) + rappel "< 30 trades = non significatif"
  - Tests : BUY/SELL, TP (+RR), SL (−1R), prix neutre, doublon (une seule position), symboles indépendants, échec moteur ne casse pas le webhook
- **Phase 22 — Multi-actifs** : déjà couvert par `ALLOWED_SYMBOLS` (ajout d'un actif = une ligne de `.env`, zéro code) ; prouvé par tests (BTCUSDT/ETHUSDT/XAUUSD dans tout le pipeline, actif non configuré rejeté, positions paper par actif)
- **Phase 23 — Multi-timeframes** : timeframe stocké avec chaque signal (depuis la Phase 11) ; **statistiques filtrables par timeframe** (`/stats timeframe:15`) — repository filtrable (`count_*`, `closed_results`), filtre invalide refusé avec la liste des valeurs autorisées
- **Phase 24 — Multi-stratégies** : couvert par `ALLOWED_STRATEGIES` + table `strategies` + affichage lisible ("Momentum V1") ; **`/stats strategie:...`** filtre aussi par stratégie (filtres combinables, critères affichés dans l'embed)
- **Phase 25 — Analyse multi-timeframe** (`momentum_mtf_v1`)
  - `pine/momentum_mtf_v1.pine` (Pine v6) : 4H = tendance (EMA 50/200), 1H = confirmation (RSI + MACD), TF du graphique (15m) = entrée — **le signal final n'est émis que si les TROIS niveaux sont alignés simultanément à la clôture** (jamais contre le timeframe supérieur)
  - MTF anti-repainting : `request.security` avec le combo sûr `expression[1]` + `lookahead_on` → uniquement des bougies supérieures FERMÉES (pas de future leak, historique == temps réel) ; `calc_on_every_tick=false`, `process_orders_on_close=true`, frais 0,1 % + slippage dès le backtest
  - Timeframes 4H/1H configurables en inputs (graphique sur le TF d'entrée) ; `alert_message` = JSON conforme au schéma du webhook (`strategy=momentum_mtf_v1`, `timeframe=TF du graphique`)
  - Backend inchangé (Phases 22-24 : multi-TF/stratégies par configuration) ; `momentum_mtf_v1` ajoutée à la liste blanche par défaut (`settings.py`, `.env.example`)
  - Cahier des charges complet documenté en tête du fichier Pine (conforme au skill strategy-design)
  - Tests : JSON exact de la stratégie accepté sur tout le pipeline (stocké + notifié + position paper) ; les 3 niveaux (240/60/15) coexistent sans collision de déduplication — **104/104 passent**
- **Phase 26 — Score de qualité du signal** (Projet.md §40)
  - Barème centralisé dans `app/signals/scoring.py` : trend /20, momentum /20, MACD /15, volume /15, structure /20, HTF /10 (somme = 100) ; composantes **optionnelles** — une stratégie n'envoie que ce qu'elle évalue
  - Le total est **calculé côté backend** (même principe que le Risk/Reward : le client ne fixe jamais le total lui-même) ; hors bornes => rejet métier `invalid_score` (200 + raison, cohérent Phase 18)
  - Schéma : 6 champs `score_*` optionnels (coercition str→int, TradingView envoie du texte) ; colonne `signals.score` nullable (NULL = aucun score), migration Alembic `a1f4c8e27b91` appliquée sur la base dev
  - Embed Discord : champ **"Signal Score : 55/100"** uniquement si des composantes sont envoyées, avec footer *"qualité interne du signal (pas une probabilité de gain)"* — exigence §40 : jamais présenté comme une probabilité
  - Pine : composantes **graduées** (séparation EMA, force RSI, expansion MACD, séparation 4H pour MTF) ajoutées à l'`alert_message` des deux stratégies — valeurs de clôture uniquement, anti-repainting inchangé
  - Tests (14 nouveaux, **118/118 passent**) : total/100, composantes absentes = 0, bornes (trop grand/négatif rejetés, max exact accepté), stockage en base, score NULL sans composante, affichage embed (avec et sans), déduplication inchangée, coercition des chaînes, `/lastsignal` recharge le score
- **Phase 27 — Tests automatisés** : audit du catalogue obligatoire Projet.md §41 contre la suite existante (déjà test-driven depuis la Phase 4), puis comblement des trous
  - Audit — tout le catalogue §41 était couvert SAUF trois chemins testés seulement au niveau unitaire (validator) et pas en bout en bout via le webhook :
    | Cas §41 | Couverture |
    |---|---|
    | Webhook valide/invalide, secret invalide, JSON invalide, BUY/SELL valides | `test_webhook.py` (existant) |
    | BUY/SELL SL incorrect, TP incorrect, timestamp expiré | `test_validation.py` (unitaire) + **e2e ajouté** |
    | Signal doublon (une seule notification, une seule ligne) | `test_deduplication.py` + `test_notification.py` |
    | Database failure (500 + loggué, secret jamais loggé) | `test_error_handling.py` |
    | Discord failure (signal conservé en ERROR) | `test_notification.py` |
    | Paper trade BUY/SELL, TP atteint (+RR), SL atteint (−1R) | `test_paper_trading.py` |
  - Ajouts dans `test_webhook.py` (7 tests) : **secret absent/vide → 422** ; **timestamp expiré e2e** (200 + `rejected` + rien en base + aucune notification) ; **incohérences SL/TP e2e** paramétrées (BUY/SELL × SL/TP → `incoherent_stop_loss`/`incoherent_take_profit`, rien stocké)
  - **125/125 tests passent** ; `pytest` vert = prérequis de déploiement respecté
- **Phase 28 — Docker** (Projet.md §42)
  - `Dockerfile` : `python:3.12-slim`, dépendances en couche cachée, utilisateur non-root, `EXPOSE 8000` ; démarrage = `alembic upgrade head` puis `uvicorn` (le schéma est toujours appliqué par migration, jamais directement)
  - `.dockerignore` : `.env`, `.venv`, `tests/`, `.git`... **aucun secret n'entre dans l'image**
  - `docker-compose.yml` : services `app` + `postgres` (conforme §42) ; `env_file: .env` pour les secrets, `DATABASE_URL` surchargé vers `postgres:5432` (dans le conteneur, `localhost` ne désigne plus la base ; l'env réelle prime sur le `.env` pour pydantic-settings) ; `depends_on` sur le healthcheck PostgreSQL, `restart: unless-stopped`, healthcheck app sur `/health`
  - Service `db` renommé `postgres` (ancien conteneur recréé via `--remove-orphans`, **données conservées** dans le volume `postgres_data`)
  - Vérifié manuellement : `docker compose up -d --build` → 2 conteneurs healthy, migrations appliquées au démarrage, bot Discord connecté depuis le conteneur, `GET /health` → 200, webhook avec mauvais secret → 401
  - Tests : **125/125 passent** (inchangés — l'infrastructure ne touche pas au code)
- **Phase 29 — Migration database** (Projet.md §43)
  - Alembic déjà en place et systématique depuis la Phase 11 (2 migrations, jamais de modification directe des tables) ; le conteneur `app` applique `alembic upgrade head` à chaque démarrage
  - `MIGRATIONS.md` : procédure formalisée — règles (app arrêtée, backup obligatoire, rollback = perte possible des colonnes supprimées), déploiement pas à pas (backup `pg_dump` → `alembic upgrade head` → vérifications → redémarrage), création de migration, rollback, restauration de backup
  - `backups/` ajouté au `.gitignore` (les dumps ne sont jamais commités)
  - **Validé en conditions réelles sur la base dev** : backup → `downgrade -1` (colonne `signals.score` supprimée proprement) → `upgrade head` (recréée) → données intactes, app redémarrée saine, `/health` OK ; constat documenté : les valeurs des colonnes supprimées sont perdues au downgrade (NULL) — d'où le backup obligatoire

- **Moteur de signaux local — Option A « sans TradingView »** (2026-08-31)
  - Contexte : le plan gratuit TradingView n'autorise ni alertes de stratégie ni webhooks → source de signaux portée en Python, sur le VPS déjà payé
  - `engine/` : `indicators.py` (EMA/RSI Wilder/MACD fidèles aux `ta.*`, amorce SMA comme en Pine), `strategy.py` (portage **exact** de `momentum_v1` : mêmes conditions, transitions uniquement, SL 1 %/TP 2 %, scores Phase 26 ; comparaisons « na-safe » comme Pine), `position.py` (position simulée pour répliquer le comportement d'alerte TradingView : SL/TP intrabar, SL prioritaire, `pyramiding=0` → un signal dans le sens de la position n'est PAS émis, renversement oui), `binance_client.py` (API publique klines, **aucune clé**, bougie en formation écartée), `webhook_client.py` (retry/backoff, 401 sans retry, secret jamais loggé), `runner.py` (poll 45 s → bougie fermée → évaluation → envoi), `backtest.py` (CLI de vérification du portage)
  - Backend **inchangé** : même JSON que TradingView, pipeline identique ; `timestamp` = borne de clôture de bougie → fraîcheur OK (< 300 s) et dédup garantie après redémarrage (recalcul sans état depuis l'historique)
  - Anti-repainting équivalent Pine : évaluation à la clôture uniquement, premier relevé sans émission (reconstruction de la position simulée depuis l'historique)
  - Vérifié manuellement : backtest 30 j BTCUSDT 15m → 118 transitions brutes vs **27 signaux émis** (91 filtrés par la position simulée, comme TradingView l'aurait fait) ; client Binance en direct : 499/500 klines (la bougie en formation est bien écartée)
  - Configuration `.env` : `ENGINE_ENABLED`, `ENGINE_SYMBOLS` (défaut BTCUSDT+ETHUSDT), `ENGINE_TIMEFRAME` (défaut 15), `ENGINE_POLL_SECONDS` (défaut 45), `ENGINE_WEBHOOK_URL` (surchargé en interne par compose) ; conteneur `engine` ajouté aux deux compose (POST interne `http://app:8000`, aucune exposition)
  - Tests : 32 nouveaux (indicateurs à valeurs calculées à la main, transitions/portage, tracker de position, boucle runner avec fakes) — **157/157 passent**

- **Phase 30 — Production/clôture** (Projet.md §44) — checklist « avant production » vérifiée ligne par ligne :
  | Exigence §44 | État |
  |---|---|
  | HTTPS | ✅ Caddy + certificat Let's Encrypt auto-renouvelé (DNS challenge DuckDNS) |
  | Domaine | ✅ `bot-td.duckdns.org` → VPS OVH |
  | Secrets configurés | ✅ `.env` de production sur le VPS uniquement (jamais commité) |
  | PostgreSQL sauvegardé | ✅ procédure `pg_dump` formalisée (MIGRATIONS.md, Phase 29) + backup automatisé OVH |
  | Logs actifs | ✅ chaque étape du pipeline, contextes métier, jamais de secret (testé Phase 18) |
  | Tests passants | ✅ 157/157 (`pytest`) |
  | Discord connecté | ✅ bot en ligne, `/status` OK (vérifié sur le VPS) |
  | Webhook testé | ✅ e2e : 200/sent, 401 secret invalide, duplicate (Phase 17) |
  | TradingView testé | ✅ remplacé par le moteur local (`engine`), déployé et vérifié en direct le 2026-08-31 (positions reconstruites conformes au backtest, clôture SL détectée en temps réel) ; l'alerte TradingView réelle reste optionnelle |
  | Déduplication testée | ✅ contrainte UNIQUE + e2e (jamais deux messages Discord) |
  | Paper trading fonctionnel | ✅ positions virtuelles, résultats en R, `/stats` |
- **Déploiement du moteur (2026-08-31)** : `git pull` + `up -d --build` sur le VPS → 4 conteneurs (app healthy, postgres, caddy, engine running) ; log de démarrage conforme : `Moteur démarré symbols=['BTCUSDT','ETHUSDT']`, `Premier relevé ... position simulée=...` (BTCUSDT short 78092.01 / ETHUSDT short 2440.28 = derniers signaux du backtest, au centime près)

## 🔧 En cours / à venir

### Phase 31 — Moteur de confluence (démarrée 2026-09-01)

Objectif : plusieurs stratégies + confluence multi-dimensionnelle. Spécification de référence : **`CONFLUENCE.md`** (décision utilisateur non négociable : les indicateurs qualifient, seuls des événements structurels déclenchent).

- ✅ **Étape 1 — Fondations** (2026-09-01) :
  - `engine/structure.py` : swings fractals confirmés (k bougies de chaque côté), BOS sur clôture, CHOCH — famille DÉCLENCHEURS
  - `engine/features.py` : états fusionnés par famille (tendance EMA, momentum RSI+MACD, volatilité ATR, volume RVOL sans auto-dilution, VWAP ancré 00:00 UTC) — famille QUALIFICATION, jamais déclencheur
  - `engine/indicators.py` : ajout `true_range` / `atr` (portage `ta.atr`)
  - `engine/feature_study.py` : étude forward returns conditionnels (4/16/48 bougies) — étape 1 du protocole anti-overfitting
  - 14 nouveaux tests dont **propriété de préfixe** (anti-lookahead) pour toutes les features — 201/201 passent
  - Première étude réelle BTCUSDT 15m 90 j : RVOL≥2 = meilleur profil ; états bearish > bullish sur la fenêtre (signature mean-reversion) ; tout est sous le seuil de frais à h=4 → la sélection de signaux sera déterminante. Une seule fenêtre = aucune conclusion définitive.
- ✅ **Étape 2 — Zones et liquidité** (2026-09-01) :
  - `engine/structure.py` : `liquidity_sweeps` — percée d'un swing confirmé (profondeur ≥ 0.1×ATR, âge ≥ 10 bougies), clôture de récupération dans ≤ 3 bougies ; cassure profonde = swing mort ; un swing = un balayage
  - `engine/zones.py` : `displacements` (run ≤ 3 bougies ≥ 1.5×ATR, émis une fois par run), `fair_value_gaps` (gap 3 bougies ≥ 0.25×ATR, retest/remplissage total/expiration, flag `with_displacement`), `order_blocks` (BOS porté par un displacement → dernière bougie opposée, retest/mitigation 50 %/invalidation/expiration)
  - `feature_study` étendu ; 12 nouveaux tests (dont préfixe pour zones : les champs de suivi ne peuvent différer que par des valeurs postérieures à la coupure) — **213/213 passent**
  - Étude 90 j BTC **et** ETH : **sweep bullish = meilleur déclencheur** (h16 : BTC +0.153 % win 61.5 %, ETH +0.198 % win 64.1 % — au-dessus du seuil de frais sur les DEUX symboles) ; FVG « avec displacement » > « sans » sur les deux (hypothèse validée) ; OB retest fort sur ETH (+0.368 % h48) mais pas BTC ; mean-reversion confirmée partout. Une fenêtre = pas de conclusion définitive, mais les candidats pour le moteur de confluence se dégagent.
- ✅ **Étape 3 — Moteur de confluence v0 + backtest comparatif** (2026-09-01) :
  - `engine/confluence.py` : gâchettes non compensables (ATR défini, tendance 15m ET 1H non opposées, RR ≥ 1.5) + déclencheur obligatoire (sweep / BOS / retest OB / retest FVG) + score /100 à 5 catégories de 20 (poids égaux v0) ; SL structurel borné [1, 2.5]×ATR, TP 2R ; `resample` 15m→1H (buckets complets uniquement, anti-lookahead)
  - `engine/confluence_backtest.py` : simulateur unique pour les deux stratégies (entrée à clôture, une position, SL prioritaire, frais en R, renversement, clôture forcée en fin) + métriques (expectancy, PF, DD, séries) ; CLI `python -m engine.confluence_backtest`
  - 8 nouveaux tests (221/211 → **221/221**), dont préfixe du moteur complet
  - **Résultat 90 j, BTC et ETH — le backtest RÉFUTE la v0 telle quelle** (rôle attendu) : confluence −143 R / 324 trades (BTC) vs baseline momentum −3.5 R / 100 ; médiane des pertes ≈ −1.15 R (frais ≈ 0.15-0.4 R par trade sur 15m) ; **score inversé sur BTC** (≥80 → win 26 % : le score récompense la poursuite de tendance dans un régime qui la punit) ; sur ETH, retests FVG presque au seuil (PF 0.96, win 44.5 %)
  - Enseignements pour l'étape 4 : (1) le bracket fixe 2R/1R détruit le drift mesuré en feature study — tester une sortie temporelle (SL de sécurité + sortie à h≈16 bougies) ; (2) les frais dominent en 15m → moins de trades, mieux filtrés, envisager l'entrée en 1H ; (3) le score doit pénaliser l'extension (distance aux EMA/VWAP), pas récompenser l'alignement
- ✅ **Étape 4 — Variantes de sortie, score anti-extension, IS/OOS scellé, profils, walk-forward** (2026-09-01) :
  - `engine/confluence_backtest.py` : `ExitPolicy` — `bracket` (SL/TP, SL prioritaire) ou `time` (SL de sécurité seul + clôture h bougies plus tard, capture la dérive sans l'amputer)
  - `engine/confluence.py` : score v1 anti-extension (pénalité 12 pts si prix > 2×ATR au-delà de l'EMA rapide dans le sens du signal) ; profils déclaratifs `STRATEGY_PROFILES` (`confluence_v0`, `trend_v2`, `smc_v1`, `breakout_v1` — mêmes features, seules les gâchettes changent : `triggers`, `require_trend_aligned`, `require_structure`)
  - `engine/validation.py` : découpage IS/OOS AVANT tout réglage (IS = 90 j récents déjà diagnostiqués ; OOS = 90 j antérieurs jamais ouverts ; limite assumée : l'OOS précède l'IS, confirmation par walk-forward), warmup dédié par fenêtre, évaluation de variantes, walk-forward 60 j/30 j, cache local (`data/cache/`, non versionné) ; CLI `--stage is|oos|wf`
  - 12 nouveaux tests (233/233 passent)
  - **Verdict final : le moteur de confluence n'est PAS validé — rien ne part en production.** Les trois méthodes convergent :
    - *IS (réglages autorisés)* : tout négatif en 15m même brut corrigé de l'anti-extension ; sans frais, BTC time16 +0.16 R mais ETH ~0 ; frais ≈ 0.5 R/trade en 15m (risque 1-2.5×ATR trop petit vs 0.12 % aller-retour) ; SELL toxique partout (brut négatif) ; en 1H, ETH smc_v1 +0.26 R (n=66) → seul candidat, figé A PRIORI (1H = frais/risque ÷2, smc_v1 = seuls déclencheurs à dérive mesurée en étape 2) avec contrôle 15m
    - *OOS (un seul passage, scellé)* : ETH s'inverse (−0.28 R) — l'edge IS était un artefact de régime ; BTC 1H à l'équilibre (+0.016 R, n=82) ; contrôle 15m perd comme prédit (thèse des frais confirmée)
    - *Walk-forward 60 j/30 j (1H)* : BTC −0.16 R, ETH −0.29 R — négatif partout
  - **Décision** : `momentum_v1` reste l'unique stratégie en production, inchangée. Le socle technique (features anti-lookahead, simulateur, protocole de validation) est conservé pour de futures études — avec davantage d'historique (plusieurs régimes), un passage maker (frais ÷5) ou d'autres familles de déclencheurs. Règle respectée : ne jamais déployer sur la seule foi d'un backtest IS.

### Phase 32 — Fibonacci (2026-09-02, TERMINÉE — feature REJETÉE)

Objectif : tester l'incrément informationnel du retracement Fibonacci (bande 0.50-0.70) sur les retests d'Order Blocks. Spécification **scellée avant toute mesure** : `FIBONACCI.md` (règle non négociable : le Fib est une mesure, jamais un déclencheur ; production inchangée pendant toute la phase).

- ✅ **Étape 0** : `FIBONACCI.md` (conventions, impulsion qualifiée, cycle de vie, grades OB×bande, hypothèse unique, protocole, critère §7 figé)
- ✅ **Étape 1** : `engine/fibonacci.py` — `fib_states()` (snapshot Fib par bougie : unicité, retests par épisode, invalidation 1.0, expiration 200 bougies) + `ob_fib_grade()` (inclusion/overlap/proximity/none) ; propriété de préfixe testée — 244/244
- ✅ **Étape 2** : `engine/fib_study.py` — étude d'événements (jointure signaux `confluence_v0` × états Fib par index : groupes OB+Fib vs OB seul, forward returns ajustés du sens h=4/16/48, R brut/net de frais, buckets de profondeur, contrôles de spécificité sweep/bos/fvg_retest, critère §7 en fin de sortie) ; CLI `--stage is|oos` ; 267/267 tests
- **Verdict IS (4 runs du périmètre scellé, fenêtre utile 90 j)** :

  | Symbole×TF | OB+Fib (n) | OB seul (n) | delta mean h=16 | delta R net h=16 | n ≥ 30 |
  |---|---|---|---|---|---|
  | BTC 15m | 8 | 63 | +0.043 % | +0.843 R | NON |
  | ETH 15m | 7 | 60 | +0.847 % | +2.071 R | NON |
  | BTC 1H | 1 | 10 | −0.820 % | −1.395 R | NON |
  | ETH 1H | 4 | 10 | −0.806 % | +0.364 R | NON |

- **Décision : feature rejetée, OOS volontairement non consommé.** Le critère §7-1 (n ≥ 30 dans chaque groupe) échoue en IS sur les 4 couples avec un défaut massif (≤ 8 événements OB+Fib en 90 j) : la coïncidence OB retesté × Fib actif × même direction × grade inclusion/overlap est trop rare pour être mesurée sur ce périmètre. La validation exigeant le critère en IS **et** en OOS, l'OOS ne pouvait rien rattraper — le lancer n'aurait rien apporté. Les deltas 15m vont dans le sens de l'hypothèse (ETH +2.07 R net) mais sur 7-8 événements = bruit ; les deltas 1H sont majoritairement négatifs ; les contrôles de spécificité ne montrent pas d'effet bande cohérent. Production (`momentum_v1`) inchangée, code d'étude archivé dans `engine/fib_study.py` si l'hypothèse est re-testée un jour avec davantage d'historique.

### Reprise Phase 31 — confluence sur 4 ans multi-régimes (2026-09-02, REJET DÉFINITIF)

Piste « davantage d'historique » de la Phase 31, exécutée sans AUCUN nouveau réglage : finalistes figés (`smc_v1/bracket`, `smc_v1/time16`), 1H uniquement (le 15m est écarté d'office : frais structurellement rédhibitoires, leçon Phase 31), BTC + ETH sur **2022-08 → 2026-09** (35 758 bougies 1H par symbole, ~1 100 trades/variante/symbole). Réserve documentée : les ~195 derniers jours avaient déjà été consommés par l'étude initiale — le verdict se fonde sur les années anciennes jamais ouvertes et la stabilité d'ensemble.

- ✅ **Outil** : stage `regimes` dans `engine/validation.py` — découpage par année civile (`split_years`, warmup 20 j en préfixe), finalistes figés évalués par année en **brut** (frais=0) / **taker** (0.12 % A/R) / **maker** (0.02 % A/R) ; 301/301 tests
- **Résultats par année (expectancy brut, R)** :

  | Variante | 2022 | 2023 | 2024 | 2025 | 2026 |
  |---|---|---|---|---|---|
  | BTC bracket | −0.128 | +0.147 | +0.063 | −0.109 | +0.031 |
  | BTC time16 | −0.014 | +0.071 | +0.188 | +0.067 | −0.019 |
  | ETH bracket | −0.038 | +0.012 | −0.058 | +0.102 | +0.096 |
  | ETH time16 | −0.059 | −0.008 | −0.144 | +0.250 | +0.111 |

- **Lecture** : brut instable et proche de zéro partout ; en taker négatif **chaque année sauf une** (ETH time16 2025 : +0.144, n=254) ; en maker marginal et porté par une seule année (BTC time16 → 2024, ETH time16 → 2025). **Aucune année positive sur les deux symboles à la fois** : l'edge apparent se déplace avec le régime et le symbole — signature de bruit, pas de structure.
- **Walk-forward 60/30 j sur 4 ans (arbitre final)** : BTC n=567, expectancy **−0.129 R** (total −73 R, PF 0.82, DD 78 R) ; ETH n=573, expectancy **−0.103 R** (total −59 R, PF 0.86, DD 74 R). Avec ~570 trades de test par symbole, l'échantillon ne peut plus être invoqué.
- **Décision : rejet définitif du moteur de confluence.** Les trois pistes de la Phase 31 sont maintenant épuisées : plus d'historique (cette reprise) ✓ testé, frais maker ✓ testé (insuffisant : l'edge brut lui-même n'existe pas de façon stable). Reste « autres déclencheurs », non planifié. `momentum_v1` reste l'unique stratégie en production. Leçon : une expectancy positive sur 90 j et un symbole (ETH 2025-2026) peut être un pur artefact de régime — l'accord inter-symboles et inter-régimes est le vrai test.

### Audit momentum_v1 (production) sur 4 ans — 2026-09-02

Question posée par l'utilisateur (6 trades paper : 2 TP / 4 SL — échantillon non significatif, RR 1:2 ⇒ seuil de rentabilité à 34 % de win rate) : momentum_v1 est-elle bénéficiaire ? Le score filtre-t-il les mauvais trades ?

- ✅ **Outil** : `engine/momentum_study.py` — transitions en un seul passage O(n) (refactor `compute_series`/`evaluate_at` dans `engine/strategy.py`, parité exacte testée avec `evaluate_momentum_v1`), simulation via le simulateur commun (pyramiding 0, renversement, frais en R), sorties global/par année (brut/taker/maker)/par direction/par bucket de score, variantes `--side`/`--min-score`. 304/304 tests.
- **Résultats (15m, 2022-08 → 2026-09, n=2204 BTC / 2793 ETH trades)** :
  - momentum_v1 telle quelle : **négative chaque année sur les deux symboles en taker** (BTC −0.137 R, ETH −0.104 R d'expectancy) ; même brut ≈ 0 (BTC −0.017, ETH +0.016). Le problème n'est pas que les frais : il n'y a pas d'edge brut.
  - **Le score sépare réellement les trades** (intuition utilisateur validée) : bucket 45 = le seul positif en brut sur les DEUX symboles (BTC +0.06, ETH +0.095), buckets 28-38 négatifs partout. Mais non monotone (38 pire que 35, 55 ≤ 45) et taker reste négatif.
  - Variante `--min-score 45` : brut positif sur les deux (BTC +0.101/n=763, ETH +0.093/n=1010), maker positif, taker ≈ 0.
  - Variante `--side buy --min-score 45` : BTC positif même taker (+0.082 R, n=382) MAIS instable (2023-2025 positifs, 2022 et **2026 négatifs**) et ne se transfère pas à ETH (négatif). Sélection post-hoc = non déployable sans validation complémentaire.
- **Conclusion** : aucune variante simple de momentum_v1 n'est bénéficiaire après frais taker de façon robuste sur 4 ans. Le socle (score) a un vrai pouvoir séparateur partiel ; le filtre score ≥ 45 divise les signaux par ~3 et améliore la qualité brute — défendable pour un affichage « haute qualité », pas une promesse de profit. Production inchangée en attente de décision utilisateur.

### Option A — filtre qualité ENGINE_MIN_SCORE en production (2026-09-02, DÉCISION UTILISATEUR)

L'utilisateur a choisi l'option A : ne plus émettre que les signaux de score suffisant.

- **Nouvelle config** `ENGINE_MIN_SCORE` (défaut 0 = comportement historique) : score minimal pour qu'une transition soit ÉMISE. Valeur retenue : **45** (seul bucket positif en brut sur BTC ET ETH dans l'audit ci-dessus).
- **Sémantique** (identique à l'étude `momentum_study.py`) : le filtre s'applique AVANT `would_fill` — une transition filtrée n'est ni émise ni ouverte en simulation, donc un signal postérieur de meilleure qualité **dans le même sens** restera émissible. Le filtre s'applique aussi à `replay_history` (reconstruction au démarrage) pour que l'état simulé corresponde à ce qui aurait été émis.
- **Code** : `total_score()` extrait dans `engine/strategy.py` ; filtre 3bis dans `engine/runner.py` ; paramètre `min_score` dans `engine/position.py::replay_history` ; `.env.example` documenté. 308/308 tests (nouveaux : sous le seuil → non émis ni ouvert ; au seuil → émis ; seuil 0 → tout émis ; replay filtré).
- **Attente raisonnable** : ~3× moins de signaux, expectancy brute améliorée (BTC +0.101 R, ETH +0.093 R en brut sur 4 ans avec ce filtre) mais ≈ 0 après frais taker — c'est un filtre de **qualité d'affichage**, pas une promesse de rentabilité.
- **Déploiement** : ajouter `ENGINE_MIN_SCORE=45` au `.env` du VPS puis reconstruire le conteneur `engine`. **FAIT le 2026-09-02** (logs vérifiés : `min_score=45` chargé, état simulé reconstruit, boucle de polling saine).

### Audit momentum_v1 sur 1H — 2026-09-03, REJET (idée d'un 2e moteur en parallèle)

Question posée par l'utilisateur : garder le 15m **et** ajouter un moteur 1H pour tester les deux en paper trading. Réponse par l'audit d'abord (`python -m engine.momentum_study --timeframe 60 --days 1490`, BTC+ETH, 35 758 bougies chacun) — momentum_v1 n'avait jamais été audité en 1H :

- **BTC 1H** (n=989) : brut +0.033 R, taker **−0.087 R**. **ETH 1H** (n=1166) : brut +0.004 R, taker **−0.116 R**.
- Bucket score 45 : BTC brut +0.116 R / taker −0.004 R (équilibre) ; ETH brut +0.051 R / taker **−0.069 R** → pas d'accord inter-symboles après frais.
- ETH SELL toxique (−0.198 R taker) ; le bucket score 55 est **mauvais** en 1H (BTC −0.16 R brut, win 28 %) — le top score n'y rime plus avec qualité.
- **Décision utilisateur : ne pas déployer le 1H, garder la config actuelle** (15m + filtre 45). Le paper trading 1H aurait re-mesuré un résultat déjà connu. Le problème de fond (frais vs taille du risque) est identique sur les deux timeframes ; pistes restantes : SL plus large par trade, autres déclencheurs.

### Jour et heure des trades dans les embeds (2026-09-03, DÉCISION UTILISATEUR)

Demande : afficher le **jour et l'heure** des trades dans les embeds de clôture TP/SL et le récap hebdo, en **heure de Paris** (destination inchangée : salon récap).

- `format_day_time()` dans `app/discord/embeds.py` : "lundi 31/08 20:00" (UTC → Europe/Paris, heure d'été/hiver gérée par `ZoneInfo`, datetimes naïfs SQLite traités comme UTC). `WEEKDAY_LABELS` déplacé d'`app/services/weekly_recap.py` vers `embeds.py` (plus de duplication).
- Embed de clôture (`build_closure_embed`) : nouveaux champs « Ouvert le » et « Clôturé le » — `CloseOutcome` (`app/paper_trading/engine.py`) porte désormais `opened_at`/`closed_at` remplis depuis la position en base.
- Récap hebdo (`build_weekly_recap_embed`) : « Nouvelles positions » affiche `ouvert le {jour+heure}` ; « Clôturées » affiche `{ouverture} → {clôture}` ; « En cours » passe de `dd/mm HH:MM UTC` au jour + heure de Paris. Footer mentionne l'heure de Paris.
- 313/313 tests (nouveaux : `format_day_time` été/hiver/naïf, champs dates de l'embed de clôture, assertions jour+heure dans le récap et la notification de clôture).

Pistes futures hors confluence (à ne faire que sur demande explicite) : ajouter des symboles/timeframes (`ENGINE_*` dans `.env`, zéro code), prendre TradingView payant et brancher l'alerte réelle (Phase 16, runbook conservé ci-dessous).

## 👤 Ce qu'il ME reste à faire (de ton côté)

Rien de bloquant pour les phases 28-30 ✅ (app Discord créée, bot invité, Docker installé et fonctionnel, `.env` rempli). Les actions ci-dessous ne bloquent pas non plus la suite : elles concernent les **alertes réelles** et la **vérification manuelle des stratégies**.

### ✅ 1. Vérifier les stratégies dans TradingView (Phases 14-15, 25, 26) — FAIT (2026-08-25, sur XAUUSD OANDA)

**momentum_v1** : collé dans le Pine Editor, affichage et Strategy Tester vérifiés —
chaque entrée a sa sortie, SL sous l'entrée / TP au-dessus (LONG), SHORT inversé, frais
et slippage actifs, aucune entrée orpheline. Deux points relevés et **tranchés** :
- *Sorties hors SL/TP* : une position peut être fermée au marché par un signal opposé
  (inversion de tendance). **Option A retenue** (garder) — décision documentée dans
  l'en-tête de `pine/momentum_v1.pine`.
- *SL/TP vs prix d'exécution réel* : SL/TP sont calculés depuis le `close` de la bougie
  de signal ; le seul écart vient du slippage 2 ticks du backtest (volontaire, réalisme).

**momentum_mtf_v1** : graphique XAUUSD OANDA **15m** — compile, s'affiche, **aucun
triangle contre le fond 4H** (règle de confluence Phase 25), List of Trades vérifiée.
Anti-repainting validé par **audit statique du code** (le Replay complet est payant) :
combo sûr `expression[1]` + `lookahead_on` sur les deux `request.security` (4H et 1H,
dernière bougie fermée uniquement), `calc_on_every_tick=false`,
`process_orders_on_close=true` — comportement identique historique/temps réel.
**Test définitif en Phase 16** : comparaison alertes réelles vs triangles historiques.

**Score (Phase 26)** : à vérifier via l'alerte de test en Phase 16 (champs `score_*`
dans le JSON `{{strategy.order.alert_message}}`).

### ✅ 2. Phase 17 — Serveur EN LIGNE depuis le 2026-08-29

**VPS OVH VPS-1 2027** (2 vCore / 4 GB RAM / 40 GB NVMe, Gravelines France,
**Ubuntu 26.04 LTS**, 4,49 €HT/mois, backup automatisé offert — complète la procédure
`pg_dump` de la Phase 29). **Domaine** : `bot-td.duckdns.org` → IP du VPS.

**Installation réalisée (2026-08-29)** :
- Étape A ✅ : IP VPS récupérée, SSH `ubuntu` testé, DuckDNS pointé sur le VPS, clé SSH ed25519 installée (`ssh-copy-id`)
- Étape B ✅ : `apt upgrade`, SSH durci (clé uniquement, `PasswordAuthentication no`, `PermitRootLogin no` — fichier `50-cloud-init.conf` neutralisé), UFW (22 + 443 seulement), Docker 29.1.3 + compose 2.40.3 (paquets Ubuntu), dépôt cloné via **deploy key GitHub en lecture seule** (`vps-bot-td`), `.env` production (POSTGRES_PASSWORD généré sur le VPS), pile démarrée
- Vérifications e2e ✅ : `/health` → 200 ; mauvais secret → 401 ; signal complet → `sent` + embed Discord ; doublon → `duplicate` (non re-stocké) ; `/status` OK ; certificat Let's Encrypt obtenu (renouvellement auto) ; signal de test supprimé de la base
- Déploiement GitHub à jour : `origin/main` poussé (5a787f4 → 0afa0d6)

**Commandes serveur utiles** (depuis le PC, `ssh ubuntu@IP`) :
```bash
cd ~/bot-td && docker compose -f docker-compose.prod.yml ps      # état des conteneurs
docker compose -f docker-compose.prod.yml logs -f app            # logs de l'app
git pull && docker compose -f docker-compose.prod.yml up -d --build   # mise à jour
```

**Déjà prêt côté dépôt** (commit Phase 17) : `Dockerfile.caddy` (Caddy + plugin DuckDNS,
build vérifié), `Caddyfile` (HTTPS Let's Encrypt automatique par DNS challenge — port 80
inutile), `docker-compose.prod.yml` (**seul le port 443 exposé sur Internet** ; l'API
reste interne au réseau Docker et PostgreSQL n'est plus publié ; mot de passe DB lu
dans `.env` via `POSTGRES_PASSWORD`), `.env.example` enrichi (`DOMAIN`, `DUCKDNS_TOKEN`,
`POSTGRES_PASSWORD`). Tests 125/125 inchangés.

#### Étape A — Réception du VPS (👤 FAIT le 2026-08-29)

1. **Récupérer l'IPv4 publique** : e-mail de livraison OVH, ou espace client
   *Bare Metal Cloud → VPS → ton serveur*. La noter.
2. **Tester la connexion SSH** depuis Git Bash (répondre `yes` à la question de
   fingerprint au premier accès) :
   ```bash
   ssh ubuntu@IP_DU_VPS        # utilisateur standard des images Ubuntu OVH
   # si refus :
   ssh root@IP_DU_VPS          # avec le mot de passe reçu par e-mail
   ```
   Noter **le login qui fonctionne** (`ubuntu` ou `root`, clé SSH ou mot de passe).
3. **Pointer le domaine sur le VPS** (remplace l'IP de ta box actuelle) :
   ```bash
   curl "https://www.duckdns.org/update?domains=bot-td&token=LE_TOKEN_DU_ENV_LOCAL&ip=IP_DU_VPS"
   ```
   Doit répondre `OK` (alternative : coller l'IP à la main sur duckdns.org).

#### Étape B — Installation serveur (🔧 FAIT le 2026-08-29 avec Claude)

Dans l'ordre, Claude guide chaque commande :
1. Mises à jour : `apt update && apt upgrade`
2. Durcissement SSH : connexion par clé uniquement, mot de passe désactivé
3. Pare-feu UFW : uniquement SSH (22) et HTTPS (443)
4. Installation Docker + plugin compose
5. Récupération du dépôt (clone Git) sur le serveur
6. Création du `.env` **de production** sur le VPS : tous les secrets habituels
   (Discord, `TRADINGVIEW_WEBHOOK_SECRET`...) + `DOMAIN=bot-td.duckdns.org`,
   `DUCKDNS_TOKEN=...`, `POSTGRES_PASSWORD=<mot de passe fort>`
7. Démarrage : `docker compose -f docker-compose.prod.yml up -d --build`
8. Vérifications : `https://bot-td.duckdns.org/health` → 200 ; webhook sans secret → 401 ;
   bot Discord connecté (`/status` dans Discord)

#### Étape C — Créer l'alerte TradingView réelle (OPTIONNELLE — remplacée par le moteur local)

URL du webhook à renseigner dans TradingView :
`https://bot-td.duckdns.org/webhook/tradingview`
(Instructions pas à pas détaillées fournies quand le serveur sera en ligne.)

### ⏳ 3. Phase 16 — créer l'alerte TradingView réelle (optionnelle, si abonnement TradingView un jour)
1. Suivant la vérification ci-dessus : stratégie **Momentum V1** ou **Momentum MTF V1** sur le graphique
2. Renseigner le **Secret webhook** dans les paramètres de la stratégie (valeur `TRADINGVIEW_WEBHOOK_SECRET` du `.env`)
3. Créer l'alerte : condition **la stratégie**, type **Order fills**, message `{{strategy.order.alert_message}}`, **Once Per Bar Close**
4. URL du webhook : `https://bot-td.duckdns.org/webhook/tradingview` (nécessite la Phase 17 étapes A+B ci-dessus)

*(Instructions détaillées pas à pas le moment venu)*

## 📌 Rappels importants

- Le système **ne passe jamais d'ordre** : signalisation uniquement
- Les secrets vont **uniquement** dans `.env` (jamais commités, jamais montrés)
- Commandes utiles : `pytest` (tests), `uvicorn app.main:app --reload --loop app.main:selector_loop` (API + bot, Windows)
- Tester le webhook localement :
  ```bash
  curl -X POST http://localhost:8000/webhook/tradingview -H "Content-Type: application/json" \
    -d '{"secret":"MON_SECRET","strategy":"momentum_v1","symbol":"BTCUSDT","exchange":"BINANCE","timeframe":"15","action":"BUY","price":104532.42,"stop_loss":103800,"take_profit":106000,"timestamp":"<heure UTC actuelle>"}'
  ```
- Vérification manuelle Phase 26 : même curl avec `"score_trend":"20","score_momentum":"10","score_macd":"8"` en plus → l'embed Discord doit afficher **Signal Score : 38/100** (et rien si les composantes sont omises) ; Pine : recoller `pine/momentum_v1.pine` / `pine/momentum_mtf_v1.pine` mis à jour dans TradingView (alert_message enrichi du score)
