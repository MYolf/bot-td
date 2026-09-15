# LIQUIDITY — Étude de stratégies de liquidité 4H (cahier des charges scellé)

> **Statut : PROJET v1.0 — à valider par l'utilisateur. Rien n'est scellé avant
> sa validation explicite. AUCUNE ligne de code avant ce feu vert.**
>
> Ce document fait foi pour toutes les décisions de l'étude. En cas de
> contradiction : `Projet.md` et `CLAUDE.md` priment.

## 1. Objet et principes

Étudier trois architectures de price action / structure de marché sur BTCUSDT
et ETHUSDT, orientées liquidité (pools, sweeps) et structure (range, breakout),
sur timeframes élevés (4H contexte, 1H exécution) — où le rapport
risque-par-trade / frais devient favorable (le 15m est définitivement exclu :
réfuté par les Phases 31 et audits momentum, frais ~0,3-0,7R/trade).

Objectif de performance : robustesse et expectancy nette d'abord ; le winrate
(~60 % recherché) ne doit JAMAIS être obtenu par paramétrage ou sélection.
Une stratégie à 55 % robuste est préférée à une stratégie à 70 % surajustée.

Méthode : HYPOTHÈSE → RÈGLES FIGÉES → ÉTUDE 0 (tri IS) → IS → OOS →
WALK-FORWARD → VERDICT. Aucune règle modifiée après consultation d'un résultat.

## 2. Périmètre et non-régression (non négociable)

- Aucune modification de : `momentum_v1`, `engine/runner.py`, `app/`
  (webhook, Discord, paper trading), docker-compose, `.env`, migrations.
