"""Moteur de simulation LIQUIDITY.md §5-§8 — exécution des 7 configurations
scellées, conventions prudentes incluses.

Cycle : événement 4H (sweep/breakout) -> fenêtre d'activation 1H ->
déclencheur / fill / expiration / annulation -> position (SL, TP) -> suivi
1H -> sortie (SL | TP | temporelle | fin de données).

Ordre de traitement à chaque clôture 1H (instant X = open_time + 1 h) :
1. la bougie 1H [X-1h, X] est traitée (fills, déclencheurs, suivi de
   position) AVANT la confirmation des bougies 4H de clôture <= X — le fill
   d'un ordre limite est physiquement antérieur à une annulation portée par
   la clôture 4H concomitante ;
2. puis chaque bougie 4H de clôture logique <= X est confirmée (état 4H,
   détection d'événements, annulations).

Anti-lookahead : une fenêtre ouverte à la clôture 4H t_s ne peut être
utilisée que par des bougies 1H d'open_time >= close_time(t_s) : la bougie
1H qui clôture en même temps que t_s (open_time < close(t_s)) n'est jamais
éligible.

Interprétations d'implémentation (ambiguïtés de la spec, choix conservateurs
documentés) :
- un sweep détecté pendant une position ouverte est ignoré SANS setup, mais
  CONSOMME son niveau (le pool a physiquement été pris : anti-spam) ;
- la consommation du niveau a lieu à la DÉTECTION de l'événement (trade,
  expiration ou annulation ultérieurs ne re-consomment rien) ;
- un nouvel événement pendant qu'un setup est en attente REMPLACE l'ancien
  setup (un seul setup actif à la fois) ;
- sortie temporelle : close de la bougie 1H fermant exactement 128 h après
  l'instant d'entrée (l'entrée étant la clôture de sa bougie, la bougie de
  fill/déclencheur est la 1re des 128 heures) ;
- un trade non clôturé à la fin des données sort au dernier close (raison
  "EOD") — l'étude pourra l'exclure ;
- instant d'entrée d'un fill limite : inconnu, horodaté à la clôture de la
  bougie de fill (durées légèrement surestimées).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.liquidity.primitives import (
    ACTIVATION_A_1H,
    DISPLACEMENT_ATR_1H,
    ACTIVATION_C_1H,
    CANCEL_ATR,
    CONSUME_ATR,
    FVG_WINDOW_1H,
    POOL_WINDOW,
    H1,
    H4,
    MIN_RR_B,
    RANGE_MIN_ATR,
    ORDER_VALIDITY_B_1H,
    RETEST_ATR,
    SL_BUFFER_ATR,
    SWEEP_BUFFER_ATR,
    TIME_EXIT_1H,
    RangeInfo,
    atr14_1h,
    atr14_4h,
    ema200_4h,
    fvg_zone_1h,
    last_swing_series,
    pool_series,
    range_series,
)
from engine.strategy import Candle

# Les 7 configurations scellées (LIQUIDITY.md §9) — toute autre combinaison
# est rejetée : garde-fou anti-variante non autorisée.
SEALED_CONFIGS: tuple[tuple[str, str], ...] = (
    ("A1", "median"),
    ("A1", "50/50"),
    ("A2", "median"),
    ("A2", "50/50"),
    ("B", "swing"),
    ("C", "measured"),
)


@dataclass(frozen=True)
class SimParams:
    """Paramètres libres §11 (valeurs scellées par défaut). Le plateau de
    robustesse ±20 % (gate 9) injecte des variations ; k=3 n'en fait pas
    partie (définition canonique du projet)."""

    pool_window: int = POOL_WINDOW
    range_min_atr: float = RANGE_MIN_ATR
    sweep_buffer_atr: float = SWEEP_BUFFER_ATR
    sl_buffer_atr: float = SL_BUFFER_ATR
    consume_atr: float = CONSUME_ATR
    activation_a_1h: int = ACTIVATION_A_1H
    activation_c_1h: int = ACTIVATION_C_1H
    fvg_window_1h: int = FVG_WINDOW_1H
    order_validity_b_1h: int = ORDER_VALIDITY_B_1H
    displacement_atr_1h: float = DISPLACEMENT_ATR_1H
    min_rr_b: float = MIN_RR_B
    cancel_atr: float = CANCEL_ATR
    retest_atr: float = RETEST_ATR
    time_exit_1h: int = TIME_EXIT_1H


@dataclass(frozen=True)
class SimConfig:
    """Configuration de simulation : candidate × politique de sortie."""

    candidate: str  # "A1" | "A2" | "B" | "C"
    tp_policy: str  # "median" | "50/50" | "swing" | "measured"

    def __post_init__(self) -> None:
        if (self.candidate, self.tp_policy) not in SEALED_CONFIGS:
            sealed = ", ".join(f"{c}/{p}" for c, p in SEALED_CONFIGS)
            raise ValueError(
                f"Configuration non scellée : {self.candidate}/{self.tp_policy}"
                f" (autorisées : {sealed})"
            )


@dataclass
class TradeResult:
    """Un trade simulé (résultat BRUT en R — les frais sont appliqués en
    post-traitement par l'étude, via ``risk_pct`` et ``entry_type``)."""

    candidate: str
    tp_policy: str
    direction: str  # "long" | "short"
    entry_time_ms: int
    exit_time_ms: int
    entry: float
    exit_avg: float  # sortie pondérée des jambes
    sl: float
    tps: list[float]
    result_r: float
    exit_reason: str  # "TP" | "SL" | "TP1+TP2" | "TP+TIME" | ... | "EOD"
    duration_h: float
    entry_type: str  # "taker" | "maker"
    risk_pct: float  # distance au SL en % du prix d'entrée
    pool: float
    range_median: float
    range_height: float


@dataclass
class _Setup:
    """Fenêtre d'activation ouverte par un événement 4H."""

    candidate: str
    tp_policy: str
    direction: str  # "long" | "short"
    side: str  # "SSL" | "BSL"
    pool: float
    rng: RangeInfo
    atr_4h: float
    sweep_extreme: float  # low (long) / high (short) de la bougie d'événement
    activation_start_ms: int
    bars_left: int  # bougies 1H éligibles restantes
    sl: float | None = None  # A : à la création ; B : à la pose de l'ordre ; C : au déclencheur
    tps: list[float] | None = None
    weights: list[float] | None = None
    order_level: float | None = None  # A2/B : niveau de l'ordre limite
    zone: tuple[float, float] | None = None  # B : zone FVG (invalidation)
    order_bars_left: int = 0  # validité de l'ordre, en bougies 1H restantes


@dataclass
class _Pos:
    """Position ouverte (une seule à la fois)."""

    setup: _Setup
    entry: float
    entry_idx: int  # bougie 1H de fill/déclencheur
    entry_time_ms: int
    entry_type: str
    sl: float
    tps: list[float]
    weights: list[float]
    closed: list[bool] = field(default_factory=list)
    exits: list[float | None] = field(default_factory=list)
    exit_reasons: list[str] = field(default_factory=list)
    exit_ms: int | None = None
    time_exit_idx: int = 0

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    def remaining(self) -> bool:
        return not all(self.closed)


class LiquiditySimulator:
    """Simule UNE configuration scellée sur un couple (4H, 1H) d'un symbole."""

    def __init__(
        self,
        candles_4h: list[Candle],
        candles_1h: list[Candle],
        config: SimConfig,
        params: SimParams | None = None,
    ):
        if any(c.open_time % H4 for c in candles_4h):
            raise ValueError("bougies 4H non alignées (open_time % 4h != 0)")
        if any(c.open_time % H1 for c in candles_1h):
            raise ValueError("bougies 1H non alignées (open_time % 1h != 0)")
        self._c4 = candles_4h
        self._c1 = candles_1h
        self._config = config
        self._p = params if params is not None else SimParams()
        self._atr4 = atr14_4h(candles_4h)
        self._ema200 = ema200_4h(candles_4h)
        self._pools = pool_series(candles_4h, window=self._p.pool_window)
        self._ranges = range_series(
            candles_4h, self._pools, self._atr4, min_width_atr=self._p.range_min_atr
        )
        self._last_high4, self._last_low4 = last_swing_series(candles_4h)
        self._atr1 = atr14_1h(candles_1h)
        self._consumed: dict[str, float | None] = {"SSL": None, "BSL": None}
        self._setup: _Setup | None = None
        self._pos: _Pos | None = None
        self._i4 = 0  # prochaine bougie 4H à confirmer
        self.trades: list[TradeResult] = []

    # ------------------------------------------------------------------ run

    def run(self) -> list[TradeResult]:
        n4 = len(self._c4)
        for j, c1 in enumerate(self._c1):
            close_1h = c1.open_time + H1
            self._process_1h(j, c1)
            while self._i4 < n4 and self._c4[self._i4].open_time + H4 <= close_1h:
                self._on_4h_close(self._i4)
                self._i4 += 1
        # Fin des données : position restante close au dernier close 1H.
        if self._pos is not None and self._pos.remaining():
            last = self._c1[-1]
            self._close_open_legs(self._pos, last.close, last.open_time + H1, "EOD")
            self._record(self._pos)
            self._pos = None
        return self.trades

    # ------------------------------------------------------------ niveau 1H

    def _process_1h(self, j: int, c1: Candle) -> None:
        if self._pos is not None:
            self._track_position(j, c1)
        elif self._setup is not None:
            self._advance_setup(j, c1)

    def _advance_setup(self, j: int, c1: Candle) -> None:
        setup = self._setup
        if c1.open_time < setup.activation_start_ms:
            return  # bougie 1H non éligible (anti-lookahead 4H -> 1H)

        long = setup.direction == "long"

        # --- Phase ordre actif (A2 dès le sweep ; B après le FVG) ---------
        if setup.order_level is not None:
            filled = (
                (c1.low <= setup.order_level)
                if long
                else (c1.high >= setup.order_level)
            )
            if filled:
                self._open_from_order(setup, j, c1)
                return
            # Invalidation de la zone FVG par clôture 1H au travers (B).
            if setup.zone is not None:
                zl, zh = setup.zone
                broken = c1.close < zl if long else c1.close > zh
                if broken:
                    self._setup = None
                    return
            setup.order_bars_left -= 1
            if setup.order_bars_left <= 0:
                self._setup = None  # ordre expiré (niveau déjà consommé)
            return

        # --- Phase déclencheur / recherche --------------------------------
        setup.bars_left -= 1
        if setup.bars_left < 0:
            self._setup = None  # fenêtre d'activation expirée
            return

        if setup.candidate in ("A1", "C"):
            if setup.candidate == "A1":
                trig = c1.close > setup.pool if long else c1.close < setup.pool
            else:  # retest C : wick au niveau + clôture du bon côté
                if long:
                    trig = (
                        c1.low <= setup.pool + self._p.retest_atr * setup.atr_4h
                        and c1.close > setup.pool
                    )
                else:
                    trig = (
                        c1.high >= setup.pool - self._p.retest_atr * setup.atr_4h
                        and c1.close < setup.pool
                    )
            if trig:
                self._open_taker(setup, j, c1)
            return

        # --- B : recherche du FVG de réversion (premier conforme gagne) ---
        zone = fvg_zone_1h(
            self._c1, self._atr1, j, setup.pool, setup.direction,
            displacement_atr=self._p.displacement_atr_1h,
        )
        if zone is None:
            return
        zl, zh = zone
        setup.zone = zone
        setup.sl = (
            min(zl, setup.pool) - self._p.sl_buffer_atr * setup.atr_4h
            if long
            else max(zh, setup.pool) + self._p.sl_buffer_atr * setup.atr_4h
        )
        setup.order_level = (zl + zh) / 2.0  # milieu de zone
        # La bougie du FVG ne peut pas remplir : validité = les
        # ORDER_VALIDITY_B_1H bougies 1H SUIVANTES.
        setup.order_bars_left = self._p.order_validity_b_1h

    def _open_taker(self, setup: _Setup, j: int, c1: Candle) -> None:
        long = setup.direction == "long"
        if setup.candidate == "C":  # SL derrière le retest
            setup.sl = (
                min(c1.low, setup.pool) - self._p.sl_buffer_atr * setup.atr_4h
                if long
                else max(c1.high, setup.pool) + self._p.sl_buffer_atr * setup.atr_4h
            )
        self._pos = _Pos(
            setup=setup,
            entry=c1.close,
            entry_idx=j,
            entry_time_ms=c1.open_time + H1,
            entry_type="taker",
            sl=setup.sl or 0.0,
            tps=list(setup.tps or []),
            weights=list(setup.weights or []),
        )
        self._finalize_open(j)

    def _open_from_order(self, setup: _Setup, j: int, c1: Candle) -> None:
        long = setup.direction == "long"
        entry = setup.order_level or 0.0
        # TP de B : dernier swing 4H confirmé au moment du fill (les 4H
        # clôturant avec cette bougie 1H ne sont PAS encore confirmées).
        if setup.candidate == "B":
            if self._i4 == 0:
                self._setup = None  # aucune référence : trade non pris
                return
            ref = self._last_high4[self._i4 - 1] if long else self._last_low4[self._i4 - 1]
            risk = abs(entry - (setup.sl or entry))
            if ref is None or risk <= 0.0:
                self._setup = None
                return
            if not (ref > entry if long else ref < entry):
                self._setup = None
                return
            if abs(ref - entry) / risk < self._p.min_rr_b:
                self._setup = None  # exigence RR >= 1.2 : trade non pris
                return
            setup.tps = [ref]
            setup.weights = [1.0]
        self._pos = _Pos(
            setup=setup,
            entry=entry,
            entry_idx=j,
            entry_time_ms=c1.open_time + H1,
            entry_type="maker",
            sl=setup.sl or 0.0,
            tps=list(setup.tps or []),
            weights=list(setup.weights or []),
        )
        self._finalize_open(j)
        # Bougie de fill : l'instant du fill est inconnu — convention prudente,
        # le SL est actif dès le fill (le TP, lui, n'est jamais compté sur
        # cette bougie : le suivi normal commence à la bougie suivante).
        pos = self._pos
        assert pos is not None
        sl_hit = (c1.low <= pos.sl) if long else (c1.high >= pos.sl)
        if sl_hit:
            self._close_open_legs(pos, pos.sl, c1.open_time + H1, "SL")
            self._record(pos)
            self._pos = None

    def _finalize_open(self, j: int) -> None:
        pos = self._pos
        assert pos is not None
        pos.closed = [False] * len(pos.tps)
        pos.exits = [None] * len(pos.tps)
        pos.exit_reasons = [""] * len(pos.tps)
        pos.time_exit_idx = j + self._p.time_exit_1h
        self._setup = None  # une position à la fois : setup nettoyé

    # ----------------------------------------------------------- suivi 1H

    def _track_position(self, j: int, c1: Candle) -> None:
        pos = self._pos
        assert pos is not None
        long = pos.setup.direction == "long"
        sl_hit = c1.low <= pos.sl if long else c1.high >= pos.sl

        if sl_hit:  # SL prioritaire si SL et TP touchés sur la même bougie
            self._close_open_legs(pos, pos.sl, c1.open_time + H1, "SL")
            self._record(pos)
            self._pos = None
            return

        multi = len(pos.tps) > 1
        for i, done in enumerate(pos.closed):
            if done:
                continue
            tp = pos.tps[i]
            hit = c1.high >= tp if long else c1.low <= tp
            if hit:
                pos.closed[i] = True
                pos.exits[i] = tp
                pos.exit_reasons[i] = f"TP{i + 1}" if multi else "TP"
                pos.exit_ms = c1.open_time + H1
        if not pos.remaining():
            self._record(pos)
            self._pos = None
            return
        if j == pos.time_exit_idx:  # sortie temporelle au close
            pos.exit_ms = c1.open_time + H1
            for i, done in enumerate(pos.closed):
                if not done:
                    pos.closed[i] = True
                    pos.exits[i] = c1.close
                    pos.exit_reasons[i] = "TIME"
            self._record(pos)
            self._pos = None

    def _close_open_legs(
        self, pos: _Pos, price: float, exit_ms: int, reason: str
    ) -> None:
        for i, done in enumerate(pos.closed):
            if not done:
                pos.closed[i] = True
                pos.exits[i] = price
                pos.exit_reasons[i] = reason
        pos.exit_ms = exit_ms

    def _record(self, pos: _Pos) -> None:
        setup = pos.setup
        total_w = sum(pos.weights) or 1.0
        exit_avg = sum(
            w * (e or 0.0) for w, e in zip(pos.weights, pos.exits)
        ) / total_w
        long = setup.direction == "long"
        result_r = (
            (exit_avg - pos.entry) / pos.risk
            if long
            else (pos.entry - exit_avg) / pos.risk
        )
        exit_ms = pos.exit_ms if pos.exit_ms is not None else pos.entry_time_ms
        self.trades.append(
            TradeResult(
                candidate=setup.candidate,
                tp_policy=setup.tp_policy,
                direction=setup.direction,
                entry_time_ms=pos.entry_time_ms,
                exit_time_ms=exit_ms,
                entry=pos.entry,
                exit_avg=exit_avg,
                sl=pos.sl,
                tps=list(pos.tps),
                result_r=result_r,
                exit_reason="+".join(r for r in pos.exit_reasons if r),
                duration_h=(exit_ms - pos.entry_time_ms) / H1,
                entry_type=pos.entry_type,
                risk_pct=abs(pos.entry - pos.sl) / pos.entry * 100.0,
                pool=setup.pool,
                range_median=setup.rng.median,
                range_height=setup.rng.height,
            )
        )

    # ------------------------------------------------------------ niveau 4H

    def _on_4h_close(self, i: int) -> None:
        c = self._c4[i]
        a = self._atr4[i]
        if a is None:
            return
        self._maybe_cancel_order(c, a)
        if i == 0:
            return
        prev_pools = self._pools[i - 1]
        prev_range = self._ranges[i - 1]

        if self._config.candidate in ("A1", "A2", "B"):
            # Sweep SSL -> LONG ; sweep BSL -> SHORT (LIQUIDITY.md §4.4)
            self._detect_sweep(
                i, c, a, prev_range,
                side="SSL", direction="long", pool=prev_pools.ssl,
            )
            self._detect_sweep(
                i, c, a, prev_range,
                side="BSL", direction="short", pool=prev_pools.bsl,
            )
        else:  # C : breakout en clôture (LIQUIDITY.md §8)
            self._detect_breakout(
                i, c, a, prev_range,
                side="BSL", direction="long", pool=prev_pools.bsl,
            )
            self._detect_breakout(
                i, c, a, prev_range,
                side="SSL", direction="short", pool=prev_pools.ssl,
            )

    def _detect_sweep(
        self,
        i: int,
        c: Candle,
        a: float,
        rng: RangeInfo | None,
        *,
        side: str,
        direction: str,
        pool: float | None,
    ) -> None:
        if pool is None or rng is None:
            return
        consumed = self._consumed[side]
        if consumed is not None and abs(pool - consumed) <= self._p.consume_atr * a:
            return  # anti-re-sweep : niveau déjà consommé
        if direction == "long":
            pierced = c.low <= pool - self._p.sweep_buffer_atr * a
            rejected = c.close > pool
        else:
            pierced = c.high >= pool + self._p.sweep_buffer_atr * a
            rejected = c.close < pool
        if not (pierced and rejected):
            return
        # L'événement CONSOMME son niveau (même si ignoré : position ouverte).
        self._consumed[side] = pool
        if self._pos is not None:
            return  # événement pendant position ouverte : ignoré

        long = direction == "long"
        setup = _Setup(
            candidate=self._config.candidate,
            tp_policy=self._config.tp_policy,
            direction=direction,
            side=side,
            pool=pool,
            rng=rng,
            atr_4h=a,
            sweep_extreme=c.low if long else c.high,
            activation_start_ms=c.open_time + H4,
            bars_left=(
                self._p.fvg_window_1h
                if self._config.candidate == "B"
                else self._p.activation_a_1h
            ),
        )
        if setup.candidate in ("A1", "A2"):
            base = min(setup.sweep_extreme, pool) if long else max(
                setup.sweep_extreme, pool
            )
            setup.sl = (
                base - self._p.sl_buffer_atr * a
                if long
                else base + self._p.sl_buffer_atr * a
            )
            if self._config.tp_policy == "median":
                setup.tps = [rng.median]
                setup.weights = [1.0]
            else:  # 50/50 : médiane + pool opposé
                setup.tps = [rng.median, rng.bsl if long else rng.ssl]
                setup.weights = [0.5, 0.5]
            if setup.candidate == "A2":  # ordre limite posé dès le sweep
                setup.order_level = (
                    pool + self._p.sl_buffer_atr * a
                    if long
                    else pool - self._p.sl_buffer_atr * a
                )
                setup.order_bars_left = self._p.activation_a_1h
        self._setup = setup  # remplace un éventuel setup en attente

    def _detect_breakout(
        self,
        i: int,
        c: Candle,
        a: float,
        rng: RangeInfo | None,
        *,
        side: str,
        direction: str,
        pool: float | None,
    ) -> None:
        if pool is None or rng is None:
            return
        consumed = self._consumed[side]
        if consumed is not None and abs(pool - consumed) <= self._p.consume_atr * a:
            return
        ema_now = self._ema200[i]
        ema_prev = self._ema200[i - 1]
        if ema_now is None or ema_prev is None:
            return
        if direction == "long":
            broke = c.close > pool
            context = ema_now > ema_prev and c.close > ema_now
        else:
            broke = c.close < pool
            context = ema_now < ema_prev and c.close < ema_now
        if not (broke and context):
            return
        self._consumed[side] = pool
        if self._pos is not None:
            return
        long = direction == "long"
        self._setup = _Setup(
            candidate="C",
            tp_policy="measured",
            direction=direction,
            side=side,
            pool=pool,
            rng=rng,
            atr_4h=a,
            sweep_extreme=c.low if long else c.high,
            activation_start_ms=c.open_time + H4,
            bars_left=self._p.activation_c_1h,
            tps=[pool + rng.height if long else pool - rng.height],
            weights=[1.0],
        )

    def _maybe_cancel_order(self, c: Candle, a: float) -> None:
        """Annule l'ordre en attente si la clôture 4H franchit le pool de
        CANCEL_ATR x ATR (sweep échoué = vraie cassure, §6-§7)."""
        setup = self._setup
        if setup is None or setup.order_level is None:
            return
        long = setup.direction == "long"
        failed = (
            c.close < setup.pool - self._p.cancel_atr * a
            if long
            else c.close > setup.pool + self._p.cancel_atr * a
        )
        if failed:
            self._setup = None
