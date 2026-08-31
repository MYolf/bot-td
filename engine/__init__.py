"""Moteur de signaux local (alternative gratuite à TradingView).

Récupère les bougies via l'API publique Binance (données de marché
uniquement — aucune clé, aucun ordre, conforme à la règle « signalisation
seule » du projet), évalue Momentum V1 à la clôture de bougie et POST le
même JSON que TradingView vers le webhook du backend.
"""
