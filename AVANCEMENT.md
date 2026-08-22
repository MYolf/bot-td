# Avancement du projet

> Fichier mis à jour au fil des phases. ✅ = fait, 🔧 = en cours, ⏳ = à faire, 👤 = à faire de TON côté.

## ✅ Terminé (par Claude)

- **Phase 0 — Préparation**
  - Ancien code Node.js archivé dans la branche `archive/nodejs-scaffold` et retiré de `main`
  - `.gitignore` (Python + secrets), `.env.example`, `README.md` créés
- **Phase 1 — Configuration**
  - `app/config/settings.py` : configuration typée, secrets obligatoires, listes blanches configurables
  - `requirements.txt`, environnement virtuel `.venv` créé et installé
  - Tests : **4/4 passent** (`pytest`)

## ⏳ En cours / à venir (par Claude)

- **Phase 2-3** — Bot Discord (`app/discord/bot.py`) + commande `/status` → *après que tu aies créé l'app Discord*
- **Phase 4-13** — FastAPI, webhook TradingView, validation, déduplication, PostgreSQL, publication Discord
- **Phase 14+** — Pine Script, paper trading, stats, Docker, production

## 👤 Ce qu'il ME reste à faire (de ton côté)

### 1. Créer l'application Discord (pour la Phase 2) ⏳
1. Ouvrir https://discord.com/developers/applications et se connecter
2. **New Application** → nommer `bot-td-signals` → **Create**
3. Onglet **Bot** → **Reset Token** → **Copy** (c'est le `DISCORD_BOT_TOKEN` — ne le donner à personne)
4. Désactiver **Public Bot** (recommandé). Ne pas activer Administrator
5. **OAuth2 → URL Generator** : cocher scopes `bot` + `applications.commands`
6. Permissions : uniquement `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`
7. Ouvrir l'URL générée → inviter le bot sur mon serveur
8. Discord : activer le **mode développeur** (Paramètres → Avancé)
9. Copier l'ID du **serveur** (`DISCORD_GUILD_ID`) et l'ID du **salon signaux** (`DISCORD_SIGNALS_CHANNEL_ID`)

### 2. Installer Docker Desktop (pour la Phase 11 — PostgreSQL) ⏳
1. Télécharger https://www.docker.com/products/docker-desktop/ (Docker Desktop for Windows)
2. Installer avec l'option **WSL 2** → redémarrer le PC si demandé
3. Ouvrir Docker Desktop et attendre "Docker Desktop is running"

### 3. Remplir mon fichier `.env` (après l'étape 1) ⏳
- Copier `.env.example` en `.env` et remplir les valeurs (sans jamais le commité)

## 📌 Rappels importants

- Le système **ne passe jamais d'ordre** : signalisation uniquement
- Les secrets vont **uniquement** dans `.env` (jamais commités, jamais montrés)
- Commandes utiles : `pytest` (tests), `uvicorn app.main:app --reload` (API, à venir)
