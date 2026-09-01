# Moteur de confluence — cahier des charges et décisions

> Extension post-Phase 30 : évolution vers plusieurs stratégies et un moteur
> de confluence multi-dimensionnel. Ce document fait foi pour les décisions ;
> `Projet.md` et `CLAUDE.md` restent prioritaires en cas de contradiction.

## Principe fondamental (décision utilisateur, non négociable)

**Les indicateurs ajoutent de la qualité, jamais une prise de position.**

- Seuls des **événements structurels** (BOS, liquidity sweep, breakout,
  retest de zone) peuvent DÉCLENCHER un signal.
- Les indicateurs (EMA, RSI, MACD, VWAP, RVOL, ATR) ne font que QUALIFIER :
  gâchettes de contexte, score de confluence, dimensionnement.
- Un score élevé sans événement déclencheur ne produit jamais de signal
  (le score est compensable, l'événement ne l'est pas).

## Architecture à trois niveaux

```
NIVEAU 1 — GÂCHETTES (booléen, non compensable)
  trend 1H non opposé (bougie clôturée), volatilité dans bornes, RR >= 1:1.5

NIVEAU 2 — DÉCLENCHEUR (booléen, obligatoire — famille structure)
  BOS confirmé | liquidity sweep confirmé | breakout | retest de zone (OB/FVG)

NIVEAU 3 — SCORE DE CONFLUENCE (qualifie, plafonds par catégorie, /100)
  Tendance 15m /25 · Tendance 1H /15 · Structure & liquidité /30
  Momentum /15 · Volume /15
```

- Poids de départ **égaux par catégorie**, ajustés uniquement sur preuve
  empirique (forward returns conditionnels) validée out-of-sample.
- Le score est un **rang ordinal** (tiers A/B/C), jamais une probabilité de gain.
- Corrélation traitée par construction : une seule valeur d'état par catégorie,
  les indicateurs d'une même famille sont fusionnés (RSI+MACD = 1 état momentum ;
  EMA50/200+pente+distance+VWAP = 1 état tendance).

## Familles de features

| Famille        | Rôle           | Contenu                                                      |
|----------------|----------------|--------------------------------------------------------------|
| Structure      | DÉCLENCHEUR    | swings fractals, BOS/CHOCH (fait) ; sweep, OB, FVG, range (à venir) |
| Tendance       | qualification  | EMA50/200, séparation, pente, position du prix ; contexte 1H |
| Momentum       | qualification  | RSI + MACD fusionnés (plafonnés, jamais déclencheurs)        |
| Volatilité     | gâchette + SL  | ATR relatif, expansion/compression ; SL/TP structurels bornés ATR |
| Volume         | qualification  | RVOL (volume / SMA20), niveaux à calibrer par backtest       |
| VWAP           | contexte       | ancré 00:00 UTC (crypto 24/7 : pas d'open de session)        |

## Contrat anti-lookahead (propriété de préfixe)

Toute feature `f` satisfait : `f(candles[:t+1]) == f(candles)[:t+1]`.
Un swing en `i` (fractale k bougies de chaque côté) n'est utilisable qu'à
partir de la bougie `i+k`. BOS sur **clôture** au-delà du dernier swing
confirmé, jamais sur un wick. Testé systématiquement dans
`tests/test_engine_structure.py` et `tests/test_engine_features.py`.

## Stratégies (profils déclaratifs sur le même moteur)

- `momentum_v1` : baseline inchangée, servant de référence.
- `trend_v2` / `smc_v1` : continuation en tendance (gâchettes trend +
  trigger pullback/retest ou sweep/BOS) — même moteur, features différentes.
- `breakout_v1` : compression → expansion (régime différent).

## Protocole anti-overfitting

1. Une feature à la fois : forward returns (4/16/48 bougies) conditionnels à
   son état, BTC puis ETH (`python -m engine.feature_study`).
2. IS/OOS temporel 70/30 — l'OOS n'est jamais touché avant le verdict final.
3. Walk-forward ancré (re-optimisation légère) comme estimation finale.
4. Robustesse : plateau de paramètres (pas de pic), bootstrap des trades,
   comptage des essais. **< 100 trades OOS = aucune conclusion.**
5. Frais (2 × 0.05 % taker) + slippage inclus dès le premier backtest.
   Sur 15m, un edge qui ne couvre pas ~0.10-0.15 % par aller-retour n'existe pas.

## Règle absolue inchangée

Signalisation uniquement. Aucun ordre, aucune clé API broker/exchange, jamais.
Binance = données publiques uniquement.
