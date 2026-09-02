# PLAN D'ÉVALUATION — momentum_v1 15m + filtre 45

> **LIRE CE FICHIER LE VENDREDI 25 SEPTEMBRE 2026** (fin des 3 semaines d'observation,
> démarrées le 2026-09-03). Récupérer le récap hebdo de la veine ou taper `/stats`
> dans Discord, puis appliquer PLAN VERT ou PLAN ROUGE ci-dessous.

Config évaluée : momentum_v1, 15m, BTC+ETH, `ENGINE_MIN_SCORE=45`, paper trading
(simulation — aucun ordre réel, règle absolue du projet).

---

## Rappel : le bon critère n'est PAS le win rate

momentum_v1 vise un Risk/Reward de **1:2** (TP = +2R, SL = −1R), donc :

- **Seuil de rentabilité : 33,3 % de trades gagnants.** À 4/10 wins, la semaine est
  positive (+1R). À 3/10, légèrement négative (−1R).
- Win rate attendu (audit 4 ans, filtre 45) : **35-37 %**. Un « 6 gagnants sur 10 »
  n'est PAS l'objectif : avec 1:2, ce serait du bruit de petit échantillon.
- **Le critère qui compte : l'expectancy en R** (champ « Résultat de la semaine » du
  récap, ou `/stats` → Paper trading (R)).
- Volume attendu : ~5-10 trades/semaine sur BTC+ETH → **15-30 trades sur 3 semaines**.
  Moins de 15 trades clôturés = aucune conclusion possible (variance).

---

## PLAN VERT — conforme ou meilleur que l'audit

**Critères (les 3 doivent être vrais) :**
1. Total R ≥ 0 sur les ~3 semaines ;
2. Win rate ≥ 30 % ;
3. Au moins 15 trades clôturés.

**Actions :**
1. **On ne touche à rien** — aucune modification de config. Ajuster sur une fenêtre
   courte, c'est construire un overfitting (leçon des phases 31-32).
2. Prolonger la mesure jusqu'à **3 mois** (fin novembre 2026) : durée minimale pour
   distinguer un edge d'une série chanceuse (~60-120 trades).
3. Point de contrôle vers le **15/10/2026** avec Claude : relance de l'audit sur la
   même période pour comparer live vs backtest (détection précoce de divergence).

---

## PLAN ROUGE — nettement sous l'audit

**Critères (l'un des deux suffit) :**
1. Total R ≤ −5R sur ≥ 15 trades ;
2. Divergence marquée vs backtest (ex. win rate < 25 % alors que l'audit dit 35 %).

**Actions, DANS L'ORDRE — on ne saute pas aux conclusions :**

1. **Vérifier la technique d'abord** (~30 min avec Claude) : signaux émis par le
   moteur vs reçus dans Discord vs clôturés en base. Une bougie manquée, un
   conteneur redémarré, une clôture SL mal détectée imitent un « ça ne marche pas ».
2. **Relancer le backtest sur les mêmes dates** :
   - backtest positif mais live négatif → bug de portage, on le corrige ;
   - les deux négatifs → c'est le marché, pas le code.
3. **Si < 15 trades clôturés : attendre.** Aucune conclusion, c'est de la variance.
4. Si la technique est propre et l'échantillon suffisant, choisir (avec Claude) :
   - **a)** repasser `ENGINE_MIN_SCORE=0` dans le `.env` du VPS si le bucket 45
     sous-performe le non-filtré en live (retour arrière gratuit, une ligne) ;
   - **b)** assumer le statut réel du système : **outil d'observation**, pas de
     profit — et rediriger l'effort vers le vrai sujet (SL plus large pour diluer
     les frais, autres déclencheurs) avec un protocole 4 ans.

**Interdit absolu** : modifier les paramètres (EMA, SL/TP, seuil) sur la base de
3 semaines. Une stratégie ajustée sur une fenêtre courte casse en conditions réelles.

---

## Références

- Audit 15m 4 ans + décision filtre 45 : `AVANCEMENT.md` § « Audit momentum_v1 » et
  § « Option A » (2026-09-02).
- Audit 1H rejeté : `AVANCEMENT.md` § « Audit momentum_v1 sur 1H » (2026-09-03).
- Sorties brutes des audits : `data/btc_momentum_audit.txt`, `data/eth_momentum_audit.txt`.
- Lecture hebdo : récap automatique chaque vendredi 22h00 (Europe/Paris) dans le
  salon récap ; suivi continu : commandes slash `/stats`, `/lastsignal`, `/signals`.
