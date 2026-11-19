"""Pré-filtre en Python pur : aucune annonce rejetée ici ne coûte de token.

Règles appliquées dans l'ordre (la première qui échoue rejette l'annonce) :
  1. catégorie : première catégorie dont un mot-clé est dans le titre (puis la description) ;
  2. localisation : France uniquement (pays étranger explicite, devise étrangère, DOM-TOM) ;
  3. annonces d'acheteurs (« recherche », « achète »…) dans le titre ;
  4. mots-clés rédhibitoires (globaux + catégorie) dans titre et description ;
  5. budget : prix + livraison ≤ `max_budget`, et prix ≥ `min_price` (faux prix à 1 €) ;
  6. signaux positifs (`include_keywords` de la catégorie + `deal_signals` globaux),
     obligatoires si la catégorie a `require_include: true`.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from .location import foreign_country, is_overseas
from .models import Ad
from .prices import has_foreign_currency
from .text import first_keyword, normalize

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


def check(ad: Ad, config: dict[str, Any]) -> str | None:
    """Raison du rejet, ou None si l'annonce passe. Renseigne `ad.category` et `ad.signals`."""
    filters = config["filters"]
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

    total = ad.total_price
    if total is None:
        if not filters.get("keep_unknown_price", True):
            return "prix inconnu"
    else:
        max_budget = cat.get("max_budget")
        if max_budget is not None and total > float(max_budget):
            return f"hors budget ({total:.0f} € > {max_budget} €)"
        min_price = float(cat.get("min_price", filters.get("min_price", 0)) or 0)
        if ad.price is not None and ad.price < min_price and ad.price_mode != "enchere":
            return f"prix suspect ({ad.price:.0f} € < {min_price:.0f} €)"

    candidates = cat.get("include_keywords", []) + filters.get("deal_signals", [])
    ad.signals = [k for k in dict.fromkeys(candidates) if first_keyword(full_n, [k])]
    if cat.get("require_include") and not ad.signals:
        return "aucun signal de panne simple ni de bonne affaire"
    return None


def apply_filters(ads: list[Ad], config: dict[str, Any]) -> tuple[list[Ad], list[tuple[Ad, str]]]:
    """Sépare annonces retenues et rejetées (avec raison).

    Les retenues sont triées (signaux d'abord, puis prix croissant) pour que
    un éventuel `max_ads_per_run` garde les plus prometteuses.
    """
    kept: list[Ad] = []
    rejected: list[tuple[Ad, str]] = []
    for ad in ads:
        try:
            reason = check(ad, config)
        except Exception as exc:  # une annonce bizarre ne doit pas arrêter le lot
            reason = f"erreur de filtrage ({exc})"
        if reason:
            rejected.append((ad, reason))
        else:
            kept.append(ad)

    kept.sort(key=lambda a: (-len(a.signals), a.total_price if a.total_price is not None else 1e9))
    stats = Counter(reason.split(" (")[0].split(" :")[0] for _, reason in rejected)
    log.info("Filtre : %d retenue(s), %d rejetée(s) %s", len(kept), len(rejected), dict(stats) if stats else "")
    for ad, reason in rejected:
        log.debug("  ✗ %-50.50s → %s", ad.title, reason)
    return kept, rejected
