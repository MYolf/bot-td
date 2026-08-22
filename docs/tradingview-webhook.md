# Configurer le webhook TradingView → bot-td

## 1. Créer l'alerte dans TradingView

1. Ouvre ton graphique (ex. BTCUSDT) et ta stratégie/indicateur.
2. Crée une **alerte** (Alt+A) sur la condition de ton choix (croisement d'EMA, signal de stratégie `strategy.entry`, RSI, etc.).

## 2. Message de l'alerte

Dans le champ **Message** de l'alerte, coller un JSON de cette forme (les valeurs entre `{{ }}` sont des placeholders TradingView) :

```json
{
  "secret": "TON_WEBHOOK_SECRET",
  "symbol": "{{ticker}}",
  "action": "buy",
  "price": {{close}},
  "stopLoss": {{plot("SL")}},
  "takeProfit": {{plot("TP")}},
  "timeframe": "{{interval}}",
  "strategy": "EMA_cross_RSI"
}
```

Notes :
- `secret` doit correspondre à `WEBHOOK_SECRET` dans ton `.env` (sinon le header `X-Webhook-Secret` peut être utilisé à la place dans les outils qui le permettent).
- `plot("SL")` / `plot("TP")` fonctionnent si ton script Pine expose ces plots ; sinon, mets des valeurs fixes ou supprime les champs.
- `action` : `buy` ou `sell` (insensible à la casse).

## 3. Webhook URL

Dans la section **Notifications** de l'alerte, activer **Webhook URL** et mettre :

```
https://TON-DOMAINE/webhook/tradingview
```

TradingView exige une URL publique en HTTPS (pas de localhost). Solutions :
- Cloudflare Tunnel (`cloudflared tunnel --url http://localhost:3000`)
- ngrok (pour les tests)
- Serveur/VPS avec reverse proxy (nginx, Caddy)

## 4. IPs TradingView (optionnel)

TradingView envoie ses webhooks depuis des IPs listées dans leur documentation. Tu peux restreindre l'accès à cette liste dans ton pare-feu / reverse proxy pour renforcer la sécurité.

## 5. Tester

Depuis un terminal :

```bash
curl -X POST http://localhost:3000/webhook/tradingview \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: ton_secret" \
  -d '{"symbol":"XAUUSD","action":"SELL","price":2400.5,"stopLoss":2420,"takeProfit":2360,"timeframe":"4h","strategy":"gold_breakout"}'
```

Réponse attendue : `{"received":true,"id":"..."}` et un embed publié dans le salon Discord.
