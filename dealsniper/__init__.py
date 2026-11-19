"""Deal Sniper — chasseur de bonnes affaires électroniques (à réparer ou sous la cote).

Chaîne de traitement :

    sources (emails d'alerte, RSS) → filters (Python pur) → analyzer (Claude) → notifier (Telegram/Discord)

Point d'entrée : ``python -m dealsniper`` (voir ``dealsniper/cli.py``).
"""

__version__ = "2.0.0"
