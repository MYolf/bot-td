# ANTICIPATION.md — Signaux anticipatifs à l'ouverture (spec scellée)

Version 1.1 — 2026-09-14. Décisions figées **avant toute mesure** (même discipline
que CONFLUENCE.md, FIBONACCI.md, MACRO.md). Toute modification de cette spec doit
être motivée et documentée ; les seuils et fenêtres ci-dessous ne sont **pas** des
paramètres à optimiser.

**Amendement v1.1 (décision utilisateur 2026-09-14, après Phase A IS, OOS non
consommée)** : le critère 2 (avantage de fill médian >= +0,3 %) est **retiré**
des critères go/no-go. Motivation documentée : la Phase A a montré que
l'estimation +0,4/+0,5 % de l'étude de touches était biaisée par sélection
(annonces uniquement lorsque le prix avait déjà pénétré à eps du niveau) ;
sans biais, l'avantage médian est +0,14/+0,18 % avec un coût d'annulation
symétrique (−0,15 %), soit une espérance d'entrée ≈ 0. Le mode anticipatif
n'est donc plus une revendication d'avantage de prix d'entrée : c'est une
**utilité d'anticipation** (le niveau est annoncé à l'avance et touché dans
~60-85 % des cas selon l'horizon). L'avantage de fill mesuré reste AFFICHÉ
honnêtement (§7) comme ≈ neutre, jamais comme un edge. Critères 1, 3, 4
conservés tels quels. Parallèlement, le cycle de vie est enrichi : la
confirmation n'est plus silencieuse mais envoie un message « signal validé »
(choix utilisateur).

## 1. Objectif

Le moteur actuel (advance.py, commit 3a9ffdf) annonce un niveau P* seulement
lorsque le prix est **déjà** à `ENGINE_ADVANCE_EPS` (0,15 %) du niveau — mode
**réactif**. L'objectif ici est le mode **anticipatif** : annoncer le niveau
d'entrée P* **dès l'ouverture de la bougie**, dès lors qu'il existe des raisons
mesurées de croire que le prix l'atteindra, pour que l'utilisateur place son
ordre limite **à l'avance**, sans attendre la clôture de confirmation
(« Signal Time »).

Le « signal à l'avance » remplace l'heure de signal par une **durée de
validité** : le niveau est valable N bougies, après quoi il expire.

Ce que cette feature est / n'est pas :

- **Est** : une information d'entrée anticipée avec niveau exact, SL/TP et
  expiration — l'utilisateur place lui-même son ordre limite (règle absolue :
  le système ne passe JAMAIS d'ordre).
- **N'est pas** : une promesse de remplissage ni une promesse de confirmation.
  Toucher ≠ confirmer (~50 % mesuré sur 4 ans, étude de touches). Le taux
  d'atteinte réel au seuil retenu est un résultat de l'étude, affiché avec la
  feature, jamais garanti.

## 2. Périmètre (figé)

