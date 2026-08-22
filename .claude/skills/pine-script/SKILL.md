---
name: pine-script
description: >-
  Stratégies et indicateurs Pine Script pour TradingView : strategy.entry,
  strategy.exit, alert_message, request.security multi-timeframe, confirmation
  de bougie, repainting, lookahead et future leak. Utiliser ce skill dès qu'on
  écrit ou relit du code Pine Script, une stratégie TradingView, ou qu'on
  corrige un comportement différent entre historique et temps réel.
---

# Pine Script — Stratégies TradingView

## Règle projet

Pine Script est le **seul endroit** où l'analyse de marché est faite. Le code
Pine ne fait **aucun** appel réseau : il ne fait que déclencher des ordres
fictifs (`strategy.*`) et des alertes. L'exécution réelle n'existe pas dans ce
projet.

## Bases indispensables

- Utiliser la **version de Pine la plus récente supportée par TradingView**
  (`//@version=6` ou v5 si contrainte), déclarée en première ligne.
- Deux modes : `indicator()` (affichage) et `strategy()` (ordres simulés,
  backtest intégré, alertes de stratégies).
- Déclaration d'une stratégie :

```pine
strategy("Momentum V1", overlay = true,
     initial_capital = 10000,
     default_qty_type = strategy.percent_of_equity, default_qty_value = 100,
     commission_type = strategy.commission.percent, commission_value = 0.1,
     slippage = 2,
     calc_on_every_tick = false,
     process_orders_on_close = true)
```

`calc_on_every_tick = false` et `process_orders_on_close = true` réduisent
l'écart entre backtest et temps réel.

## Ordres et alertes

```pine
longCondition  := ta.crossover(ta.ema(close, 50), ta.ema(close, 200)) and ta.rsi(close, 14) > 50
shortCondition := ta.crossunder(ta.ema(close, 50), ta.ema(close, 200)) and ta.rsi(close, 14) < 50

if longCondition
    strategy.entry("Long", strategy.long)
    strategy.exit("Long TP/SL", from_entry = "Long",
        stop   = close * (1 - slPct),
        limit  = close * (1 + tpPct),
        alert_message = '{"secret":"SECRET","strategy":"momentum_v1","symbol":"' + syminfo.ticker + '","exchange":"' + syminfo.prefix + '","timeframe":"' + timeframe.period + '","action":"BUY","price":"' + str.tostring(close) + '","stop_loss":"' + str.tostring(close * (1 - slPct)) + '","take_profit":"' + str.tostring(close * (1 + tpPct)) + '","timestamp":"' + str.format_time(timenow, "yyyy-MM-dd'T'HH:mm:ss'Z'", "UTC") + '"}')
```

- `alert_message` est transmis à l'alerte de type **Order fills / stratégie**
  (`{{strategy.order.alert_message}}`), ce qui garantit **cohérence entre
  l'ordre simulé et le JSON envoyé** : une seule source de vérité.
- Alternative : alerte classique avec `alert()` et un JSON construit à la main
  — acceptable, mais risque de divergence avec les ordres de la stratégie.
- Toujours `str.tostring` pour les nombres dans le JSON ; toujours JSON
  valide (échapper si nécessaire).

## Séries temporelles (mental model)

- Presque tout est une **série** : `close` = série des clôtures. `close[1]` =
  valeur de la bougie précédente (opérateur `[]` = historique, jamais futur).
- `ta.ema(close, 50)` est une série ; les conditions sont des séries de
  booléens ; `if` s'exécute **bougie par bougie**.
- Ne jamais recalculer un indicateur sur `close[1]` dans une condition live
  sans le vouloir : écrire la condition sur les séries et laisser Pine gérer
  l'historique.

## Multi-timeframe : request.security

```pine
htfTrend = request.security(syminfo.tickerid, "240", ta.ema(close, 50) > ta.ema(close, 200)[1], lookahead = barmerge.lookahead_off)
```

Règles :

- Par défaut, sur le timeframe demandé, la **dernière bougie en cours est
  utilisée** : sa clôture change en temps réel => source majeure de
  **repainting**.
- Pour une donnée confirmée : décaler avec `[1]` dans l'expression demandée
  ET `lookahead = barmerge.lookahead_on`, ou utiliser
  `barmerge.lookahead_off` avec `[1]` sur la série retournée. Le combo
  sûr et idiomatique :

```pine
f_confirmed(src) => src[1]
htf = request.security(syminfo.tickerid, "240", f_confirmed(ta.ema(close, 50)), lookahead = barmerge.lookahead_on)
```

- `lookahead_on` **sans** décalage `[1]` = **future leak** en backtest :
  backtest excellent, temps réel désastreux. C'est l'erreur n°1.
- Éviter `request.security` dans des boucles ou sur des symboles multiples
  inutiles (limites de contexte).

## Confirmation de bougie et repainting

Sources classiques de repainting / divergence historique vs réel :

1. `security` sur bougie en cours (voir ci-dessus).
2. `calc_on_every_tick = true` (stratégie recalculée en live, pas en
   historique).
3. Signaux basés sur `high`/`low` de la bougie courante.
4. Indicateurs qui se recalculent (ex. ZigZag, patterns non confirmés).
5. `barstate.isrealtime` vs historique : conditions différentes.
6. Utiliser `close` d'une bougie non terminée.

Parades systématiques :

- Générer les signaux **à la clôture** (`Once Per Bar Close` sur l'alerte,
  conditions évaluées sur bougie terminée, `barstate.isconfirmed` si besoin
  en temps réel).
- Comparer le backtest TradingView avec et sans `Bar Magnifier` ; un écart
  important = signal d'alarme.
- Vérifier la stratégie sur Replay / temps réel avant tout déploiement.

## Backtesting intégré

- Activer **commission et slippage** réalistes dès le début (voir skill
  `backtesting`) : un backtest sans frais n'a aucune valeur.
- `strategy.exit` avec `stop` et `limit` couvre SL/TP ; vérifier que chaque
  entrée a toujours une sortie définie.
- Ne jamais juger une stratégie sur le seul equity curve historique (voir
  skill `backtesting`).

## Erreurs fréquentes

- JSON invalide dans `alert_message` (virgule, guillemets, format nombre).
- Ordre simulé sans `strategy.exit` associé.
- Signal sur bougie non confirmée => signal qui disparaît en temps réel.
- `lookahead_on` sans `[1]` => future leak.
- Paramètres optimisés à l'excès sur l'historique => overfitting.
- Timeframe hardcodé au lieu de `timeframe.period`.

## Critères de validation

- [ ] Signaux générés uniquement à la clôture de bougie.
- [ ] Aucun `lookahead_on` sans décalage confirmé.
- [ ] Chaque `strategy.entry` a un `strategy.exit` (SL/TP).
- [ ] `alert_message` produit un JSON strictement conforme au schéma du
      backend (voir skill `signal-engine`).
- [ ] Commission et slippage actifs dans le backtest.
- [ ] Comportement identique observé en Replay et en historique.
