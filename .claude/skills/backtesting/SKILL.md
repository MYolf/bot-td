---
name: backtesting
description: >-
  Évaluation des stratégies : backtesting, métriques de performance (win rate,
  profit factor, expectancy, R-multiples, drawdown), frais et slippage, biais
  (lookahead, survivorship, overfitting), out-of-sample et walk-forward.
  Utiliser ce skill dès qu'on évalue, compare ou valide une stratégie, ou
  qu'on interprète des statistiques de performance (commande /stats, paper
  trading, résultats de backtest TradingView).
---

# Backtesting et évaluation des stratégies

## Principe de base

**Un backtest n'est jamais une preuve de rentabilité future.** C'est un outil
pour **réfuter** une stratégie (trouver ses défauts) et estimer son
comportement passé dans des conditions réalistes. Toute conclusion doit être
formulée avec prudence, y compris dans les messages Discord.

## Conditions de validité d'un backtest

- **Frais inclus** : commission par trade réaliste (ex. 0.05–0.1 % taker
  crypto, spread forex), configurés dans la stratégie TradingView.
- **Slippage inclus** : slippage en ticks/valeur de prix, pas zéro.
- **Signaux à la clôture confirmée uniquement** (voir skill `pine-script`) :
  pas de lookahead, pas de future leak, pas de données de bougie en cours.
- **Données suffisantes** : couvrir plusieurs régimes de marché (tendance
  haussière, baissière, range, événements de volatilité). Le BTC 2020-2021
  seul ne prouve rien pour 2026.
- **Échantillon suffisant** : < 30 trades = aucune signification statistique.
  100+ trades pour des conclusions sérieuses.

## Métriques de référence

Le projet mesure en **R-multiples** (résultat / risque initial), ce qui rend
les stratégies comparables indépendamment du capital :

```text
Entry = 100, SL = 98  ->  risque = 1R = 2
TP = 104 atteint      ->  +2R
SL atteint            ->  -1R
```

Métriques à calculer (backend /stats et évaluation) :

| Métrique       | Définition / lecture                                     |
|----------------|----------------------------------------------------------|
| Win rate       | trades gagnants / total. Seul, il ne dit rien (WR 40 % avec RR 1:3 est rentable) |
| Expectancy     | moyenne des R réalisés : `(win_rate * avg_win_R) - (loss_rate * avg_loss_R)` ; > 0 obligatoire |
| Total R        | somme des R ; performance globale                        |
| Profit factor  | gains cumulés / pertes cumulées ; > 1.5 correct, < 1 fragile |
| Max drawdown   | plus forte baisse cumulée (en R) ; douleur maximale endurée |
| Avg R          | R moyen par trade                                       |
| Best / Worst   | meilleur et pire trade (détecter les outliers)          |
| Profit factor par catégorie | par stratégie / symbole / timeframe / action (LONG vs SHORT) |

Toujours croiser win rate avec le RR moyen : c'est le couple qui compte.

## Biais à traquer systématiquement

1. **Lookahead bias / future leak** : utiliser en backtest une information non
   disponible au moment du signal. Sources : `request.security` avec
   `lookahead_on` sans décalage, signaux sur bougie non clôturée, `high/low`
   de la bougie courante. Symptôme : backtest parfait, live désastreux.
2. **Repainting** : la stratégie modifie ses signaux passés (voir skill
   `pine-script`).
3. **Survivorship bias** : ne tester que des actifs encore vivants/performants
   aujourd'hui.
4. **Overfitting** : optimiser les paramètres jusqu'à coller parfaitement à
   l'historique. Symptômes : paramètres très précis (RSI 13.7), performance
   qui s'effondre hors période d'optimisation, ajout de conditions après coup.
5. **Biais de sélection** : ne rapporter que la meilleure des N variantes
   testées.

## Méthodes de validation

- **Out-of-sample** : optimiser sur une période (ex. 2023-2024), valider sur
  une période jamais touchée (2025-2026). Si la performance s'effondre
  out-of-sample => overfitting.
- **Walk-forward** : découper en fenêtres successives optimisation/validation
  qui avancent dans le temps ; la performance walk-forward est la meilleure
  estimation réaliste.
- **Sensibilité des paramètres** : si RSI 14 -> 12 ou 16 change tout, la
  stratégie est fragile. Une bonne stratégie est **plate** autour de ses
  paramètres.
- **Tests MTF / multi-actifs** : une logique saine se généralise mal mais pas
  catastrophiquement.

## Paper trading (rôle dans ce projet)

Le paper trading (simulation locale, cf. Projet.md) sert à valider en
**conditions réelles temps réel** ce que le backtest a suggéré :

```text
Backtest  -> hypothèse
Paper     -> vérification live (signaux réels, exécution simulée, SL ou TP atteint)
Stats     -> décision de garder / modifier / abandonner la stratégie
```

- Le moteur de paper trading suit pour chaque position : entry, SL, TP, et
  détermine si SL ou TP est atteint => résultat en R (voir skill
  `signal-engine`).
- Une divergence importante entre backtest TradingView et paper trading
  (signaux manquants, horaires différents) indique du repainting : investiguer
  avant tout déploiement.

## Règles de communication (Discord, docs)

- Ne jamais présenter un backtest comme une garantie.
- Ne jamais présenter le score de qualité comme une probabilité de gain.
- Toujours préciser la période, le nombre de trades et si les frais sont
  inclus quand on rapporte des statistiques.

## Critères de validation

- [ ] Frais et slippage inclus et réalistes.
- [ ] Nombre de trades suffisant (100+) sur plusieurs régimes de marché.
- [ ] Validation out-of-sample ou walk-forward effectuée.
- [ ] Sensibilité des paramètres vérifiée (plateau, pas de pic).
- [ ] Aucun lookahead / repainting détectable.
- [ ] Expectancy > 0 et drawdown acceptable, mesurés en R.
- [ ] Conclusions formulées avec les réserves appropriées.
