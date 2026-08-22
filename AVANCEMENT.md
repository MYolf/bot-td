# Avancement du projet

> Fichier mis à jour au fil des phases. ✅ = fait, 🔧 = en cours, ⏳ = à faire, 👤 = à faire de TON côté.

## ✅ Terminé (par Claude)

- **Phase 0 — Préparation**
  - Ancien code Node.js archivé dans la branche `archive/nodejs-scaffold` et retiré de `main`
  - `.gitignore` (Python + secrets), `.env.example`, `README.md` créés
- **Phase 1 — Configuration**
  - `app/config/settings.py` : configuration typée, secrets obligatoires, listes blanches configurables
  - `requirements.txt`, environnement virtuel `.venv` créé et installé
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
- **Tests : 25/25 passent** (`pytest`)

## 🔧 En cours / à venir (par Claude)

- **Phase 2-3** — Bot Discord (`app/discord/bot.py`) + commande `/status` → *attend que tu crées l'app Discord*
- **Phase 10-11** — Déduplication + PostgreSQL → *attend que tu installes Docker*
- **Phase 12-13** — Envoi Discord (embeds) + pipeline complet
- **Phase 14+** — Pine Script, paper trading, stats, production

## 👤 Ce qu'il ME reste à faire (de ton côté)

### 1. Créer l'application Discord (pour la Phase 2-3) ⏳
1. Ouvrir https://discord.com/developers/applications et se connecter
2. **New Application** → nommer `bot-td-signals` → **Create**
3. Onglet **Bot** → **Reset Token** → **Copy** (c'est le `DISCORD_BOT_TOKEN` — ne le donner à personne)
4. Désactiver **Public Bot** (recommandé). Ne jamais activer Administrator
5. **OAuth2 → URL Generator** : cocher scopes `bot` + `applications.commands`
6. Permissions : uniquement `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`
7. Ouvrir l'URL générée → inviter le bot sur mon serveur
8. Discord : activer le **mode développeur** (Paramètres → Avancé)
9. Copier l'ID du **serveur** (`DISCORD_GUILD_ID`) et l'ID du **salon signaux** (`DISCORD_SIGNALS_CHANNEL_ID`)

### 2. Installer Docker Desktop (pour la Phase 10-11) ⏳
1. Télécharger https://www.docker.com/products/docker-desktop/ (Docker Desktop for Windows)
2. Installer avec l'option **WSL 2** → redémarrer le PC si demandé
3. Ouvrir Docker Desktop et attendre "Docker Desktop is running"

### 3. Remplir mon fichier `.env` (après les étapes 1 et 2) ⏳
- Copier `.env.example` en `.env` et remplir les valeurs (ne JAMAIS le commité)

## 📌 Rappels importants

- Le système **ne passe jamais d'ordre** : signalisation uniquement
- Les secrets vont **uniquement** dans `.env` (jamais commités, jamais montrés)
- Commandes utiles : `pytest` (tests), `uvicorn app.main:app --reload` (API)
- Tester le webhook localement :
  ```bash
  curl -X POST http://localhost:8000/webhook/tradingview -H "Content-Type: application/json" \
    -d '{"secret":"MON_SECRET","strategy":"momentum_v1","symbol":"BTCUSDT","exchange":"BINANCE","timeframe":"15","action":"BUY","price":104532.42,"stop_loss":103800,"take_profit":106000,"timestamp":"<heure UTC actuelle>"}'
  ```