- L'étude vit dans de nouveaux modules (`engine/liquidity/…`,
  `engine/liquidity_study.py`, `tests/test_liquidity…`) + un journal d'essais
  versionné (`data/liquidity/trials.log`, non commité tant que l'étude court).
- Règle absolue du projet inchangée : signalisation uniquement, aucune clé API
  de trading, Binance = données publiques uniquement.

## 3. Données, fenêtres scellées, conventions

### 3.1 Données

- Klines Binance publiques 4H et 1H, BTCUSDT et ETHUSDT.
- Historique chargé depuis 2021-10-01 (warmup : 200 bougies 4H et 200 bougies
  1H minimum avant la première décision).
- Bougies 1H alignées sur les bougies 4H (6 bougies 1H par bougie 4H,
  open_time 4H multiple de 4 h).

### 3.2 Fenêtres scellées (avant tout test)

| Fenêtre | Bornes (heure d'entrée des trades) | Usage |
|---|---|---|
| IS   | 2022-01-01 00:00 UTC → 2025-03-01 00:00 UTC | étude, tri, décisions |
| OOS  | 2025-03-01 00:00 UTC → 2026-09-01 00:00 UTC | verdict final uniquement |
| WF   | 8 fenêtres test de 6 mois glissantes sur 2022-01 → 2026-09 | stabilité temporelle |

L'OOS n'est JAMAIS consulté avant le verdict IS complet d'une candidate.
Paramètres figés : aucune re-optimisation ; le walk-forward mesure la
STABILITÉ (performance par sous-périodes), pas une recherche de paramètres.

### 3.3 Conventions d'exécution (toutes candidates)

- **Anti-lookahead (propriété de préfixe)** : toute décision à la clôture de la
  bougie t n'utilise que des bougies d'open_time ≤ t. Testée unitairement pour
  chaque primitive.
- Entrée "market" = au close de la bougie de déclencheur, frais taker.
- Entrée "limite" (maker) = fill si le low (long) / high (short) de la bougie
  atteint la limite pendant la fenêtre de validité.
- **Conventions prudentes** (identiques aux moteurs du projet) :
  - si une même bougie touche SL et TP → **SL prioritaire** ;
  - sur la bougie de fill d'une entrée limite : le SL est actif dès cette
    bougie, le TP n'est compté qu'à partir de la bougie SUIVANTE ;
  - une position n'est jamais vérifiée contre une information de la bougie
    4H qui n'est pas clôturée.
- **Une position à la fois par symbole et par backtest** (pyramiding = 0).
  Tout événement survenant pendant une position ouverte est ignoré (pas de
  file d'attente).
- Suivi SL/TP en résolution 1H (la plus fine disponible).
- Sortie au niveau exact du SL/TP (pas au wick). Sortie temporelle au close
  de la bougie d'expiration.

### 3.4 Frais et slippage (trois niveaux rapportés SÉPARÉMENT)

| Niveau | Aller | Retour | Slippage par côté |
|---|---|---|---|
| Brut   | 0 %    | 0 %    | 0 %   |
| Taker  | 0,05 % | 0,05 % | 0,02 % |
| Maker  | 0,02 % | 0,02 % | 0,02 % (retrait supposé maker) |

- Entrées market : taker. Entrées limite : maker à l'entrée ; le retrait est
  supposé maker (SL = ordre stop-market en réalité → variante "maker-entrée /
  taker-sortie" = 0,02 % + 0,05 % sera AUSSI rapportée pour les entrées
  limite : c'est la plus réaliste).
- Coût en R toujours rapporté : coût_R = (frais_aller + frais_retour +
  2×slippage) / distance_SL_en_%.

### 3.5 Régimes (classification figée avant test, usage diagnostique)

Par symbole, à la clôture 4H : bull si EMA200 4H ascendante depuis ≥ 20
bougies ET close > EMA200 ; bear si l'inverse ; chop sinon. Volatilité :
ATR14_4H / close au-dessus (HIGH VOL) ou en-dessous (LOW VOL) de sa médiane
calculée sur l'IS. Ces classes servent aux rapports ventilés, JAMAIS de filtre
d'entrée (sauf candidate C, qui inclut l'EMA200 dans ses règles, §8).

## 4. Primitives mathématiques (communes, déterministes)

Toutes les primitives sont définies sur les bougies 4H clôturées, sauf mention
contraire. Notations : bougie i = (o, h, l, c)[i] ; ATR14(t) = ATR Wilder 14
bougies 4H à la clôture de t.

### 4.1 Swing fractal (k = 3)

- Swing low en i : l[i] < l[j] pour tout j ∈ {i−3,…,i−1, i+1,…,i+3}.
- Swing high en i : symétrique sur les highs.
- **Confirmation** : le swing en i n'est utilisable qu'à partir de la clôture
  de la bougie i+3 (jamais avant).

### 4.2 Pool de liquidité actif (fenêtre N = 50 bougies 4H)

À la clôture de la bougie 4H t :

- Candidats SSL = bougies i ∈ [t−49, t−3] qui sont des swings low confirmés.
- **pool_SSL(t)** = l[i*] où i* = argmin des candidats ; aucun pool si aucun
  candidat.
- **pool_BSL(t)** = symétrique sur les highs (argmax).

Le pool est donc l'extrême confirmé le plus profond de la fenêtre glissante.
Aucun paramètre d'expiration : le pool change quand la fenêtre glisse ou quand
un extrême plus profond se confirme.

### 4.3 Range (événement-mère des candidates A et B)

Le range est valide à la clôture 4H t si :

1. pool_SSL(t) et pool_BSL(t) existent tous les deux ;
2. largeur : pool_BSL(t) − pool_SSL(t) ≥ 5 × ATR14(t) ;
3. le prix est à l'intérieur : pool_SSL(t) < close[t] < pool_BSL(t).

Définitions dérivées, figées au moment de référence : médiane du range =
(pool_SSL + pool_BSL) / 2 ; hauteur = pool_BSL − pool_SSL.

### 4.4 Sweep (événement déclencheur commun A/B)

Sweep SSL (haussier, cible LONG) à la bougie 4H t_s si :

1. le range est valide à la clôture t_s−1 (pool = pool_SSL(t_s−1)) ;
2. pénétration : low[t_s] ≤ pool − 0,05 × ATR14(t_s) ;
3. rejet : close[t_s] > pool.

Sweep BSL (baissier, cible SHORT) : symétrique
(high[t_s] ≥ pool_BSL(t_s−1) + 0,05×ATR14(t_s) ET close[t_s] < pool_BSL(t_s−1)).

**Anti-re-sweep** : après qu'un événement (trade, expiration ou annulation)
a consommé un niveau L d'un côté donné, aucun nouvel événement de ce côté tant
que le pool actif ne diffère pas de L de plus de 0,25 × ATR14 courant.
Un événement est consommé aussi si le setup expire sans trade (§6-8).

**Niveaux figés** : médiane du range, pool opposé et hauteur du range pour les
TP sont ceux définis à t_s−1 et ne sont jamais recalculés ensuite.

### 4.5 FVG 1H de réversion (candidate B)

Bougie 1H u, FVG bullish : l[u] > h[u−2] ; zone = [h[u−2], l[u]].
Conditions de "displacement de réversion" en u (SSL sweep, sens LONG) :

1. u ∈ fenêtre des 24 bougies 1H suivant la clôture 4H de t_s ;
2. bougie haussière : close[u] > open[u] ;
3. force : corps de u = close[u] − open[u] ≥ 1,5 × ATR14_1H(u−1) ;
4. FVG présent en u ;
5. zone cohérente : h[u−2] > pool (au-dessus du pool sweepé) et l[u] <
   close[u] (sous le prix courant).

BSL sweep : symétrique (FVG bearish : h[u] < l[u−2], bougie baissière,
corps ≥ 1,5×ATR14_1H, zone = [h[u], l[u−2]] sous le prix, au-dessous… i.e.
entièrement < pool et > close[u]).

### 4.6 EMA200 4H (candidate C uniquement)

EMA 200 bougies 4H ; "ascendante à t" = EMA200(t) > EMA200(t−1) ; calculée
sur bougies clôturées uniquement (warmup 200 bougies).

## 5. Moteur de simulation

1. Reconstruction pas à pas : à chaque clôture 4H puis chaque clôture 1H,
   l'état (pools, range, sweeps, ordres) est mis à jour SANS jamais lire une
   bougie ultérieure.
2. Cycle d'un trade : événement → fenêtre d'activation 1H → (déclencheur |
   expiration | annulation) → position (entrée, SL, TP) → suivi 1H →
   sortie (SL | TP | temporelle).
3. Sortie temporelle commune : close de la 128e bougie 1H (32 bougies 4H)
   après l'entrée.
4. Résultat en R : (sortie − entrée)/risque ajusté du sens ; net = brut −
   coût_R du niveau de frais.
5. Durée, année, régime, direction, symbole enregistrés par trade.
6. Tests unitaires obligatoires : propriété de préfixe de chaque primitive
   (re-calcul complet sur préfixe == série incrémentale), priorité SL,
   convention de fill limite, anti-re-sweep, anti-lookahead 4H→1H (aucun
   déclencheur 1H avant la clôture 4H du sweep).

## 6. Candidate A — SRR « Sweep-Reversion Range » (srr_v1)

**Hypothèse** : un sweep de pool multi-semaines DANS un range valide est suivi
d'une réversion vers l'équilibre du range (mécanisme : prise de stops →
transfert → retour).

- **Contexte** : range valide à t_s−1 (§4.3). Aucun indiceur.
- **Setup** : sweep (§4.4) à la bougie 4H t_s.
- **Fenêtre d'activation** : 20 bougies 1H suivant la clôture 4H de t_s.
- **SL** : min(low[t_s], pool_sweepé) − 0,25 × ATR14(t_s)  [LONG]
  (SHORT : max(high[t_s], pool) + 0,25×ATR).
- **TP (politiques scellées, 2 maximum)** :
  - A-TP1 : 100 % à la **médiane du range** — variante PRINCIPALE ;
  - A-50/50 : 50 % médiane + 50 % pool opposé (résultat = moyenne pondérée
    avec SL global unique) — variante secondaire.
- **Variantes d'exécution (2, scellées)** :
  - **A1 (PRINCIPALE)** : déclencheur = première bougie 1H u de la fenêtre
    avec close[u] > pool_sweepé [LONG] / close[u] < pool [SHORT] ; entrée
    market au close de u, frais taker.
  - **A2 (secondaire)** : ordre limite posé dès la clôture 4H t_s au niveau
    pool + 0,25×ATR14(t_s) [LONG] / pool − 0,25×ATR [SHORT] ; entrée maker ;
    annulé si la fenêtre expire OU si une clôture 4H franchit
    pool − 0,5×ATR [LONG] / pool + 0,5×ATR [SHORT] avant fill.
- Hiérarchie déclarée : **A1 + A-TP1 est la configuration principale** dont
  dépend le verdict ; A2 et A-50/50 sont informatives (ne peuvent être
  "promues" que si elles passent ELLES-MÊMES tous les gates du §10).

## 7. Candidate B — Sweep + FVG de réversion, entrée maker (sweep_fvg_v1)

**Hypothèse** : après un sweep, le retour s'appuie sur l'inefficience laissée
par le premier displacement de réversion ; entrée limite au cœur du FVG
améliore le prix d'entrée et divise les frais.

- **Contexte + setup** : IDENTIQUES à A (range valide + sweep §4.4). Même
  population-mère que A → ablation propre (même événement, autre exécution).
- **Recherche du FVG** : fenêtre des 24 bougies 1H suivant t_s (§4.5). Le
  PREMIER FVG de réversion conforme crée l'ordre ; la recherche s'arrête là.
- **Ordre limite** : niveau = milieu de la zone FVG ; valable 20 bougies 1H
  après la bougie u qui l'a créé. Annulation : fenêtre expirée, OU clôture
  1H au travers de la zone côté opposé (zone invalidée : clôture < h[u−2]
  pour un bullish FVG), OU clôture 4H franchit pool ∓ 0,5×ATR (§6).
- **SL** : min(bas de zone, pool_sweepé) − 0,25×ATR14(t_s) [LONG]
  (SHORT symétrique).
- **TP (une seule politique, scellée)** : dernier swing high 4H CONFIRMÉ
  (k=3) au moment du fill, strictement supérieur au niveau d'entrée
  [LONG ; SHORT : dernier swing low confirmé inférieur]. **Exigence RR ≥ 1,2**
  sinon le trade n'est PAS pris (l'événement est consommé).
- Exécution : maker (les trois niveaux de frais rapportés, §3.4).
- Configuration principale de B : celle-ci, une seule variante.

## 8. Candidate C — Breakout + retest, contrôle décorrélatif (brk_retest_v1)

**Hypothèse** : la cassure en clôture d'un range suivi d'un retest tenu
poursuit vers la hauteur projetée. Profil attendu : WR 40-50 %, RR 1,5-2,5 —
contrôle volontairement NON aligné sur l'objectif 60 %.

- **Contexte** : range valide à t_b−1 (§4.3).
- **Breakout haussier** à la bougie 4H t_b : close[t_b] > pool_BSL(t_b−1)
  (clôture, jamais un wick) ET EMA200(t_b) ascendante ET close[t_b] >
  EMA200(t_b). Baissier : symétrique (close < pool_SSL(t_b−1), EMA200
  descendante, close < EMA200).
- **Fenêtre d'activation** : 36 bougies 1H suivant la clôture 4H de t_b.
- **Déclencheur (retest)** : première bougie 1H v de la fenêtre avec
  low[v] ≤ pool_cassé + 0,1×ATR14(t_b) ET close[v] > pool_cassé [haussier]
  (baissier : high[v] ≥ pool − 0,1×ATR ET close[v] < pool). Entrée market au
  close de v, taker.
- **SL** : min(low[v], pool_cassé) − 0,25×ATR14(t_b) [haussier]
  (symétrique baissier).
- **TP (une seule politique)** : measured move = pool_cassé + hauteur du
  range (figée à t_b−1). Pas d'exigence de RR minimum.
- Anti-répétition : le niveau cassé est consommé (règle §4.4, 0,25×ATR).
- Si aucun retest dans la fenêtre : événement consommé, pas de trade.

## 9. Métriques et rapports

Par configuration × symbole × direction, et poolé par configuration-direction :

- n (trades), n (événements-mères, avec taux de conversion) ;
- winrate (résultat net > 0) ; profit factor net ; expectancy BRUTE, nette
  taker, nette maker, nette maker-entrée/taker-sortie ; R moyen, R médian ;
- coût moyen des frais en R/trade ; taux de fill (variantes limite) ;
- max drawdown (equity cumulée en R) ; durée moyenne des trades (heures) ;
- ventilation : par année, par régime (§3.5), LONG vs SHORT, BTC vs ETH ;
- bootstrap 10 000 rééchantillonnages : IC 90 % de l'expectancy nette
  (rapporté, informatif) ;
- IS, OOS, et 8 fenêtres WF de 6 mois.

Rapport honnête obligatoire : chaque configuration testée figure dans le
journal d'essais (date, configuration, fenêtre consultée, résultat brut) —
y compris les échecs. Le nombre total de configurations scellées est fixe :
A1×{TP1, 50/50} + A2×{TP1, 50/50} + B×1 + C×1 = **7 configurations**
× 2 symboles × 2 directions.

## 10. Critères de verdict (scellés AVANT tout test)

### 10.1 Étape 0 — tri par event-study (IS UNIQUEMENT, populations A/B)

Forward returns sens-ajustés à h = 12, 24, 48 bougies 4H après chaque sweep
(population-mère A/B), comparés à une baseline appariée (même jour de
semaine, même position dans le jour, hors événements, méthode macro_study).

- **GATE 0** : n ≥ 100 événements IS poolés (BTC+ETH) ET médiane du forward
  return sens-ajusté > 0 ET > baseline sur au moins un horizon h.
- Échec → A et B REJETÉS sans consommer l'OOS bracket ; C continue.

### 10.2 Verdict par configuration (après IS, puis OOS, puis WF)

**ON CONTINUE** si TOUTES les conditions suivantes sont réunies :

| # | Critère | Seuil |
|---|---|---|
| 1 | Effectif IS | ≥ 60 trades par cellule (symbole×direction), ≥ 80 poolés par configuration-direction |
| 2 | Expectancy BRUTE IS | > 0 sur BTC **et** sur ETH (chaque cellule) |
| 3 | Expectancy nette taker IS | ≥ +0,10R/trade poolée, ≥ 0 sur chaque cellule |
| 4 | Profit factor net taker IS | ≥ 1,30 poolé |
| 5 | Max drawdown net IS | ≤ 12R poolé |
| 6 | OOS | expectancy brute > 0 ET ≥ 50 % de l'IS, par cellule et poolé |
| 7 | Walk-forward | expectancy nette taker > 0 poolée ; ≥ 6/8 fenêtres positives |
| 8 | Stabilité annuelle | au plus 1 année négative ; aucune < −0,20R/trade |
| 9 | Plateau ±20 % | expectancy nette poolée > 0 pour TOUTE variation ±20 % de chaque paramètre libre (§11) |
| 10 | Directions | chaque direction (LONG, SHORT) ne passe dans la version finale que si elle satisfait 1-9 SEULE |
| 11 | Accord inter-symboles | pas de configuration validée sur un seul symbole |

**ON REJETTE** si UNE SEULE de ces conditions manque — sans exception, sans
"presque", sans relance de paramètres (leçon des Phases 31/32 : un gate
"presque passé" reste échoué).

Cas particuliers scellés :
- n insuffisant (< 60) mais ≥ 30 : verdict "NON CONCLUANT", pas de validation ;
  il est interdit d'élargir les fenêtres d'activation pour augmenter n.
- Expectancy brute > 0 mais nette < 0 : rejet avec mention explicite
  "edge mangé par les frais" (distinct de "pas d'edge").

### 10.3 Verdict final de l'étude

Une candidate est "VALIDÉE POUR INTÉGRATION" si sa configuration principale
passe §10.2. Toute intégration au bot (nouvelle stratégie du moteur, nouveau
salon, etc.) fait l'objet d'une spécification séparée et ne démarre pas avant
un accord explicite — la période d'observation de momentum_v1 (jusqu'au
25/09/2026) n'est pas affectée.

## 11. Paramètres libres et plateau de robustesse

Valeurs a priori FIGÉES (choisies rondes, avant tout test). Le plateau ±20 %
est évalué a posteriori comme TEST DE ROBUSTESSE (jamais comme recherche du
meilleur point).

| Paramètre | Valeur | Utilisé par |
|---|---|---|
| k swing fractal | 3 | pools |
| N fenêtre pools | 50 bougies 4H | pools/range |
| Largeur mini range | 5 × ATR14_4H | A, B, C |
| Buffer sweep | 0,05 × ATR14_4H | A, B |
| Buffer SL | 0,25 × ATR14_4H | A, B, C |
| Anti-re-sweep | 0,25 × ATR14_4H | A, B, C |
| Fenêtre activation A1/A2 | 20 bougies 1H | A |
| Fenêtre recherche FVG (B) | 24 bougies 1H | B |
| Fenêtre validité ordre (B) | 20 bougies 1H | B |
| Fenêtre activation C | 36 bougies 1H | C |
| Corps displacement | 1,5 × ATR14_1H | B |
| RR minimum (B) | 1,2 | B |
| Sortie temporelle | 32 bougies 4H | A, B, C |
| Annulation | 0,5 × ATR14_4H au-delà du pool | A2, B |

k=3 n'entre pas dans le plateau (définition canonique du projet, déjà
validée en Phase 31).

