"""Moteur de confluence v0 : gâchettes + déclencheur + score.

Architecture à trois niveaux (CONFLUENCE.md) :

- Niveau 1, GÂCHETTES (booléen, non compensable) : ATR défini, tendance 15m
  non opposée, tendance 1H non opposée (dernière bougie 1H CLÔTURÉE
  uniquement, jamais la bougie en cours).
- Niveau 2, DÉCLENCHEUR (obligatoire, famille structure) : liquidity sweep,
  BOS, premier retest d'Order Block ou de FVG. Les indicateurs (RSI, MACD,
  EMA, volume) ne déclenchent JAMAIS — ils qualifient.
- Niveau 3, SCORE /100 : 5 catégories de 20 points (poids égaux v0, ajustés
  uniquement après validation out-of-sample) :
  tendance 15m, tendance 1H, structure & liquidité, momentum, volume.
  Score v1 (étape 4) : la catégorie tendance pénalise la sur-extension
  (prix très au-delà de l'EMA rapide dans le sens du signal) — le régime
  mesuré est mean-reverting, la poursuite de tendance y est perdante.

Profils déclaratifs (``STRATEGY_PROFILES``) : mêmes gâchettes et features,
les paramètres ``triggers`` / ``require_trend_aligned`` /
``require_structure`` définissent trend_v2, smc_v1, breakout_v1.

Le score QUALIFIE un signal (choix des tiers, étude) ; il ne le crée pas.

SL structurel : ancré sous le niveau invalidant (basses récentes, niveau
balayé, bord de zone) avec un tampon de 0.25 x ATR, borné à
[1, 2.5] x ATR de risque. TP : ``tp_r`` x le risque (RR fixe v0).

Contrat anti-lookahead : toutes les features utilisées respectent la
propriété de préfixe ; ``confluence_signals(candles[:t+1])`` est donc
identique aux signaux de ``confluence_signals(candles)`` d'index <= t
(testé en unitaire).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.features import momentum_states, trend_states, volume_states
from engine.indicators import atr as atr_series, ema
from engine.strategy import Candle
from engine.structure import liquidity_sweeps, market_structure
from engine.zones import fair_value_gaps, order_blocks

BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"

# Priorité d'étiquetage quand plusieurs déclencheurs tombent sur la même bougie.
TRIGGER_PRIORITY = ("sweep", "bos", "ob_retest", "fvg_retest")
ALL_TRIGGERS = TRIGGER_PRIORITY


@dataclass(frozen=True)
class ConfluenceParams:
    """Paramètres du moteur v0 (les ajustements passent par la validation OOS)."""

    swing_k: int = 3
    sweep_k: int = 5
    sweep_min_age: int = 10
    atr_len: int = 14
    sl_buffer_atr: float = 0.25
    sl_atr_min: float = 1.0
    sl_atr_max: float = 2.5
    tp_r: float = 2.0
    min_rr: float = 1.5
    structure_window: int = 12
    ema_fast: int = 50
    ema_slow: int = 200
    htf_ema_fast: int = 50
    htf_ema_slow: int = 200
    rvol_len: int = 20
    # --- Profils déclaratifs (CONFLUENCE.md, mêmes features, gâchettes différentes)
    triggers: tuple[str, ...] = ALL_TRIGGERS  # déclencheurs autorisés
    require_trend_aligned: bool = False  # tendance 15m ALIGNÉE (pas juste non opposée)
    require_structure: int = 0  # éléments structurels alignés minimum
    # --- Score v1 anti-extension (étape 4) : pénaliser la poursuite de tendance
    extension_max_atr: float = 2.0  # distance clôture/EMA rapide, en ATR
    extension_penalty: int = 12  # points retirés de la catégorie tendance


@dataclass(frozen=True)
class ConfluenceSignal:
    """Candidat émis par le moteur (le filtrage par position est en aval)."""

    index: int  # bougie de déclenchement
    action: str  # "BUY" / "SELL"
    trigger: str  # "sweep" / "bos" / "ob_retest" / "fvg_retest"
    entry: float
    stop_loss: float
    take_profit: float
    risk_rr: float  # RR effectif
    score: int  # /100
    details: tuple[str, ...]  # une ligne par catégorie (explicabilité)
    candle_open_time: int
    candle_close_time: int


# Profils déclaratifs (CONFLUENCE.md §Stratégies) : même moteur, même score,
# seules les gâchettes et les déclencheurs autorisés changent. Définis A PRIORI
# (aucun réglage sur les données avant validation IS/OOS).
STRATEGY_PROFILES: dict[str, ConfluenceParams] = {
    # Référence complète : tous déclencheurs, gâchettes non opposées.
    "confluence_v0": ConfluenceParams(),
    # Continuation en tendance : BOS dans le sens de la tendance 15m alignée,
    # appuyé par au moins un élément structurel dans la fenêtre.
    "trend_v2": ConfluenceParams(
        triggers=("bos", "ob_retest"), require_trend_aligned=True, require_structure=1
    ),
    # Smart money : liquidité balayée puis reprise (sweeps + retests de zones),
    # contre-tendance locale tolérée si le HTF n'est pas opposé.
    "smc_v1": ConfluenceParams(triggers=("sweep", "fvg_retest")),
    # Compression -> expansion : BOS porté par une structure dense (2 éléments
    # minimum) et une tendance 15m déjà alignée.
    "breakout_v1": ConfluenceParams(
        triggers=("bos",), require_trend_aligned=True, require_structure=2
    ),
}


def resample(candles: list[Candle], bucket_ms: int) -> list[Candle]:
    """Regroupe les bougies en seaux de ``bucket_ms`` (ex. 15m -> 1H).

    Seuls les seaux COMPLETS sont émis : le dernier seau partiel (bougie
    HTF en formation) n'existe pas — anti-lookahead. Le timeframe source
    est déduit des deux premières bougies.
    """
    if len(candles) < 2:
        return []
    source_ms = candles[1].open_time - candles[0].open_time
    if source_ms <= 0 or bucket_ms % source_ms != 0:
        raise ValueError(f"bucket {bucket_ms} incompatible du timeframe {source_ms}")
    per_bucket = bucket_ms // source_ms

    out: list[Candle] = []
    group: list[Candle] = []
    current_bucket: int | None = None
    for candle in candles:
        bucket = candle.open_time // bucket_ms
        if bucket != current_bucket:
            if len(group) == per_bucket:
                out.append(_merge(group))
            group = []
            current_bucket = bucket
        group.append(candle)
    if len(group) == per_bucket:
        out.append(_merge(group))
    return out


def _merge(group: list[Candle]) -> Candle:
    return Candle(
        open_time=group[0].open_time,
        close_time=group[-1].close_time,
        open=group[0].open,
        high=max(c.high for c in group),
        low=min(c.low for c in group),
        close=group[-1].close,
        volume=sum(c.volume for c in group),
    )


def _htf_bias_series(
    candles: list[Candle],
    htf_candles: list[Candle] | None,
    params: ConfluenceParams,
) -> list[str]:
    """Biais de tendance HTF par bougie 15m (dernière bougie HTF clôturée)."""
    if not htf_candles:
        return [NEUTRAL] * len(candles)
    htf_trend = trend_states(htf_candles, params.htf_ema_fast, params.htf_ema_slow)
    out: list[str] = []
    j = 0
    n_htf = len(htf_candles)
    for candle in candles:
        while j < n_htf and htf_candles[j].close_time <= candle.close_time:
            j += 1
        out.append(htf_trend[j - 1].bias if j > 0 else NEUTRAL)
    return out


def _opposite(bias: str, direction: str) -> bool:
    return (direction == BULLISH and bias == BEARISH) or (
        direction == BEARISH and bias == BULLISH
    )


def confluence_signals(
    candles: list[Candle],
    htf_candles: list[Candle] | None = None,
    params: ConfluenceParams | None = None,
) -> list[ConfluenceSignal]:
    """Évalue toutes les bougies : candidats déclencheurs + gâchettes + score.

    Les features sont calculées UNE fois sur tout l'historique — la propriété
    de préfixe garantit l'équivalence avec un calcul bougie par bougie.
    """
    params = params or ConfluenceParams()
    n = len(candles)
    if n < 3:
        return []

    closes = [c.close for c in candles]
    atrs = atr_series(
        [c.high for c in candles], [c.low for c in candles], closes, params.atr_len
    )
    ema_fast_values = ema(closes, params.ema_fast)
    trend = trend_states(candles, params.ema_fast, params.ema_slow)
    momentum = momentum_states(candles)
    volume = volume_states(candles, params.rvol_len)
    htf_bias = _htf_bias_series(candles, htf_candles, params)

    structure = market_structure(candles, params.swing_k)
    sweeps = liquidity_sweeps(
        candles, k=params.sweep_k, atr_len=params.atr_len, min_age=params.sweep_min_age
    )
    gaps = fair_value_gaps(candles, atr_len=params.atr_len)
    blocks = order_blocks(candles, swing_k=params.swing_k, atr_len=params.atr_len)

    # Déclencheurs et éléments structurels indexés par bougie.
    triggers_at: dict[int, list[tuple[str, str, float | None]]] = {}
    structural_elements: list[tuple[int, str]] = []  # (bougie, direction)

    for event in structure.events:
        direction = BULLISH if event.kind == "bos_bullish" else BEARISH
        triggers_at.setdefault(event.index, []).append(("bos", direction, event.broken_level))
        structural_elements.append((event.index, direction))
    for event in sweeps:
        triggers_at.setdefault(event.index, []).append(
            ("sweep", event.direction, event.swept_level)
        )
        structural_elements.append((event.index, event.direction))
    for gap in gaps:
        if gap.first_retest_index is not None:
            triggers_at.setdefault(gap.first_retest_index, []).append(
                ("fvg_retest", gap.direction, None)
            )
            structural_elements.append((gap.first_retest_index, gap.direction))
    for block in blocks:
        if block.first_retest_index is not None:
            triggers_at.setdefault(block.first_retest_index, []).append(
                ("ob_retest", block.direction, None)
            )
            structural_elements.append((block.first_retest_index, block.direction))

    structural_elements.sort()

    def _aligned_elements(index: int, direction: str) -> int:
        low = index - params.structure_window
        return sum(1 for i, d in structural_elements if low <= i <= index and d == direction)

    signals: list[ConfluenceSignal] = []
    for t in sorted(triggers_at):
        entries = [e for e in triggers_at[t] if e[0] in params.triggers]
        if not entries:
            continue  # aucun déclencheur autorisé par le profil sur cette bougie
        directions = {d for _, d, _ in entries}
        if len(directions) != 1:
            continue  # déclencheurs contradictoires sur la même bougie : abstention
        direction = directions.pop()
        a = atrs[t]
        if a is None or a <= 0.0:
            continue  # gâchette volatilité : ATR non défini
        if _opposite(trend[t].bias, direction):
            continue  # gâchette tendance 15m non opposée
        if _opposite(htf_bias[t], direction):
            continue  # gâchette tendance 1H non opposée
        if params.require_trend_aligned and trend[t].bias != direction:
            continue  # gâchette de profil : tendance 15m ALIGNÉE exigée

        candle = candles[t]
        entry = candle.close
        bullish = direction == BULLISH

        # SL structurel : ancre sous (au-dessus pour SHORT) le niveau invalidant.
        anchor_levels: list[float] = [candles[j].low if bullish else candles[j].high for j in range(max(0, t - 2), t + 1)]
        for _, d, level in entries:
            if level is not None and d == direction:
                anchor_levels.append(level)
        if bullish:
            sl = min(anchor_levels) - params.sl_buffer_atr * a
            sl = max(sl, entry - params.sl_atr_max * a)  # risque <= 2.5 ATR
            sl = min(sl, entry - params.sl_atr_min * a)  # risque >= 1 ATR
            risk = entry - sl
            tp = entry + params.tp_r * risk
        else:
            sl = max(anchor_levels) + params.sl_buffer_atr * a
            sl = min(sl, entry + params.sl_atr_max * a)
            sl = max(sl, entry + params.sl_atr_min * a)
            risk = sl - entry
            tp = entry - params.tp_r * risk

        rr = (tp - entry) / risk if bullish else (entry - tp) / risk
        if rr < params.min_rr:
            continue  # gâchette RR minimal

        # --- Score v1 : 5 catégories de 20, poids égaux (CONFLUENCE.md) ---
        elements = _aligned_elements(t, direction)
        if elements < params.require_structure:
            continue  # gâchette de profil : structure minimale exigée
        score_structure = 20 if elements >= 2 else 10

        aligned_trend = trend[t].bias == direction
        # Anti-extension (étape 4) : le drift mesuré est un retour vers la
        # moyenne ; entrer dans le sens de la tendance QUAND le prix est déjà
        # très au-delà de l'EMA rapide, c'est poursuivre — pénalisé.
        fast = ema_fast_values[t]
        extension_atr: float | None = None
        if fast is not None and a > 0.0:
            extension_atr = (closes[t] - fast) / a
        over_extended = (
            extension_atr is not None
            and (extension_atr if bullish else -extension_atr) > params.extension_max_atr
        )
        score_trend15 = 20 if aligned_trend else 10  # opposé = gated
        if aligned_trend and over_extended:
            score_trend15 -= params.extension_penalty
        score_trend1h = 20 if htf_bias[t] == direction else 10  # neutre/amorce

        if momentum[t].bias == direction:
            score_momentum = 14
        elif momentum[t].bias == NEUTRAL:
            score_momentum = 7
        else:
            score_momentum = 0

        rvol = volume[t].rvol
        if rvol is None:
            score_volume = 10
        elif rvol >= 2.0:
            score_volume = 20
        elif rvol >= 1.2:
            score_volume = 12
        else:
            score_volume = 4

        score = score_trend15 + score_trend1h + score_structure + score_momentum + score_volume

        trigger = next(kind for kind in TRIGGER_PRIORITY if any(k == kind for k, _, _ in entries))
        details = (
            f"Declencheur: {trigger} ({direction})",
            f"Tendance 15m: {trend[t].bias}"
            f"{' (sur-extension ' + str(round(extension_atr, 1)) + ' ATR)' if over_extended else ''}"
            f" +{score_trend15}",
            f"Tendance 1H: {htf_bias[t]} +{score_trend1h}",
            f"Structure: {elements} element(s) aligne(s) +{score_structure}",
            f"Momentum: {momentum[t].bias} +{score_momentum}",
            f"Volume: rvol={rvol if rvol is None else round(rvol, 2)} +{score_volume}",
        )

        close_boundary = candle.open_time + (candle.close_time - candle.open_time + 1)
        signals.append(
            ConfluenceSignal(
                index=t,
                action="BUY" if bullish else "SELL",
                trigger=trigger,
                entry=entry,
                stop_loss=sl,
                take_profit=tp,
                risk_rr=rr,
                score=score,
                details=details,
                candle_open_time=candle.open_time,
                candle_close_time=close_boundary,
            )
        )

    return signals