| Sujet | Décision |
|---|---|
| Stratégie sous-jacente | **momentum_v1** uniquement (mêmes conditions, mêmes formes fermées que touch_study) |
| Symboles | BTCUSDT, ETHUSDT (périmètre moteur actuel) |
| Timeframe | 15m uniquement |
| Moment d'annonce | **Ouverture de la bougie** (état = bougies fermées uniquement) |
| Critère d'atteignabilité | distance \|P* − open\| ≤ **k × ATR14** (ATR calculé sur bougies fermées) |
| Horizon de validité | **N bougies** (niveau figé à l'annonce, expiration après N clôtures sans touche) |
| Filtre qualité | score au niveau ≥ `ENGINE_MIN_SCORE` (identique au moteur et au mode réactif) |
| Garde-fou pyramiding=0 | pas d'annonce d'une direction déjà détenue par la position simulée (identique advance) |
| Impact pipeline officiel | **Aucun** : best-effort, rien en base, pas de `signal_uid`, pas de numéro de trade |
| Autres stratégies (swing, etc.) | Hors périmètre — abandonnées au profit de celle-ci (décision 2026-09-14) |

## 3. Sémantique du cycle de vie (runtime, figée)

| Événement | Comportement Discord |
|---|---|
| Ouverture de bougie : niveau P* existe + score OK + atteignable (k×ATR) + pas d'annonce active | **Pré-alerte anticipative** : embed avec entry = P*, SL/TP momentum_v1, « expire dans N bougies », sans heure ni numéro |
| Pendant la validité : le prix touche P* et une clôture CONFIRME (signal officiel émis) | **« Signal validé »** (amendement v1.1, choix utilisateur) : message de confirmation reprenant le niveau, envoyé en plus du signal officiel |
| Le prix touche P* mais la clôture ne confirme pas | **Annulation** (décharger la position, payload `kind=invalidated` existant) |
| N clôtures sans touche | **Expiration** (retirer l'ordre limite, nouveau payload `kind=expired`) |
| Signal officiel opposé pendant la validité | **Annulation** (le scénario est invalidé par le moteur lui-même) |

Anti-spam figé :
- **une seule annonce active par (symbole, direction)** ;
- P* est recalculé à chaque bougie mais le niveau annoncé **ne dérive pas** :
  pas de re-annonce tant que l'annonce active n'est pas consommée
  (confirmée / annulée / expirée) ;
- au maximum 2 annonces simultanées par symbole (BUY + SELL).

## 4. Anti-lookahead (figé)

- L'état des indicateurs est pris sur les bougies **fermées** uniquement ;
  P* en forme fermée est déjà auto-validé contre le moteur (touch_study,
  `trigger_level` / `score_at`, tests 409/409).
- L'annonce à l'ouverture ne connaît que : l'**open** de la bougie courante,
  l'état des bougies fermées et leur ATR. Le high/low de la bougie courante
  n'entre JAMAIS dans la décision d'annoncer — uniquement dans la mesure du
  résultat (touché ou non).
- L'étude et le runtime exécutent le même code de décision (fonction pure sur
  l'état fermé + open), pattern `macro_context_at`.

## 5. Phase A — reach study (obligatoire avant tout réglage)

`engine/reach_study.py`, 4 ans (1490 j) de 15m, BTC + ETH, cache
`data/cache/`. Pour **chaque bougie** où un niveau P* existe (transition
possible, score au niveau ≥ filtre, pyramiding=0 respecté) :

1. **Distance d'atteinte** : `d = |P* − open| / ATR14` (ATR des bougies
   fermées, bornes de l'état précédent — jamais la bougie courante).
2. **Issue** sur un horizon de 1, 2, 4 et 8 bougies : touché ?, touché et
   confirmé ?, jamais touché ?, scénario invalidé par signal opposé ?
3. **Avantage de fill** quand touché : écart entry-limite vs prix de
   confirmation (même définition que l'étude de touches), en % et en R
   (SL momentum_v1).
4. **Coût d'annulation** quand touché non confirmé (décharge médiane).
5. **Volume d'annonces/jour** par bucket de distance.

Buckets de distance scellés : d ≤ 0,25 / 0,5 / 1,0 / 1,5 / 2,0 ATR.

Fenêtres scellées : **IS = 3 premières années (jours 1→1095)**,
**OOS = année finale (jours 1096→1490)**, warmup dédié avant l'IS (non
compté). L'OOS n'est consommé **qu'une fois**, après que k et N sont figés
sur l'IS.

Critère d'effectif (leçon FIBONACCI.md) : si l'IS contient moins de
**200 annonces filtrées par symbole** pour le bucket retenu, l'étude est
déclarée **non concluante** et l'OOS n'est PAS consommée — le mode anticipatif
est rejeté sans discussion d'effectifs.

**Résultats Phase A IS (2026-09-14, 3 ans, min_score=45, implémentation
`engine/reach_study.py`, 445 tests passants)** :

| Seuil k | n BTC / ETH | Touché N=1 | N=2 | N=4 | N=8 | Fill médian | Vol/jour (N=2) |
|---|---|---|---|---|---|---|---|
| 0,25 | 135 / 180 | 81,5 / 73,3 % | 83,7 / 81,7 % | 85,9 / 87,8 % | 88,9 / 91,7 % | +0,15/+0,16 % | 0,12 / 0,16 |
| 0,50 | 269 / 420 | 66,2 / 58,8 % | 73,2 / 67,9 % | 79,9 / 76,7 % | 85,9 / 82,4 % | +0,17/+0,16 % | 0,23 / 0,34 |
| 1,00 | 600 / 984 | 42,8 / 37,5 % | 52,5 / 48,4 % | 61,7 / 59,2 % | 69,2 / 67,6 % | +0,15/+0,16 % | 0,44 / 0,69 |
| 1,50 | 979 / 1638 | 29,1 / 25,6 % | 38,8 / 35,5 % | 48,8 / 45,6 % | 57,7 / 55,2 % | +0,15/+0,18 % | 0,68 / 1,06 |
| 2,00 | 1396 / 2356 | 21,5 / 18,7 % | 29,4 / 27,0 % | 39,8 / 36,3 % | 49,1 / 46,6 % | +0,16/+0,18 % | 0,92 / 1,47 |

Lecture : confirmation/touche stable ~41-51 % sur tous les couples
(cohérent avec l'étude de touches ~50 %) ; décharge médiane −0,12/−0,22 % ;
avantage de fill médian +0,14/+0,18 % (p75 +0,24/+0,57 %) → critère 2 KO
sur tous les couples (amendement v1.1 ci-dessus). no_fill et invalidation
< 3,5 % partout.

**Figage de (k, N) sur l'IS, avant consommation de l'OOS** :
**k = 0,50 ATR, N = 2 bougies (30 min)**. Arguments structurants (jamais le
meilleur bucket isolé) :
- k = 0,50 est le plus petit seuil passant l'effectif (>= 200) sur les DEUX
  symboles (269 / 420) — k = 0,25 échoue l'effectif BTC (135) ;
- taux de toucher >= 2/3 sur les deux symboles dès N=1 (66,2 / 58,8 %),
  conforté à N=2 (73,2 / 67,9 %) ;
- N = 2 limite la dérive du niveau (l'état des indicateurs bouge à chaque
  clôture : no_fill et invalidation restent ~0 %) tout en ajoutant +7/+9
  points de toucher sur N=1 ;
- volume très faible (0,23 / 0,34 annonces/jour à N=2) : la rareté est un
  choix, cohérent avec le filtre score 45 ;
- taux de toucher affiché dans l'embed : **« ~70 % »** (moyenne IS 70,6 %).

## 6. Critères go/no-go (scellés avant mesure, amendés v1.1)

Le choix de (k, N) se fait sur l'IS uniquement, sur arguments structurants
(courbe taux de toucher vs distance, volume/jour), jamais sur le meilleur
bucket isolé (leçon confluence). Puis l'OOS tranche une fois :

| # | Critère | Seuil |
|---|---|---|
| 1 | Taux de toucher à (k=0,50, N=2), IS **et** OOS, BTC **et** ETH | ≥ 50 % |
| 2 | ~~Avantage de fill médian ≥ +0,3 %~~ **RETRAIT v1.1** : l'avantage n'est plus revendiqué (≈ neutre, +0,14/+0,18 % mesuré) ; il reste mesuré et affiché (§7) |
| 3 | Volume d'annonces filtrées | ≤ 3 / jour en moyenne |
| 4 | Accord inter-symboles | aucun symbole avec taux de toucher < 40 % |

- **GO** (critères 1, 3, 4) → Phase C (runtime), mode anticipatif activable.
- **KO (un seul critère)** → le mode anticipatif est **rejeté** ; le mode
  réactif existant (advance eps) demeure la seule forme de pré-alerte.
  Interdiction d'élargir k ou N « pour rattraper » un critère.

**Verdict OOS (2026-09-14, consommée une seule fois après figage
(k=0,50 ; N=2))** : touché N=2 — BTC 77,8 % (n=63), ETH 73,6 % (n=163) ;
volume 0,15 / 0,37 annonces/jour ; aucun symbole < 40 %. **GO** — critères
1, 3 et 4 validés sur IS ET OOS. Avantage de fill OOS +0,13 / +0,29 %
(≈ neutre, cohérent avec l'amendement v1.1). Le taux de toucher affiché
reste « ~70 % » (IS+OOS poolés : BTC 74,1 %, ETH 69,5 %).

Un « presque 50 % » est un échec (même discipline que le gate macro ×2,0).

## 7. Phase B — verdict et affichage honnête

- Le verdict documente : période, effectifs par bucket, taux de toucher,
  taux de confirmation, avantage médian et distribution (p25/p50/p75), coût
  d'annulation, volume/jour — IS vs OOS, BTC vs ETH.
- Si GO, l'embed de pré-alerte anticipative **affiche le taux de toucher
  mesuré** au seuil (k=0,50, N=2) — « niveau atteint dans ~70 % des cas » —
  et le message « signal validé » affiche l'avantage de fill mesuré comme
  **≈ neutre** (aucun edge d'entrée revendiqué, amendement v1.1) :
  l'utilisateur sait exactement ce que vaut l'information, sans en faire une
  probabilité de gain (interdit sans calibration — skill strategy-design).

## 8. Phase C — intégration runtime (si GO uniquement)

- `ENGINE_ADVANCE_MODE = reactive | anticipative` (défaut **reactive** :
  comportement inchangé, aucune régression possible).
- `ENGINE_ADVANCE_K_ATR = 0.50` et `ENGINE_ADVANCE_HORIZON = 2` (bougies) :
  valeurs figées par l'étude (§5), surchargeables par `.env` sans en changer
  les défauts.
- Backend `/internal/prealert` : payload existant + champs `expires_in`
  (bougies) et `touch_rate` (affichage honnête §7) ; nouveaux `kind=expired`
  (niveau jamais touché à l'horizon) et `kind=confirmed` (« signal validé »,
  amendement v1.1 — envoyé en PLUS du signal officiel, qui reste seul à
  compter pour le pipeline).
- Discord : embed « Ordre limite — expire dans N bougies (≈ N×15 min) » dans
  le salon `DISCORD_ADVANCE_CHANNEL_ID` ; messages « Signal validé »,
  d'annulation et d'expiration distincts et visuellement sans ambiguïté.
- Failsafe : endpoint injoignable = log + abandon (best-effort, aucune
  retry infinie) ; ATR indisponible (warmup) = pas d'annonce.
- Tests obligatoires : annonce à l'ouverture uniquement, non-dérive du
  niveau, expiration exacte à N, annulation touche-non-confirmée, silence si
  signal officiel, anti-spam 1 annonce active par direction, aucun impact
  pipeline officiel (aucune écriture `signals`).

## 9. Compatibilité

- **Règle absolue** respectée : aucun ordre, aucune clé broker/exchange ;
  l'ordre limite est placé manuellement par l'utilisateur.
- **Période d'observation (→ 25/09/2026)** : la feature est désactivée par
  défaut (`ENGINE_ADVANCE_MODE=reactive`) ; elle ne modifie ni le score, ni
  `ENGINE_MIN_SCORE`, ni le pipeline officiel. Un éventuel déploiement
  n'attend pas la fin de la période mais l'activation reste un choix
  explicite dans le `.env`.
- Les métriques de paper trading officiel ne comptent **jamais** les
  pré-alertes : un trade anticipatif rempli manuellement n'est pas suivi par
  le système (l'utilisateur gère sa position lui-même).
