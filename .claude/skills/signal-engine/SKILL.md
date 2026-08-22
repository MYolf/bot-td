---
name: signal-engine
description: >-
  Cœur métier du traitement des signaux : pipeline webhook (authentification,
  normalisation, validation, cohérence, déduplication), schéma du signal,
  calcul du Risk/Reward, statuts, persistance, notification Discord et logs.
  Utiliser ce skill dès qu'on travaille sur app/signals/, app/services/,
  la logique métier des signaux, la déduplication ou le paper trading.
---

# Signal Engine — Pipeline de traitement des signaux

## Règle absolue rappelée

Ce pipeline traite de **l'information**. Il ne contient aucune logique
d'exécution : pas d'ordre, pas de broker, pas d'exchange, pas de clé API de
trading. Il s'arrête à : signal stocké + notifié dans Discord.

## Pipeline obligatoire (ordre strict)

```text
1. RECEPTION     POST /webhook/tradingview (couche HTTP : voir skill fastapi)
2. AUTHENT       vérification du secret (temps constant) -> 401 si invalide
3. NORMALISATION str -> Decimal, symboles uppercase, timestamp -> UTC
4. VALIDATION    listes blanches (strategy, symbol, exchange, timeframe)
                 champs obligatoires, price/SL/TP > 0, fraîcheur du timestamp
5. COHERENCE     BUY : SL < entry < TP ; SELL : TP < entry < SL
6. DEDUPLICATION signal_uid unique en base -> DUPLICATE si déjà présent
7. PERSISTENCE   insertion PostgreSQL (statut VALIDATED)
8. NOTIFICATION  embed Discord (statut SENT), message_id stocké
```

**Invariants** :

- Un signal dupliqué ne produit **jamais** deux notifications Discord.
- La persistance précède la notification (un signal non stocké n'est pas
  notifié ; si Discord échoue, le signal reste en base avec statut `ERROR`
  et peut être re-notifié manuellement).
- Chaque rejet est loggé avec une **raison structurée** (`reason=...`).

## Schéma du signal (TradingViewSignal)

```text
secret        str    (retiré immédiatement après authentification, jamais loggé)
strategy      str    non vide, dans la liste blanche
symbol        str    non vide, uppercase, liste blanche (BTCUSDT, ETHUSDT, XAUUSD...)
exchange      str    non vide, liste blanche
timeframe     str    non vide, liste blanche (5, 15, 30, 60, 240, D)
action        enum   BUY | SELL (rien d'autre)
price         Decimal > 0 (accepter str, TradingView envoie des chaînes)
stop_loss     Decimal > 0
take_profit   Decimal > 0
timestamp     datetime UTC, fraîcheur vérifiée (fenêtre configurable)
```

Les nombres en **Decimal** (jamais float) pour les prix : comparaisons SL/TP
et calculs RR exacts.

## Normalisation

- Symboles/timeframes/strategy : trim + uppercase (ou casse canonique).
- Timestamp : parsing ISO 8601 UTC ; conversion systématique en UTC.
- Champs numériques reçus en `str` : conversion avec gestion d'échec =>
  rejet `invalid_price`, pas d'exception non gérée.

## Déduplication

Identifiant déterministe construit sur :

```text
signal_uid = "{strategy}:{symbol}:{timeframe}:{candle_timestamp}:{action}"
ex. momentum_v1:BTCUSDT:15:1755892800:BUY
```

- `candle_timestamp` = **timestamp de bougie** (timestamp du signal arrondi
  au timeframe), PAS l'horodatage de réception : deux envois de la même
  alerte doivent produire le même uid.
- Contrainte `UNIQUE` PostgreSQL sur `signals.signal_uid` : la base est la
  source de vérité, pas un cache mémoire (l'app peut redémarrer).
- Conflit d'insertion => statut `DUPLICATE`, réponse 200, aucune notification.

## Statuts du signal

```text
RECEIVED   -> reçu, en cours de traitement
VALIDATED  -> passé toutes les validations
SENT       -> embed Discord envoyé
REJECTED   -> invalide (raison loggée : invalid_symbol, invalid_stop_loss...)
DUPLICATE  -> déjà traité
ERROR      -> échec interne (DB, Discord...) après validation
```

## Risk/Reward (calcul backend)

```text
BUY :  risk = entry - SL ; reward = TP - entry
SELL : risk = SL - entry ; reward = entry - TP
RR  = reward / risk   (risk = 0 => rejet)
```

Affichage Discord : `Risk/Reward: 1:2` (ratio RR arrondi, ex. RR 2.0 => 1:2).
Un RR minimal configurable peut être imposé (voir skill `strategy-design`).

## Paper trading (branché sur ce pipeline)

Après `SENT`, le moteur de paper trading (`app/paper_trading/`) ouvre une
position virtuelle (entry/SL/TP du signal), puis détermine si SL ou TP est
atteint et enregistre le résultat **en R** :

```text
TP atteint -> +RR (ex. +2R) ; SL atteint -> -1R
```

Le paper trading est une **simulation locale** : aucun appel réseau vers un
broker/exchange. Les statistiques (win rate, expectancy, total R, drawdown)
sont dérivées de ces résultats (voir skill `backtesting` pour les définitions).

## Architecture imposée

```text
app/signals/schemas.py       Pydantic (TradingViewSignal)
app/signals/validator.py     validation métier (listes blanches, cohérence)
app/signals/deduplication.py construction du signal_uid
app/signals/processor.py     orchestration du pipeline
app/services/signal_service.py  cas d'usage : traiter un signal brut
app/services/discord_service.py envoi des embeds (interface mockable)
app/database/repository.py   accès données (voir skill postgresql)
```

- Le processor ne connaît ni FastAPI ni discord.py : il dépend d'**interfaces**
  (repository, discord service) injectées => testable sans HTTP ni Discord.
- Les listes blanches et seuils (frais, RR mini, fenêtre de fraîcheur) viennent
  de la configuration, **jamais hardcodés**.
- Toute la logique métier est **typée** et asynchrone-compatible.

## Logging

Format structuré, une ligne par étape :

```text
INFO  TradingView webhook received
INFO  Signal validated strategy=momentum_v1 symbol=BTCUSDT tf=15 action=BUY
INFO  Signal stored id=... uid=...
INFO  Discord notification sent message_id=...
WARNING Signal rejected reason=invalid_stop_loss
INFO  Signal duplicate ignored uid=...
```

Interdits dans les logs : secret, token Discord, mot de passe DB, payload brut
complet contenant le secret.

## Gestion des erreurs

| Défaillance          | Comportement                                                |
|----------------------|-------------------------------------------------------------|
| DB indisponible      | 5xx, log ERROR, pas de notification (le signal est perdu mais tracé) |
| Discord indisponible | Signal reste `ERROR` en base, log ERROR, salon de logs prévenu si possible |
| Validation           | 200 + `REJECTED` (TradingView attend un 2xx)                |
| Secret               | 401 générique                                               |

## Critères de validation

- [ ] L'ordre du pipeline est respecté (auth avant toute autre opération).
- [ ] Doublon => une seule notification, garanti par contrainte UNIQUE.
- [ ] Tous les prix en Decimal ; cohérence SL/entry/TP vérifiée par action.
- [ ] Listes blanches et seuils issus de la configuration.
- [ ] Chaque rejet a une raison loggée ; aucun secret dans les logs.
- [ ] Le processor est testable sans HTTP ni Discord (interfaces injectées).
