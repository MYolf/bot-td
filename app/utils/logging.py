"""Configuration des logs.

Format conforme au Projet.md (Phase 19) :
    2026-08-22 22:14:03 INFO TradingView webhook received

Règle absolue : ne jamais logger de secret (token Discord, secret webhook,
mot de passe de base de données).
"""

import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    """Configure le logging racine de l'application."""
    logging.basicConfig(level=level.upper(), format=FORMAT, datefmt=DATE_FORMAT)
    # Réduire le bruit des bibliothèques tierces.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
