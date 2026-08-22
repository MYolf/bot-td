---
name: strategy-design
description: >-
  Analyse technique et conception de stratégies de trading : tendance,
  momentum, volatilité, volume, structure de marché, support/résistance,
  confluence, multi-timeframe, et cahier des charges type d'une stratégie
  (marché, timeframe, entrée, sortie, SL, TP, RR, filtres, invalidation).
  Utiliser ce skill dès qu'on conçoit, décrit ou évalue une stratégie de
  trading, ou qu'on discute de la pertinence d'indicateurs ou de conditions
  d'entrée/sortie.
---

# Conception de stratégies et analyse technique

Ce skill fusionne l'analyse technique (comment raisonner sur le marché) et le
design de stratégie (comment transformer ce raisonnement en système testable).

## Principes d'analyse technique

Un indicateur seul n'est **jamais** une preuve suffisante pour générer un
signal. Un signal doit résulter d'une **confluence** : plusieurs conditions
indépendantes qui décrivent le même scénario.

Dimensions à considérer :

1. **Tendance** : direction du marché. EMA (50/200), structure de prix (HH/HL
   vs LH/LL), pente. Définir : haussière, baissière, range.
2. **Momentum** : force du mouvement. RSI (14), MACD (ligne, histogramme,
   divergences), taux de variation. Attention : le momentum confirme, il
   n'anticipe pas.
3. **Volatilité** : ATR (14), bandes de Bollinger. Sert surtout à dimensionner
   SL/TP de façon adaptative plutôt qu'en pourcentage fixe.
4. **Volume** : confirmation de la participation (volume > moyenne 20).
   Indispensable sur actions, moins fiable en crypto 24/7 — à justifier selon
   l'actif.
5. **Structure du marché** : niveaux horizontaux, ordres blocs, cassures avec
   retest. La structure prime sur les indicateurs.
6. **Support / Résistance** : niveaux clés, zones (pas des lignes exactes).
   Un SL juste sous un support évident est plus fragile qu'un SL structurel.

**Confluence** = exiger par exemple : tendance HTF alignée + momentum
déclencheur + niveau structurel proche. Chaque condition ajoutée doit apporter
une information **nouvelle**, pas redondante (EMA 50 + EMA 55 + SMA 50 = une
seule information déguisée trois fois).

## Analyse multi-timeframe (MTF)

Schéma de référence :

```text
Timeframe élevé (4H / 1D)  -> contexte, direction autorisée
Timeframe intermédiaire (1H) -> confirmation, structure
Timeframe d'entrée (15m)     -> déclenchement
```

- Un signal LONG n'est valide que si le HTF est haussier (ou neutre selon la
  stratégie), **jamais contre le HTF** sans règle explicite.
- Les données HTF doivent être **confirmées** (voir skill `pine-script`,
  repainting) : une tendance HTF basée sur une bougie 4H en cours peut
  changer.
- Le signal final n'est émis que si **toutes** les conditions requises sont
  satisfaites simultanément à la clôture.

## Cahier des charges type d'une stratégie

Toute stratégie du projet doit être documentée avec ces champs **avant**
d'être codée :

```text
Nom / version        : momentum_v1 (version dans la table strategies)
Market               : BTCUSDT, ETHUSDT, XAUUSD... (listes blanches)
Timeframe(s)         : entrée 15m, contexte 4H
Type                 : tendance / retour à la moyenne / cassure
Trend conditions     : EMA50 > EMA200 sur 4H (contexte haussier)
Entry conditions     : cassure EMA20 + RSI > 50 + MACD haussier, à la clôture
Exit conditions      : TP/SL fixes OU sortie sur signal inverse (justifier)
Stop Loss            : structurel (sous le dernier swing low) ou ATR x1.5
Take Profit          : RR minimum 1.5, ou structurel
Risk/Reward          : minimum accepté (ex. 1:1.5), sinon signal rejeté
Filters              : volume, session, volatilité max, spread
Invalidation         : condition qui annule le scénario (ex. clôture sous EMA200)
```

## Règles de conception

1. **Justifier chaque indicateur** : quel rôle (contexte / déclencheur /
   filtre) ? Si aucun rôle clair, le supprimer.
2. **Éviter les tours d'indicateurs** : 3 à 5 conditions bien choisies
   suffisent ; empiler des indicateurs corrélés ne fait qu'ajouter du bruit et
   de l'overfitting.
3. **Testable** : chaque condition doit être vérifiable en backtest et
   observable en base (le signal stocké doit permettre de reconstituer pourquoi
   il a été émis).
4. **Invalidation explicite** : une stratégie sans condition d'invalidation
   n'est pas terminée.
5. **SL/TP toujours définis à l'émission** : le backend exige `stop_loss` et
   `take_profit` cohérents (BUY : SL < entry < TP ; SELL : TP < entry < SL).
6. **RR minimum** : un signal dont le RR est inférieur au seuil configuré doit
   être rejeté côté backend (et idéalement jamais émis côté Pine).
7. **Versionner** : changer la logique = nouvelle version (`momentum_v2`), pas
   une modification silencieuse — les statistiques par stratégie en dépendent.

## Dimensionnement SL/TP

- **Structurel** (préféré) : SL sous/au-delà du niveau invalidant, TP vers le
  prochain niveau. RR = conséquence de la structure, pas un choix arbitraire.
- **ATR** : `SL = entry - 1.5 * ATR(14)` (BUY), `TP = entry + k * ATR` avec
  k >= 1.5. S'adapte à la volatilité.
- **Pourcentage fixe** : à éviter comme défaut, sauf justification.

## Erreurs fréquentes

- Optimiser les paramètres jusqu'à ce que l'historique soit parfait
  (overfitting — voir skill `backtesting`).
- Ajouter des conditions après chaque perte sans repenser la logique.
- Signaux contre-tendance HTF sans règle explicite.
- SL arbitraire trop serré, systématiquement sorti par le bruit.
- Confondre "le graphique historique est joli" et "la stratégie est valable".
- Score de qualité présenté comme une probabilité de gain (interdit sans
  calibration statistique réelle).

## Critères de validation

- [ ] Le cahier des charges de la stratégie est complet (tous les champs).
- [ ] Chaque condition a un rôle identifié (contexte / déclencheur / filtre).
- [ ] Le signal exige une confluence, pas un indicateur isolé.
- [ ] Le MTF est confirmé à la clôture (anti-repainting).
- [ ] SL, TP, RR minimum et invalidation sont explicites.
- [ ] La version de la stratégie est enregistrée en base.
