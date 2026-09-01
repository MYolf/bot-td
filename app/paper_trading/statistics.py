"""Statistiques de paper trading (Phase 21).

Métriques en R-multiples (voir skill backtesting) : comparables entre
stratégies indépendamment du capital. Rappel : un échantillon faible
(< 30 trades) n'a aucune signification statistique.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PERCENT = Decimal("0.1")
R_PRECISION = Decimal("0.01")

SPARK_CHARS = "▁▂▃▄▅▆▇█"


@dataclass(frozen=True)
class PerformanceStats:
    """Statistiques dérivées des résultats (en R) des positions clôturées."""

    total: int
    wins: int
    losses: int
    win_rate: Decimal  # en %
    total_r: Decimal
    avg_r: Decimal  # = expectancy
    median_r: Decimal
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
            median_r=Decimal("0"),
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

    tri = sorted(results)
    if total % 2 == 1:
        median = tri[total // 2]
    else:  # nombre pair : moyenne des deux valeurs centrales
        median = (tri[total // 2 - 1] + tri[total // 2]) / Decimal("2")

    return PerformanceStats(
        total=total,
        wins=len(wins),
        losses=len(losses),
        win_rate=_round(Decimal(len(wins)) * 100 / Decimal(total)),
        total_r=_round(total_r),
        avg_r=_round(total_r / Decimal(total)),
        median_r=_round(median),
        best_r=_round(max(results)),
        worst_r=_round(min(results)),
        profit_factor=_round(profit_factor) if profit_factor is not None else None,
        max_drawdown_r=_round(max_drawdown),
    )


def paper_breakdown(
    rows: list[tuple[Decimal, str, str]],
) -> tuple[dict[str, PerformanceStats], dict[str, PerformanceStats]]:
    """Ventile les trades clôturés par symbole puis par direction.

    ``rows`` : tuples (result_r, symbol, action) fournis par
    ``PaperRepository.closed_rows``. Fonction pure : testable sans base.
    """
    par_symbole: dict[str, list[Decimal]] = {}
    par_direction: dict[str, list[Decimal]] = {}
    for result_r, symbol, action in rows:
        par_symbole.setdefault(symbol, []).append(result_r)
        par_direction.setdefault(action, []).append(result_r)
    return (
        {k: compute_stats(v) for k, v in sorted(par_symbole.items())},
        {k: compute_stats(v) for k, v in sorted(par_direction.items())},
    )


def equity_sparkline(results: list[Decimal], width: int = 20) -> str:
    """Courbe d'équité textuelle (R cumulé, dans l'ordre des clôtures).

    Vide si moins de 2 trades. Échantillonnage régulier sur ``width`` points,
    normalisation min/max sur les blocs Unicode. Fonction pure.
    """
    if len(results) < 2:
        return ""
    cumul: list[Decimal] = []
    running = Decimal("0")
    for r in results:
        running += r
        cumul.append(running)
    step = len(cumul) / width
    sampled = [cumul[min(len(cumul) - 1, int(i * step))] for i in range(width)]
    lo, hi = min(sampled), max(sampled)
    span = (hi - lo) or Decimal("1")
    rank = len(SPARK_CHARS) - 1
    return "".join(
        SPARK_CHARS[int((v - lo) * rank / span)] for v in sampled
    )
