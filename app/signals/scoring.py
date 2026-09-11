"""Score de qualité du signal (Phase 26, Projet.md §40).

Barème sur 100 points, composantes optionnelles : une stratégie n'envoie
que ce qu'elle évalue réellement (ex. momentum_v1 n'évalue ni le volume ni
la structure). Le total est TOUJOURS calculé côté backend à partir des
composantes reçues — le client n'envoie jamais un total qu'il aurait fixé
lui-même (même principe que le Risk/Reward).

Important (Projet.md) : le score est un indicateur INTERNE de qualité.
Il n'est pas une probabilité de gain et ne doit jamais être présenté
comme tel (aucune calibration statistique n'a été effectuée).
"""

from app.signals.schemas import TradingViewSignal

# Barème officiel (Projet.md §40) : maximum de points par composante.
# Le total théorique vaut exactement 100.
SCORE_COMPONENT_MAX: dict[str, int] = {
    "score_trend": 20,
    "score_momentum": 20,
    "score_macd": 15,
    "score_volume": 15,
    "score_structure": 20,
    "score_htf": 10,
}


def present_components(signal: TradingViewSignal) -> dict[str, int]:
    """Composantes réellement envoyées par la stratégie (les autres absentes).

    Ex. momentum_v1 n'évalue que trend/momentum/macd : maximum 55 points de
    barème. Sert au recalibrage de l'affichage sur 100 (voir embeds).
    """
    return {
        field: value
        for field in SCORE_COMPONENT_MAX
        if (value := getattr(signal, field)) is not None
    }


def compute_score(signal: TradingViewSignal) -> int | None:
    """Total du score (0-100), ou None si le signal n'envoie aucune composante.

    Une composante absente vaut 0 (la stratégie ne l'évalue pas).
    """
    components = present_components(signal)
    if not components:
        return None
    return sum(components.values())
