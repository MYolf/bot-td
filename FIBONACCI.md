# Fibonacci — spécification scellée (Phase 32, étape 0)

> Extension post-Phase 31. **Ce document est scellé avant toute mesure sur les
> données** : aucun des seuils ci-dessous ne sera retouché après consultation
> des résultats. En cas de contradiction, `Projet.md` et `CLAUDE.md` priment.

## Position dans l'architecture (décision non négociable)

Le retracement Fibonacci est une **mesure**, pas un déclencheur.

- Il n'entre **jamais** dans les gâchettes, les déclencheurs ni le score tant
  que l'hypothèse ci-dessous n'est pas validée out-of-sample.
- Le moteur de production (`engine/confluence.py`, `engine/runner.py`) reste
  **inchangé** pendant toute la phase : l'étude joint les signaux existants aux
  états Fibonacci par index de bougie, sans les modifier.
- Contexte (verdict Phase 31) : le 15m est structurellement perdant (frais
  ~0.5 R/trade). Cette feature ne peut pas corriger un mur de frais ; elle est
  évaluée pour son **incrément informationnel**, pas comme sauveur du 15m.

## 1. Convention de calcul (figée)

- Unité de base : leg entre deux swings fractals confirmés (`find_swings`,
  `k = 3`, identique à `engine/structure.py`).
- **Impulsion haussière** : swing low confirmé (bougie `i_low`) puis swing high
  confirmé (bougie `i_high`), `i_high > i_low`.
  Niveau `r` = `high − r × (high − low)` — 0 au sommet, 1 à l'origine
  (convention TradingView).
