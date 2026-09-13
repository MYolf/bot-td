# MACRO.md — Macro Risk Engine (spec scellée)

Version 1.0 — 2026-09-13. Décisions figées **avant toute mesure** (même discipline
que CONFLUENCE.md et FIBONACCI.md). Toute modification de cette spec doit être
motivée et documentée ; les seuils et fenêtres ci-dessous ne sont **pas** des
paramètres à optimiser.

## 1. Objectif

Filtre de **risque temporel** : détecter qu'un signal technique apparaît à
proximité d'un événement macroéconomique US majeur, moments où le comportement
du marché (volatilité, liquidité, faux breakouts) peut devenir anormal.

Le Macro Risk Engine est un **filtre**, jamais une stratégie directionnelle :

- PAS de « news positive = BUY » ;
- PAS de prédiction du sens du mouvement ;
- il répond uniquement : « est-ce un mauvais moment pour laisser passer ce
  setup ? ».

## 2. Périmètre (validé, réduit volontairement)

| Sujet | Décision |
|---|---|
| Types d'événements v1 | **FOMC, CPI, NFP, PPI** (ISM : liste d'attente, tranché par la Phase A) |
| Données utilisées par la décision | **`scheduled_at` uniquement** (planning connu à l'avance) |
| Forecast / actual | **Jamais dans la décision.** Recherche uniquement, phase ultérieure éventuelle |
| DXY | **Rejeté** (pas d'intraday fiable gratuit, corrélation instable, redondant avec la volatilité de BTC lui-même) |
| US 10Y | **Rejeté pour la prod** (idem) ; au mieux variable de recherche quotidienne FRED |
| Jobless Claims, Minutes, discours hors FOMC, événements non-US | **Rejetés** (bruit / source non fiable) |
| Impact sur le score technique | **Aucun, jamais** — le score reste purement technique |
| Impact sur les positions ouvertes | **Aucun** — SL/TP existants gèrent déjà la traversée d'événements |

## 3. Niveaux et fenêtres (a priori, figées)

Heures conventionnelles US (America/New_York) : CPI, NFP, PPI, GDP = 8h30 ;
FOMC = 14h00.

| Type | EXTREME (annotation, routage salon dédié) | HIGH (annotation, routage salon dédié) | Au-delà |
|---|---|---|---|
| FOMC | T−15 → T+45 (la conférence de presse prolonge la fenêtre) | T−60 → T+120 | LOW |
| CPI, NFP, PPI | T−15 → T+30 | T−30 → T+60 | LOW |

États possibles : `LOW`, `HIGH`, `EXTREME`, `UNKNOWN` (données macro
absentes/périmées/incohérentes → traité comme LOW + log ; c'est le failsafe
rendu explicite).

Règles :
- niveau = maximum (pire) sur tous les événements considérés ;
- plusieurs événements proches : on retient le plus contraignant ;
- distances mesurées en minutes signées (négatif = avant l'événement).

Ces fenêtres ont été choisies **a priori** (bon sens + impact documenté des
annonces). Elles ne seront PAS ajustées sur les résultats de l'étude — un
ajustement serait de l'overfitting sur un échantillon minuscule.

## 4. Source de données : fichier curated, zéro dépendance runtime

- **Production** : `data/macro/events.json`, versionné dans le repo. Les
  calendriers BLS/Fed sont publiés ~1 an à l'avance et quasi immuables.
  Aucun appel réseau au moment de la décision → le failsafe ultime est
  qu'il n'y a rien qui puisse tomber en panne.
- **Génération** : `python -m engine.macro.generate` — dates de release
  CPI/NFP/PPI via l'API FRED (`FRED_API_KEY`, gratuite ; résolution dynamique
  des `release_id` par nom), dates de décision FOMC curated (constante du
  code, décision = 2e jour de réunion). Vérification trimestrielle.
- **Historique pour études** : même générateur sur plage de dates passées ;
  cache local (jamais commité pour les grosses plages).

Format du fichier (dates US, heure ET optionnelle — défaut = heure
conventionnelle du type) :

```json
[{"event_type": "CPI", "date": "2026-09-10", "time_et": "08:30"}, ...]
```

Limitation assumée : FRED donne la **date** de release observée (gère les
reports type shutdown), l'heure est conventionnelle. Suffisant : les heures
BLS/Fed sont stables depuis des décennies.

## 5. Fuseaux horaires

- Stockage et calcul : **UTC exclusivement** ; conversion en America/New_York
  **une fois, à la génération** (zoneinfo gère l'heure d'été/hiver).
- Affichage Discord : Europe/Paris, uniquement à la présentation.
- Tests obligatoires de non-régression : 8h30 ET = 12h30 UTC en juillet (EDT)
  mais 13h30 UTC en janvier (EST) ; 14h00 ET = 18h00/19h00 UTC selon DST.

## 6. Anti-lookahead

- La décision n'utilise que `scheduled_at`, connu des semaines à l'avance :
  lookahead impossible **par construction**.
- `actual`/`forecast` jamais lus par le chemin de décision (même pas après
  l'événement).
- `macro_context_at(events, ts)` est une **fonction pure sans I/O** : le
  backtest et la production exécutent exactement le même code.
- Test d'ablation : un événement futur du fichier ne peut jamais influencer
  un timestamp passé.

## 7. Failsafe

| Panne | Comportement |
|---|---|
| Fichier absent / JSON invalide | `UNKNOWN` (≈ LOW), log d'avertissement, momentum_v1 inchangé |
| Entrée incohérente (type inconnu, date invalide) | Entrée écartée + log |
| Doublon | Dédupliqué (type + instant) |
| Heure absente | Heure conventionnelle du type |

Le Macro Risk Engine est **injecté** dans `SignalEngine` (même pattern que
`Fetcher`/`Sender`), par défaut pass-through : momentum_v1 est structurellement
incapable d'être cassé par la macro.

## 8. Phase A — Event study (GATE, obligatoire avant tout le reste)

Question : « quels événements changent réellement le comportement du marché ? »

Par type (FOMC/CPI/NFP/PPI ± ISM), symbole (BTCUSDT, ETHUSDT), timeframe
(15m, 1H), 4 ans d'historique :

- par bougie : amplitude `(high−low)/open` en %, volume, |retour| ;
- fenêtres : pré `[T−60, T−15)`, **cœur `[T−15, T+30)`**, post `[T+30, T+120)` ;
- une bougie appartient à une fenêtre si son intervalle `[open, close)` a une
  intersection non vide avec la fenêtre ;
- baseline : bougies de la **même minute de journée et du même jour de
  semaine**, sur des jours **sans aucun événement suivi** (contrôle du cycle
  journalier et hebdomadaire du crypto) ;
- métrique : rapport des **médianes** (événement / baseline).

**Critère de gate (scellé avant mesure)** : la médiane d'amplitude du cœur
`[T−15, T+30)` doit être **≥ 2× la baseline** pour FOMC et pour CPI, sur
BTCUSDT **et** ETHUSDT en 15m.

- Gate OK → le blocage EXTREME est justifié par l'élévation de volatilité.
- Gate KO → **NO-GO définitif du blocage** ; au maximum un affichage
  informatif (HIGH), sans action.

**Résultat (2026-09-13, 4 ans BTC+ETH 15m)** : **GATE KO** — 3 des 4 couples
sous le seuil ×2,0 (FOMC BTC ×1,96, CPI BTC ×1,84, CPI ETH ×1,97 ; FOMC ETH
×2,1 seul validé). Protocole scellé appliqué sans ajustement (« presque 2 »
est le piège classique). Conclusions conservées : hiérarchie réelle
FOMC > CPI > NFP >> PPI (PPI ~×1,1 = non distinguable du bruit), fenêtre
post-FOMC étendue justifiée (conférence de presse ×2,1-2,4). Décisions
utilisateur : blocage définitivement abandonné, display-only FOMC/CPI/NFP
validé, aucun DXY/US10Y, aucun changement au score Momentum V1.

## 9. Phase B — Fréquence et ablation

1. Compter les trades momentum_v1 (MIN_SCORE=45) ouverts dans les fenêtres
   EXTREME/HIGH sur 4 ans (ablation par timestamp de bougie, avant
   `simulate_trades` — pattern `momentum_study.py`).
2. Issue à écrire noir sur blanc à l'avance :
   - n bloqué < ~15 au total → l'ablation est déclarée **statistiquement non
     concluante par principe** ; la justification du blocage repose
     uniquement sur le gate de la Phase A. Interdiction d'élargir les
     fenêtres pour « rattraper » des effectifs.
   - n suffisant → baseline vs filtré via `engine/validation.py`
     (IS/OOS scellé, walk-forward, brut/taker/maker) + tableau complet
     (trades bloqués, pertes évitées, gagnants perdus, delta expectancy,
     delta drawdown).

## 10. Phase C — Intégration production (display-only, décision finale 2026-09-13)

Le blocage automatique est **définitivement abandonné** (gate KO §8) : la
seule intégration retenue est le display-only, sans `MACRO_MODE` — il n'existe
et n'existera pas de mode block.

- `MACRO_ENABLED=true` (engine) : le runner annote chaque signal émis via
  `macro_context_at` (pure, même code backtest/production, anti-lookahead §6).
- `MACRO_TYPES` défaut `["FOMC","CPI","NFP"]` (PPI exclu du display, §8).
- Annotation uniquement si le contexte est HIGH ou EXTREME ; LOW/UNKNOWN ne
  modifient rien (UNKNOWN = failsafe §7, fichier absent/illisible).
- Payload webhook : champs optionnels `macro_level` / `macro_note` ;
  schéma backend rétrocompatible.
- Backend : colonnes `signals.macro_level` / `signals.macro_note` (migration
  `f4a9c2d7e1b8`, observabilité uniquement, jamais dans le chemin de
  décision ; la table `macro_events` prévue initialement n'est pas utile :
  le planning versionné `data/macro/events.json` suffit).
- Discord : champ « Macro » dans l'embed de signal (⚠️ HIGH / 🔴 EXTREME,
  note factuelle) ; un signal HIGH/EXTREME est publié dans le salon dédié
  `DISCORD_MACRO_CHANNEL_ID` **à la place** du salon des signaux (choix
  utilisateur 2026-09-13) ; sans salon configuré, il reste dans le salon
  des signaux habituel.
- Compatible avec la période d'observation scellée jusqu'au 25/09/2026 :
  aucun signal n'est bloqué, le score Momentum V1 est inchangé.

## 11. Compatibilité avec le protocole de validation

- Le gate s'injecte au niveau runner, pas dans les stratégies → « SMC » vs
  « SMC + Macro » = même mécanisme d'ablation (présence/absence du gate).
- IS/OOS/walk-forward consommés une seule fois, à la fin.
- Toute conclusion rapportée avec période, effectifs et réserves (skill
  backtesting) ; l'amélioration d'expectancy n'est **pas** promise et ne
  sera probablement pas démontrable (1 à 4 trades bloqués/an attendus) — le
  bénéfice revendiqué est l'hygiène de risque, mesuré par la Phase A.