## 12. Décisions motivées (rejets et choix)

1. **Order Block éliminé** de B : définition la plus contradictoire de la
   littérature SMC (dernière bougie opposée ? dernier corps ? wick ?), donc
   le plus exposé au p-hacking définitionnel. Le FVG (gap de prix pur) porte
   la même information d'inefficience avec une définition objective. Une
   seule définition de zone par étude.
2. **MSS/CHOCH non utilisé comme déclencheur** : dans notre construction,
   l'information "clôture au-delà du dernier swing opposé APRÈS un sweep" est
   déjà portée par l'événement sweep + clôture de reclaim (A1) — l'ajouter
   serait une redondance, pas une confluence.
3. **Absorption, squeeze/liquidation cascade, Volume Profile, VWAP** :
   éliminés (non mesurables avec des klines publiques, ou redondants — voir
   analyse du 2026-09-15 validée par l'utilisateur).
4. **RSI/MACD exclus même en qualificatif** : décorrélation voulue avec
   momentum_v1 (argument portefeuille : une deuxième stratégie corrélée à la
   première ne diversifie pas).
5. **A et B partagent la population-mère** (range + sweep) : volontaire, pour
   une ablation propre (même événement, exécutions différentes). La
   corrélation A/B sera rapportée ; si les deux passent, le choix final entre
   A et B se fera sur frais nets et robustesse, jamais sur le meilleur IS.
6. **Deux niveaux de sortie max par candidate, fixés avant test** ; la
   promotion d'une variante secondaire exige qu'elle passe seule tous les
   gates (anti cherry-picking).
7. **Le winrate 60 % n'est pas un gate** : c'est un profil attendu de la
   candidate A (TP médiane, SL structurel large). Les gates sont
   multidimensionnels (§10.2). Aucun ajustement pour approcher 60 %.

## 13. Ordre d'exécution (après validation de ce document)

1. Primitives + moteur de simulation + tests unitaires (propriété de
   préfixe, priorité SL, fills).
2. Étape 0 (event-study IS) → GATE 0.
3. IS des 7 configurations (si GATE 0 passe) → gates 1-5, 9.
4. OOS (une seule consultation) → gate 6.
5. WF + stabilité annuelle → gates 7-8.
6. Rapport final + verdict §10.3 + mise à jour du journal d'essais.

Aucune étape ne démarre sans que la précédente soit documentée.