- **Impulsion baissière** : miroir exact — niveau `r` =
  `low + r × (high − low)` (0 au creux, 1 à l'origine).
- Niveaux calculés : `0, 0.236, 0.382, 0.5, 0.618, 0.786, 1` (les trois
  premiers servent uniquement à la visualisation et à la bucketisation de
  profondeur).
- **Bande utile** : `[niveau 0.50, niveau 0.70]`. Le 0.70 est une borne, pas
  un niveau de retracement. Le 0.786 sert de référence visuelle, jamais de
  logique d'entrée.

## 2. Impulsion qualifiée (figée)

Un leg n'est une impulsion que s'il satisfait **toutes** les conditions,
évaluées à la bougie de confirmation du second swing :

| Condition                    | Seuil scellé                                                    |
|------------------------------|-----------------------------------------------------------------|
| Swings                       | deux swings confirmés consécutifs de sens opposés (k = 3)       |
| Amplitude                    | `high − low ≥ 4 × ATR14` (ATR à la bougie de confirmation)      |
| Durée                        | `i_high − i_low ≤ 60` bougies                                   |
| Displacement                 | au moins un `DisplacementEvent` dans `[i_low, i_high]`, même sens |
| Validation structurelle      | au moins un BOS du même sens que le leg, cassé pendant `[i_low, i_high]` |

Réutilise uniquement les primitives existantes (`find_swings`,
`displacements`, `market_structure`). Aucun nouveau détecteur.

## 3. Cycle de vie (figé)

- **Création** : à la bougie `confirmed_at` du second swing (`i + k`). Jamais
  avant — propriété de préfixe, même contrat que `structure.py` / `zones.py`.
- **Unicité** : au plus **un** Fib actif. Toute nouvelle impulsion qualifiée
  **remplace** immédiatement l'active (même en cours de retracement).
- **Retest de bande** : un « retest » est compté à chaque **épisode** de
  contact du prix avec la bande (entrée dans `[0.50, 0.70]`, wick ou clôture) ;
  un nouvel épisode exige que le prix soit sorti de la bande entre les deux.
- **Consommation** : au **3ᵉ** retest, le Fib est consommé (statut terminal).
- **Invalidation** : clôture au-delà du niveau **1.0** (l'origine du leg) —
  le retracement a échoué, l'impulsion est remise en cause.
- **Expiration** : `200` bougies après la création.
- L'ATR de référence pour l'amplitude est l'ATR14 à la bougie de confirmation
  du second swing.

## 4. Chevauchement Order Block × bande (figé)

Mesure objective entre `[ob.bottom, ob.top]` et `[zone_basse, zone_haute]`,
au moment du retest de l'OB :

| Grade          | Définition                                                        |
|----------------|-------------------------------------------------------------------|
| `inclusion`    | `[ob.bottom, ob.top]` ⊆ bande                                     |
| `overlap`      | intersection non vide, sans inclusion                             |
| `proximity`    | disjoint, distance bord à bord `≤ 0.5 × ATR14`                    |
| `none`         | sinon                                                             |

Distance normalisée ATR (fonctionne sur BTC et ETH sans retouche).
La direction du Fib doit **correspondre** à la direction de l'OB pour que le
grade soit autre que `none` (jamais de croisement bullish/bearish).

## 5. Hypothèse unique (figée)

> Un retest d'Order Block **ayant un grade `inclusion` ou `overlap`** avec la
> bande 0.50–0.70 d'un Fib actif de même direction a un forward return
> **supérieur** au retest d'OB sans cette confluence.

Question secondaire (lecture, jamais décision) : la profondeur de retracement
au moment du retest, bucketisée `< 0.5 / 0.5–0.7 / 0.7–1.0`, discrimine-t-elle
elle aussi les retests ?

## 6. Protocole de mesure (figé)

- **Périmètre** : BTCUSDT, ETHUSDT × 15m, 1H.
- **Événements** : tous les triggers `ob_retest` émis par `confluence_signals`
  (profils existants inchangés). Contrôles : mêmes découpes sur `sweep`,
  `bos`, `fvg_retest` pour vérifier la spécificité de l'effet.
- **Groupes comparés** : `OB seul` (grade `none`/`proximity` ou aucun Fib
  actif aligné) vs `OB + Fib` (grade `inclusion` ou `overlap`).
- **Horizons** : 4 / 16 / 48 bougies (forward return, comme `feature_study.py`).
- **Frais** : d'abord **sans frais** (séparer « pas d'edge » de « edge mangé
  par les frais » — leçon Phase 31), puis traduction en R via le bracket
  standard (SL structurel 1–2.5 ATR, TP 2R, frais aller-retour 0.12 %).
- **Découpage** : IS/OOS scellé identique à `engine/validation.py`
  (IS = 90 jours récents, OOS = 90 jours antérieurs, warmup 20 jours).
  L'OOS n'est lancé **qu'une fois**, après figeage des finalistes sur l'IS.
  Limite assumée et documentée : l'OOS précède l'IS dans le temps.
- **Historique** : 195 jours en 15m, 365 jours en 1H (assez d'événements),
  via le cache `data/cache/` (non versionné).

## 7. Critère de décision (figé, écrit avant toute mesure)

Pour un couple symbole × timeframe donné, l'hypothèse est **validée** si et
seulement si, **dans la fenêtre IS et dans la fenêtre OOS** :

1. `n ≥ 30` événements dans **chacun** des deux groupes comparés ;
2. le forward return moyen à `h = 16` du groupe `OB + Fib` dépasse celui du
   groupe `OB seul` (delta strictement positif) ;
3. le même delta, traduit en R net de frais sur bracket standard, reste
   positif.

Un **timeframe** n'est retenu que si le critère passe pour **BTC et ETH**.
Sinon : feature rejetée, documentée dans `AVANCEMENT.md`, archivée — peu
importe à quel point elle « semble logique » visuellement.

Si (et seulement si) validé : étape 4 optionnelle — bonus **plafonné à +5**
dans la catégorie Structure & Liquidity et/ou profil `smc_fib_v1`, re-validé
par walk-forward avant toute intégration au score.

## 8. Livrables et périmètre de code

| Fichier                       | Rôle                                                     |
|-------------------------------|----------------------------------------------------------|
| `engine/fibonacci.py`         | `FibState` par bougie, cycle de vie, overlap, dump JSON   |
| `engine/fib_study.py`         | étude d'événements (jointure signaux × Fib par index)     |
| `tests/test_engine_fibonacci.py` | préfixe anti-lookahead, conventions, cycle de vie, grades |

**Interdits pendant la phase** : modifier `engine/confluence.py`,
`engine/runner.py`, le score, les gâchettes ou tout fichier de production.

## 9. Visualisation (debug uniquement)

`python -m engine.fibonacci --symbol BTCUSDT --timeframe 15 --days 90 --dump`
produit un JSON horodaté : swings retenus, niveaux 0 → 1, bande 0.50–0.70,
OB et FVG actifs, statut du Fib à chaque bougie. But : comparer le tracé du
moteur avec l'analyse manuelle TradingView, **avant** l'étude chiffrée.

## 10. Règle absolue inchangée

Signalisation uniquement. Aucun ordre, aucune clé API broker/exchange, jamais.
Binance = données publiques uniquement.
