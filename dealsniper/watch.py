"""Veille : modèles précis que vous voulez repérer en priorité, quelle que soit leur note.

Une annonce « surveillée » est notifiée même si elle n'atteint pas le seuil habituel, et elle n'est
pas écartée par la catégorie ni par la cote. Chaque veille = des mots (tous présents dans le titre)
et un prix maximum facultatif. Exemple : « thinkpad t480 » à 120 €.
"""

from __future__ import annotations

from typing import Any

from .models import Ad
from .utils.text import first_keyword, normalize


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".")) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def matches(ad: Ad, watchlist: list[dict[str, Any]]) -> bool:
    """Vrai si l'annonce correspond à une veille (tous les mots dans le titre, prix sous le maximum)."""
    title = normalize(ad.title)
    for watch in watchlist or []:
        words = [w for w in normalize(watch.get("query")).split() if w]
        if not words or any(first_keyword(title, [w]) is None for w in words):
            continue
        max_price = _number(watch.get("max_price"))
        if max_price is None or (ad.total_price is not None and ad.total_price <= max_price):
            return True
    return False
