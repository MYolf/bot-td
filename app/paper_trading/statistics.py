"""Statistiques de paper trading (Phase 21).

Métriques en R-multiples (voir skill backtesting) : comparables entre
stratégies indépendamment du capital. Rappel : un échantillon faible
(< 30 trades) n'a aucune signification statistique.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PERCENT = Decimal("0.1")
R_PRECISION = Decimal("0.01")


@dataclass(frozen=True)
class PerformanceStats:
    """Statistiques dérivées des résultats (en R) des positions clôturées."""

    total: int
    wins: int
    losses: int
    win_rate: Decimal  # en %
    total_r: Decimal
    avg_r: Decimal  # = expectancy
    best_r: Decimal
    worst_r: Decimal
    profit_factor: Decimal | None  # None si aucune perte (division par zéro)
    max_drawdown_r: Decimal


def _round(value: Decimal) -> Decimal:
    return value.quantize(R_PRECISION, rounding=ROUND_HALF_UP)


def compute_stats(results: list[Decimal]) -> PerformanceStats:
    """Calcule les statistiques d'une liste de résultats en R.

    Fonction pure : testable sans base ni Discord.
    """
    total = len(results)
    if total == 0:
        return PerformanceStats(
            total=0,
            wins=0,
            losses=0,
            win_rate=Decimal("0"),
            total_r=Decimal("0"),
            avg_r=Decimal("0"),
            best_r=Decimal("0"),
            worst_r=Decimal("0"),
            profit_factor=None,
            max_drawdown_r=Decimal("0"),
        )

    wins = [r for r in results if r > 0]
    losses = [r for r in results if r <= 0]
    total_r = sum(results, Decimal("0"))

    gains = sum(wins, Decimal("0"))
    pertes = abs(sum(losses, Decimal("0")))
    profit_factor = (gains / pertes) if pertes > 0 else None

    # Max drawdown en R : plus grand écart entre un cumul de départ (peak)
    # et un cumul ultérieur.
    cumul = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for r in results:
        cumul += r
        if cumul > peak:
            peak = cumul
        drawdown = peak - cumul
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    return PerformanceStats(
        total=total,
        wins=len(wins),
        losses=len(losses),
        win_rate=_round(Decimal(len(wins)) * 100 / Decimal(total)),
        total_r=_round(total_r),
        avg_r=_round(total_r / Decimal(total)),
        best_r=_round(max(results)),
        worst_r=_round(min(results)),
        profit_factor=_round(profit_factor) if profit_factor is not None else None,
        max_drawdown_r=_round(max_drawdown),
    )
