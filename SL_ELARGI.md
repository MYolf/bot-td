# Étude « SL élargi » — momentum_v1, multiplicateur du bracket

> Spec scellée le 2026-09-27, AVANT toute exécution. Engagement Phase 34 :
> aucun raffinement de grille après consultation des résultats.

## 1. Contexte et hypothèse

Diagnostic central des audits (2026-09-02/03) : momentum_v1 15m n'a pas d'edge
brut (≈ 0R sur 4 ans) et les frais taker (~0,12 % aller-retour) pèsent ~0,12R
par trade car le risque par trade est petit (SL = 1 % fixe de l'entrée).

**Hypothèse** : élargir le bracket (SL et TP multipliés par k, RR 1:2 conservé)
(1) dilue les frais en R d'un facteur k, (2) réduit les sorties secouées par le
bruit → win rate ↑. L'expectancy NETTE en taker devient positive si la perte
brute marginale du bracket élargi est plus petite que le gain de frais.

## 2. Grille (figée)

- k ∈ **{1, 2, 3, 4}** — k=1 = contrôle (production actuelle).
- `sl_pct = 0,01 × k`, `tp_pct = 0,02 × k` (`MomentumParams`), RR 1:2 constant.
- Les transitions (conditions d'entrée) sont IDENTIQUES pour tous les k :
  seuls les niveaux de sortie changent. `ENGINE_MIN_SCORE` inchangé.
- Interdit après consultation des résultats : k=5+, k non entier, TP/SL
  asymétriques, tout autre raffinement.

## 3. Périmètre

- BTCUSDT + ETHUSDT, 15m, 1490 jours (≈ 2022-08 → 2026-09).
- Filtre **min_score = 45** (sémantique de production) en PRIMAIRE ;
  min_score = 0 affiché en contexte secondaire.
- Simulateur commun (`confluence_backtest`) : entrée à clôture, pyramiding 0,
  renversement, SL prioritaire si SL+TP sur la même bougie, frais en R
  (brut 0 / taker 0,12 % / maker 0,02 % A-R). Le break-even et les sorties
  partielles de la production ne sont PAS simulés (cohérent avec tous les
  audits précédents).

## 4. Fenêtres scellées

- Dataset figé (cache local du 2026-09-13, antérieur à l'étude — aucun accès
  aux données plus récentes) : 1490 j, **2022-08-15 → 2026-09-13**, 15m.
- **IS** : du début des données jusqu'à J-550 → **2022-08-15 → 2025-03-12**.
- **OOS** : les 550 derniers jours → **2025-03-12 → 2026-09-13**, warmup 20 j.
- IS chronologiquement AVANT OOS. **L'OOS n'est consommé qu'une fois**, pour le
  k figé à l'issue de l'IS.
- Limite documentée : la baseline k=1 est déjà connue des audits 4 ans — c'est
  la dimension « multiplicateur » qui est scellée ici.

## 5. Gates (dans l'ordre, sur l'IS d'abord)

- **G1 (fréquence)** : n ≥ 150 trades IS par symbole pour le k.
- **G2 (edge net)** : expectancy TAKER > 0 sur l'IS, les DEUX symboles.
- **G3 (accord + figeage)** : si plusieurs k passent G1+G2, figer le PLUS PETIT
  (parcimonie : risque par trade moindre). Si aucun k > 1 ne passe G1+G2 :
  REJET immédiat, OOS non consommé.
- **G4 (OOS, une seule consommation)** : expectancy taker OOS > 0 sur les DEUX
  symboles pour le k figé.
- **G5 (supériorité)** : le k figé bat le contrôle k=1 en expectancy taker sur
  l'IS ET l'OOS, sur les DEUX symboles.

## 6. Verdict

- **VALIDÉ** seulement si G1 → G5 tous vrais.
- Tout échec = **REJET SANS RETOUCHE** : momentum_v1 + filtre 45 restent en
  production inchangés, aucune nouvelle variante sans nouvelle hypothèse
  (engagement Phase 34).
- La validation n'entraîne PAS un déploiement automatique : déploiement =
  décision séparée (config `ENGINE_SL_MULT` du moteur, paper trading à
  rediscuter), avec la règle fondamentale inchangée (signalisation uniquement).

## 7. Livrables

- `engine/sl_study.py` (CLI `--stage is|oos`, aucun envoi réseau de signaux).
- `tests/test_engine_sl_study.py`.
- Sorties brutes : `data/sl_study_<symbole>_<stage>.txt` (non versionnés).

## 8. Exécution et verdict (2026-09-27) — REJET (G5)

- **IS** (2022-08-15 → 2025-03-12, min_score 45, taker 0,12 %) :
  BTC positif à tous les k (+0,036 → +0,105R croissant) ; ETH négatif sauf
  k=4 (+0,015R). G1+G2 ne passent ensemble que sur k=4 → **k=4 figé (G3)**.
  G5-IS ✓ (k=4 > k=1 sur les deux symboles).
- **OOS** (2025-03-12 → 2026-09-13, consommé une fois) : BTC k=4 +0,025R
  (n=133) ✓ ; ETH k=4 **+0,00005R** (n=195, PF=1,0) — G4 passe au signe près
  mais **G5 échoue** : k=4 ne bat pas k=1 sur ETH OOS (+0,003R).
- **VERDICT : REJET sans retouche** (grille figée §2 interdit tout raffinement).
  momentum_v1 + filtre 45 restent en production inchangés.
- Leçons : (1) la dilution des frais par bracket élargi est réelle (BTC) mais
  ne crée pas d'accord inter-symboles — ETH reste à l'équilibre quel que soit
  k ; (2) l'hypothèse « win rate ↑ » ne se matérialise pas non plus (ETH OOS
  33,3 % à k=4 contre 37,4 % à k=1) ; (3) meilleur cas net observé partout
  ≤ +0,105R : marginal.
