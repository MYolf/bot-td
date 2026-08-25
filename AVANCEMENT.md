# Avancement du projet

> Fichier mis à jour au fil des phases. ✅ = fait, 🔧 = en cours, ⏳ = à faire, 👤 = à faire de TON côté.

## 📍 Point d'avancement (2026-08-25)

- **Phases terminées** : 0-15, 18-27 (28 phases sur 30)
- **Tests** : **125/125 passent** (`pytest`)
- **Reste à faire (Claude)** : Phase 28 (Docker), Phase 29 (migrations), Phase 30 (production)
- **Reste à faire (toi)** : Phase 16-17 (alertes TradingView réelles + serveur HTTPS public) et vérifications manuelles dans TradingView — voir la section 👤 ci-dessous
- Pipeline opérationnel en local : TradingView (JSON) → FastAPI → PostgreSQL → Discord, avec déduplication, paper trading en R, score de qualité et 5 commandes slash

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

## 🔧 En cours / à venir (par Claude)

- **Phase 28 — Docker** : Dockerfile de l'application + docker-compose complet (app + PostgreSQL)
- **Phase 29 — Migrations** : procédure de migration de base (backup, upgrade, rollback)
- **Phase 30 — Production** : durcissement final, déploiement, supervision
- **Phase 16-17** — Alertes TradingView réelles + connexion (👤 nécessite des actions de ta part : coller la stratégie dans TradingView, créer l'alerte, serveur HTTPS public)

## 👤 Ce qu'il ME reste à faire (de ton côté)

Rien de bloquant pour les phases 28-30 ✅ (app Discord créée, bot invité, Docker installé et fonctionnel, `.env` rempli). Les actions ci-dessous ne bloquent pas non plus la suite : elles concernent les **alertes réelles** et la **vérification manuelle des stratégies**.

### ⏳ 1. Vérifier les stratégies dans TradingView (Phases 14-15, 25, 26)

**momentum_v1** (simple, TF unique) :
1. Ouvrir TradingView → Pine Editor → coller le contenu de `pine/momentum_v1.pine` (version à jour : alert_message inclut le score) → **Add to chart**
2. Symbole : BTCUSDT (BINANCE), timeframe 15m ou 60m
3. Strategy Tester : backtest avec frais (déjà actifs), vérifier que chaque entrée a sa sortie (SL/TP)

**momentum_mtf_v1** (multi-timeframes) :
1. Coller `pine/momentum_mtf_v1.pine` → **Add to chart**
2. Symbole BTCUSDT (BINANCE), **timeframe du graphique : 15m** (le TF d'entrée ; 4H et 1H se règlent dans les paramètres de la stratégie)
3. Vérifier que le script compile sans erreur et s'affiche (fond vert/rouge = tendance 4H, triangles = signaux)
4. Vérifier visuellement : **aucun triangle ne va contre le fond** (règle de confluence de la Phase 25)
5. Strategy Tester : backtest avec frais ; tester en Replay : les signaux historiques ne doivent **ni disparaître ni se déplacer** (anti-repainting)
6. Si alerte réelle souhaitée : `"momentum_mtf_v1"` est déjà dans la liste blanche par défaut (rien à faire sauf si tu as surchargé `ALLOWED_STRATEGIES`)

**Score (Phase 26)** : dans les deux Strategy Testers, le JSON d'alerte contient désormais `"score_trend"`, `"score_momentum"`, `"score_macd"` (+ `"score_htf"` pour MTF) — vérifiable via une alerte de test.

### ⏳ 2. Phase 16 — créer l'alerte TradingView réelle
1. Suivant la vérification ci-dessus : stratégie **Momentum V1** ou **Momentum MTF V1** sur le graphique
2. Renseigner le **Secret webhook** dans les paramètres de la stratégie (valeur `TRADINGVIEW_WEBHOOK_SECRET` du `.env`)
3. Créer l'alerte : condition **la stratégie**, type **Order fills**, message `{{strategy.order.alert_message}}`, **Once Per Bar Close**
4. URL du webhook : `https://TON_DOMAINE/webhook/tradingview` (⚠️ nécessite un serveur accessible en HTTPS — voir Phase 17)

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
