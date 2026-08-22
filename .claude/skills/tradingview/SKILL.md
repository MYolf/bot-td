---
name: tradingview
description: >-
  Intégration TradingView : alertes, webhooks sortants, payload JSON avec
  placeholders, sécurité du canal TradingView -> backend, contraintes et
  fiabilité. Utiliser ce skill dès qu'on travaille sur le webhook entrant
  POST /webhook/tradingview, sur le format des alertes, le secret partagé,
  les timestamps des signaux ou les erreurs de communication TradingView.
---

# TradingView — Alertes et Webhooks

## Rôle dans ce projet

TradingView est le **seul moteur d'analyse et de génération de signaux**. Le
backend ne fait que **recevoir** : il n'interroge jamais TradingView, il ne
récupère jamais de prix de marché lui-même. Le flux est unidirectionnel :

```text
Pine Strategy -> Alert TradingView -> HTTPS POST -> FastAPI
```

## Fonctionnement des alertes

- Une alerte est créée dans TradingView sur une condition (stratégie ou
  indicateur) pour un symbole et un timeframe donnés.
- Le déclenchement dépend du paramètre de l'alerte : `Once Per Bar Close`
  (recommandé dans ce projet) évite les signaux intra-bougie non confirmés.
- Une alerte peut avoir **une seule action webhook** : URL + message.
- `{{placeholder}}` sont substitués par TradingView au moment du déclenchement.

## Placeholders utiles

| Placeholder    | Valeur                              |
|----------------|-------------------------------------|
| `{{ticker}}`   | Symbole (ex. `BTCUSDT`)             |
| `{{exchange}}` | Exchange (ex. `BINANCE`)            |
| `{{interval}}` | Timeframe (ex. `15`, `240`, `D`)    |
| `{{close}}`    | Prix de clôture                     |
| `{{time}}`     | Heure d'ouverture de la bougie (UTC)|
| `{{timenow}}`  | Heure de déclenchement (UTC)        |

Les valeurs numériques arrivent **sous forme de chaînes** : `price` doit être
accepté en `str` côté Pydantic puis converti et validé (`> 0`, décimal).

## Payload JSON de référence

```json
{
  "secret": "YOUR_SECRET",
  "strategy": "momentum_v1",
  "symbol": "{{ticker}}",
  "exchange": "{{exchange}}",
  "timeframe": "{{interval}}",
  "action": "BUY",
  "price": "{{close}}",
  "stop_loss": "103800.00",
  "take_profit": "106000.00",
  "timestamp": "{{timenow}}"
}
```

Le message de l'alerte doit être du JSON valide sur une seule action webhook.
SL/TP sont soit calculés en Pine et passés dans le message, soit dérivés du
signal côté backend — mais toujours **vérifiés côté backend** (jamais de
confiance aveugle dans ce que le webhook envoie).

## Contraintes techniques des webhooks TradingView

- **Requête sortante uniquement** : TradingView appelle notre endpoint. Le
  backend doit donc être joignable en HTTPS public (pas de tunnel temporaire
  en production).
- **Pas d'en-têtes d'authentification personnalisables** : le secret est
  transmis **dans le corps JSON**. C'est la raison du champ `secret`.
- **Pas de garantie de livraison exactly-once** : TradingView peut renvoyer
  une alerte. Le backend DOIT être idempotent (voir skill `signal-engine`,
  déduplication par `signal_uid`).
- **Timeouts côté TradingView** : le endpoint doit répondre **rapidement**
  (< quelques secondes). Les traitements lents (notifications, statistiques)
  doivent être découplés de la réponse HTTP.
- **Code de réponse attendu 2xx** : sinon TradingView marque l'alerte en
  échec. Un signal invalide doit donc répondre **2xx avec un statut métier**
  dans le corps (rejet loggué en base), pas une erreur 4xx/5xx, sauf pour
  l'authentification (401) et le JSON illisible.
- **Fréquence limite** : ne pas compter sur un volume élevé d'alertes/seconde ;
  prévoir tout de même un rate limiting côté backend.

## Sécurité du canal

1. **HTTPS obligatoire** en production (domaine réel, certificat valide).
2. Secret partagé long et aléatoire (32+ caractères), stocké dans `.env`
   (`TRADINGVIEW_WEBHOOK_SECRET`), remplaçable **sans modifier le code**.
3. Secret comparé en **temps constant** (`secrets.compare_digest`).
4. Ne **jamais** logger le secret ni le retourner dans une réponse d'erreur.
5. En cas de secret invalide : `401`, réponse générique, log minimal (IP,
   horodatage) sans la valeur reçue.
6. Optionnel durcissement : liste blanche d'IP sortantes TradingView, rate
   limiting par IP.

## Timestamps

- `{{timenow}}` et `{{time}}` sont en UTC. Toujours stocker en UTC
  (timestamptz) et faire les calculs en UTC.
- Le backend doit rejeter les signaux trop anciens (fenêtre configurable,
  ex. quelques minutes) pour éviter les rejeux d'alertes anciennes.
- Tolérance de dérive d'horloge : comparer à l'horloge du serveur avec une
  marge, pas à la seconde près.
- L'identifiant de déduplication utilise le **timestamp de bougie**
  (arrondi au timeframe), pas le timestamp de réception.

## Gestion des erreurs

| Situation                      | Réponse backend                   |
|--------------------------------|-----------------------------------|
| Secret invalide/absent         | 401, générique, loggé             |
| JSON illisible                 | 400, loggé                        |
| Signal invalide (règles métier)| 200 + statut `REJECTED`, loggé    |
| Doublon                        | 200 + statut `DUPLICATE`          |
| Backend partiellement en panne | Ne jamais répondre 500 silencieusement : logguer, compter, alerter via le salon de logs Discord |

## Critères de validation

- [ ] Le endpoint répond en moins de quelques secondes.
- [ ] Secret invalide => 401 sans fuite d'information.
- [ ] Un même alerte renvoyée par TradingView ne produit qu'une seule
      notification Discord.
- [ ] Les timestamps sont traités en UTC avec tolérance de dérive.
- [ ] Aucun secret dans les logs ni les réponses.
