"""Filtre gratuit (aucun appel à Claude).

Rejette, dans l'ordre : déjà vendue / réservée, hors catégorie, hors de France, annonce d'acheteur,
mot-clé exclu, hors budget ou prix suspect, absence de signal si exigé.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .models import Ad
from .utils.location import foreign_country, is_overseas
from .utils.prices import has_foreign_currency
from .utils.products import game_or_accessory, matches_query, part_or_accessory
from .utils.text import first_keyword, normalize

BRAND_SIGNAL = "marque recherchée :"

log = logging.getLogger(__name__)


def categorize(ad: Ad, categories: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Catégorie de l'annonce : le titre prime sur la description (qui cite souvent d'autres modèles)."""
    for text in (normalize(ad.title), normalize(ad.description[:300])):
        for cat in categories:
            if first_keyword(text, cat.get("keywords")):
                return cat
    return None


def location_problem(ad: Ad, location_cfg: dict[str, Any]) -> str | None:
    """Raison de rejet liée à la localisation, ou None si l'annonce est en France."""
    if not location_cfg.get("france_only", True):
        return None
    text = f"{ad.title} {ad.description} {ad.location or ''}"
    country = foreign_country(text)
    if country:
        return f"hors de France ({country})"
    if has_foreign_currency(text):
        return "hors de France (devise étrangère)"
    if location_cfg.get("exclude_overseas", True) and is_overseas(f"{ad.location or ''} {ad.description}"):
        return "hors de France métropolitaine (DOM-TOM)"
    return None


def sold_problem(ad: Ad, filters: dict[str, Any]) -> str | None:
    """Annonce déjà vendue, réservée ou en cours d'achat (indiqué par le site, le titre ou la description)."""
    if not ad.available:
        return "déjà vendue ou réservée"
    title = re.sub(r"[^a-z0-9' ]+", " ", normalize(ad.title)).split()
    edges = " ".join(title[:1] + title[-1:])  # « vendu pour pièces » ne doit pas compter
    kw = first_keyword(edges, filters.get("sold_title_keywords"))
    if kw:
        return f"déjà vendue ou réservée ({kw})"
    kw = first_keyword(normalize(ad.description), filters.get("sold_phrases"))
    if kw:
        return f"déjà vendue ou réservée ({kw})"
    return None


def check(ad: Ad, config: dict[str, Any]) -> str | None:
    """Raison du rejet, ou None si l'annonce passe. Renseigne `ad.category` et `ad.signals`."""
    filters = config["filters"]
    reason = sold_problem(ad, filters)
    if reason:
        return reason
    words = filters.get("query_words")  # recherche ponctuelle : les sites renvoient aussi des articles voisins
    if words and not matches_query(f"{ad.title} {ad.description}", words):
        return "hors sujet (aucun mot de la recherche)"
    cat = categorize(ad, config["categories"])
    if cat is None:
        if filters.get("drop_uncategorized", True):
            return "hors catégorie"
        cat = {"name": "Autre", "max_budget": filters.get("default_max_budget", 100)}
    ad.category = cat["name"]

    reason = location_problem(ad, config["location"])
    if reason:
        return reason

    title_n = normalize(ad.title)
    full_n = f"{title_n} {normalize(ad.description)}"

    kw = first_keyword(title_n, filters.get("buyer_ad_keywords"))
    if kw:
        return f"annonce d'acheteur ({kw})"

    kw = first_keyword(full_n, filters.get("global_exclude_keywords", []) + cat.get("exclude_keywords", []))
    if kw:
        return f"mot-clé exclu : {kw}"

    # Critères éliminatoires choisis avant une recherche (livraison, type de vendeur, prix max absolu).
    if filters.get("delivery") == "ship" and not ad.shippable:
        return "pas de livraison"
    wanted, kind = filters.get("seller_type"), (ad.seller or {}).get("type")
    if wanted in ("pro", "particulier") and kind and kind != wanted:  # type inconnu : gardée
        return "vendeur professionnel" if kind == "pro" else "vendeur particulier"
    total = ad.total_price
    hard_max = filters.get("max_price")
    if hard_max is not None and total is not None and total > float(hard_max):
        return f"au-dessus de votre prix max ({total:.0f} € > {float(hard_max):.0f} €)"
    if total is None:
        if not filters.get("keep_unknown_price", True):
            return "prix inconnu"
    else:
        # Prix max choisi pour une recherche : il remplace le budget de la catégorie.
        ignore = hard_max is not None or filters.get("ignore_category_budget")
        max_budget = None if ignore else cat.get("max_budget")
        if max_budget is not None and total > float(max_budget):
            return f"hors budget ({total:.0f} € > {max_budget} €)"
        min_price = float(cat.get("min_price", filters.get("min_price", 0)) or 0)
        if ad.price is not None and ad.price < min_price and ad.price_mode != "enchere":
            return f"prix suspect ({ad.price:.0f} € < {min_price:.0f} €)"

    candidates = cat.get("include_keywords", []) + filters.get("deal_signals", [])
    ad.signals = [k for k in dict.fromkeys(candidates) if first_keyword(full_n, [k])]
    if cat.get("require_include") and not ad.signals:
        return "aucun signal de panne simple ni de bonne affaire"
    kind = game_or_accessory(ad.title)
    if kind:  # dit à l'IA de ne pas estimer le prix de la console citée dans le titre
        ad.signals.append(f"{kind} (pas la console elle-même)")
    elif part := part_or_accessory(ad.title):  # « Ventilateur ThinkPad T480 » : pas le prix du PC
        kind = part
        ad.signals.append(f"{part} (pas l'appareil complet)")
    brand = first_keyword(title_n, filters.get("desirable_brands", []))
    if brand and not kind:  # marque recherchée (qualité, cote stable) : se revend plus vite et plus cher
        ad.signals.append(f"{BRAND_SIGNAL} {brand}")
    return None


def desirable_brand(ad: Ad) -> str | None:
    """La marque recherchée repérée dans le titre (signal posé par `check`), sinon None."""
    return next((s.split(":", 1)[1].strip() for s in ad.signals if s.startswith(BRAND_SIGNAL)), None)


def priority(ad: Ad) -> tuple[int, float]:
    """Ordre d'analyse : signaux d'abord, puis prix croissant (utile si le budget IA s'épuise en cours de lot)."""
    return -len(ad.signals), ad.total_price if ad.total_price is not None else 1e9
