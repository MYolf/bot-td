"""Position simulée — réplication du comportement d'alerte de TradingView.

Dans le Pine, ``strategy.entry`` avec ``pyramiding = 0`` ne fait rien quand
une position est déjà ouverte (pas d'ordre => pas d'alerte ``alert_message``),
et ``strategy.exit`` referme la position au SL/TP. Le moteur réplique cette
logique LOCALEMENT pour n'émettre que les signaux qui auraient déclenché un
ordre simulé (et donc une alerte) côté TradingView :

- position ouverte à la clôture de la bougie de signal ;
- fermée si une bougie suivante touche le SL ou le TP (SL prioritaire si
  les deux sont touchés sur la même bougie — hypothèse prudente) ;
- un signal OPPOSÉ renverse la position (fermeture + ouverture => alerte) ;
- un signal du MÊME sens que la position est ignoré (aucune alerte).

Ceci reste une simulation purement locale pour filtrer les alertes : aucune
ordre réel, aucun broker (règle absolue du projet).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.strategy import Candle, MomentumParams, evaluate_momentum_v1

LONG = "long"
SHORT = "short"


@dataclass
class SimulatedPosition:
    side: str  # "long" ou "short"
    entry: float
    stop_loss: float
    take_profit: float


class PositionTracker:
    """État de la position simulée d'un symbole (une seule à la fois)."""

    def __init__(self) -> None:
        self.position: SimulatedPosition | None = None

    def apply_candle(self, candle: Candle) -> str | None:
        """Applique une bougie fermée : ferme la position au SL/TP le cas échéant.

        Retourne "stop_loss", "take_profit" ou None (position inchangée).
        """
        position = self.position
        if position is None:
            return None
        if position.side == LONG:
            if candle.low <= position.stop_loss:
                self.position = None
                return "stop_loss"
            if candle.high >= position.take_profit:
                self.position = None
                return "take_profit"
        else:  # short
            if candle.high >= position.stop_loss:
                self.position = None
                return "stop_loss"
            if candle.low <= position.take_profit:
                self.position = None
                return "take_profit"
        return None

    def would_fill(self, action: str) -> bool:
        """Un ordre simulé s'exécuterait-il ? (plat ou renversement)"""
        if self.position is None:
            return True
        return self.position.side != (LONG if action == "BUY" else SHORT)

    def open(self, action: str, entry: float, stop_loss: float, take_profit: float) -> None:
        self.position = SimulatedPosition(
            side=LONG if action == "BUY" else SHORT,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )


def replay_history(
    candles: list[Candle], params: MomentumParams | None = None
) -> PositionTracker:
    """Reconstruit l'état de la position en rejouant l'historique (sans émettre).

    Utilisé au démarrage : le moteur devient sans état persistant — tout est
    recalculé depuis les bougies Binance.
    """
    params = params or MomentumParams()
    tracker = PositionTracker()
    min_len = params.ema_slow + params.macd_signal + 2
    for i in range(min_len, len(candles)):
        tracker.apply_candle(candles[i])
        result = evaluate_momentum_v1(candles[: i + 1], params)
        if result is not None and tracker.would_fill(result.action):
            tracker.open(
                result.action, result.entry, result.stop_loss, result.take_profit
            )
    return tracker
