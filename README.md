# bot-td — Bot Discord de signaux de trading (TradingView)

Bot qui reçoit les signaux détectés par **TradingView** (stratégies + indicateurs sur Bitcoin, or, etc.) via webhook, les valide, les enregistre, puis les publie dans un salon Discord avec un embed clair (action BUY/SELL, entrée, Stop Loss, Take Profit, timeframe, stratégie).

> **Important** : ce bot **ne passe jamais d'ordre**. Il sert uniquement à générer, transmettre et analyser des signaux afin que tu décides toi-même de prendre ou non le trade.

## Architecture

```
TradingView (alerte/stratégie)
        │  POST JSON (secret partagé)
        ▼
Express webhook (/webhook/tradingview)   ── src/server.js
        │  validation du payload         ── src/validate.js
        │  sauvegarde locale             ── src/store.js  (data/signals.json)
        ▼
Bot Discord (embed dans le salon)        ── src/discord.js + src/embeds.js
```

## Structure du projet

```
bot-td/
├── src/
│   ├── index.js        # Point d'entrée : démarre Discord + le serveur webhook
│   ├── config.js       # Lecture des variables d'environnement (.env)
│   ├── server.js       # Serveur Express + route webhook TradingView
│   ├── validate.js     # Validation des signaux reçus
│   ├── store.js        # Persistance locale des signaux (JSON)
│   ├── embeds.js       # Construction de l'embed Discord
│   └── discord.js      # Client Discord + publication des signaux
├── docs/
│   └── tradingview-webhook.md   # Guide de configuration côté TradingView
├── data/               # Signaux enregistrés (gitignoré)
├── .env.example        # Modèle de configuration
└── package.json
```

## Installation

```bash
cd bot-td
npm install
cp .env.example .env   # puis remplir DISCORD_TOKEN, DISCORD_CHANNEL_ID, WEBHOOK_SECRET
```

## Démarrage

```bash
npm start       # production
npm run dev     # développement (redémarrage auto)
```

## Test rapide (sans TradingView)

```bash
curl -X POST http://localhost:3000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: ton_secret" \
  -d '{"symbol":"BTCUSDT","action":"BUY","price":65000,"stopLoss":64000,"takeProfit":67000,"timeframe":"1h","strategy":"EMA_RSI"}'
```

## Étapes de configuration

1. Créer une application bot sur le [Discord Developer Portal](https://discord.com/developers/applications), récupérer le token et inviter le bot sur ton serveur.
2. Récupérer l'ID du salon où publier (mode développeur Discord → clic droit sur le salon → Copier l'identifiant).
3. Remplir `.env`.
4. Configurer l'alerte TradingView → voir `docs/tradingview-webhook.md`.
5. Exposer le serveur en HTTPS (le webhook TradingView exige une URL publique) — par exemple via un reverse proxy (nginx, Caddy, Cloudflare Tunnel…).

## Avertissement

Ce projet est un outil d'information. Les signaux ne constituent pas un conseil financier. Aucune exécution d'ordre n'est effectuée par le bot.
