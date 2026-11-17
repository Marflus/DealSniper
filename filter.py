"""
filter.py — Pré-filtre strict en Python pur (0 token consommé).

Chaque annonce passe, dans l'ordre :
  1. **Catégorisation** : première catégorie de `config.yaml` dont un mot-clé
     apparaît dans le titre (puis dans la description). Sans catégorie →
     rejet si `drop_uncategorized: true`.
  2. **Annonces d'acheteurs** (« recherche », « achète »…) → rejet.
  3. **Mots-clés rédhibitoires** globaux + propres à la catégorie → rejet.
  4. **Budget** : prix + livraison > `max_budget` de la catégorie → rejet ;
     prix < `min_price` (souvent des arnaques ou des « prix à débattre » à 1 €) → rejet.
  5. **Signaux positifs** : `include_keywords` de la catégorie (pannes simples) et
     `deal_signals` globaux (vente urgente, déménagement, facture…), utiles pour
     repérer les objets FONCTIONNELS vendus sous leur cote. Ils sont notés sur
     l'annonce et transmis à Claude ; si `require_include: true`, au moins un
     signal (de l'un ou l'autre type) est obligatoire.

Les comparaisons ignorent la casse et les accents ("Oxydé" == "oxyde").
Note : le module s'appelle `filter` comme demandé ; importez-le avec
`import filter as ad_filter` pour ne pas masquer la fonction native `filter`.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from core import Ad, contains_keyword, normalize

log = logging.getLogger("dealsniper.filter")


def _first_match(text_n: str, keywords: list[str]) -> str | None:
    for kw in keywords or []:
        if contains_keyword(text_n, kw):
            return kw
    return None


def categorize(ad: Ad, categories: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Renvoie la configuration de catégorie correspondant à l'annonce."""
    title_n = normalize(ad.title)
    # Le titre est prioritaire : la description cite souvent d'autres modèles.
    for cat in categories:
        if _first_match(title_n, cat.get("keywords", [])):
            return cat
    desc_n = normalize(ad.description[:300])
    for cat in categories:
        if _first_match(desc_n, cat.get("keywords", [])):
            return cat
    return None


def check(ad: Ad, config: dict[str, Any]) -> str | None:
    """Applique toutes les règles. Renvoie la raison du rejet, ou None si l'annonce passe.

    Effet de bord : renseigne `ad.category` et `ad.signals`.
    """
    filters = config.get("filters", {})
    categories = config.get("categories", [])

    cat = categorize(ad, categories)
    if cat is None:
        if filters.get("drop_uncategorized", True):
            return "hors catégorie"
        cat = {"name": "Autre", "max_budget": filters.get("default_max_budget", 100)}
    ad.category = cat["name"]

    title_n = normalize(ad.title)
    full_n = f"{title_n} {normalize(ad.description)}"

    kw = _first_match(title_n, filters.get("buyer_ad_keywords", []))
    if kw:
        return f"annonce d'acheteur ({kw})"

    kw = _first_match(full_n, filters.get("global_exclude_keywords", []) + cat.get("exclude_keywords", []))
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
        min_price = cat.get("min_price", filters.get("min_price", 0))
        if ad.price is not None and ad.price < float(min_price or 0):
            return f"prix suspect ({ad.price:.0f} € < {min_price} €)"

    # Signaux « panne simple » (catégorie) + signaux « bonne affaire » (globaux : urgent, déménagement…)
    candidates = cat.get("include_keywords", []) + filters.get("deal_signals", [])
    ad.signals = list(dict.fromkeys(k for k in candidates if contains_keyword(full_n, k)))
    if cat.get("require_include") and not ad.signals:
        return "aucun signal de panne simple ni de bonne affaire"
    return None


def apply_filters(ads: list[Ad], config: dict[str, Any]) -> tuple[list[Ad], list[tuple[Ad, str]]]:
    """Sépare les annonces retenues des rejetées (avec la raison du rejet).

    Les annonces retenues sont triées : d'abord celles avec des signaux positifs,
    puis par prix croissant — utile si `max_ads_per_run` tronque la liste.
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

    if rejected:
        stats = Counter(r.split(" (")[0].split(" :")[0] for _, r in rejected)
        log.info("Filtre : %d retenue(s), %d rejetée(s) %s", len(kept), len(rejected), dict(stats))
    else:
        log.info("Filtre : %d retenue(s), 0 rejetée", len(kept))
    for ad, reason in rejected:
        log.debug("  ✗ %-45.45s → %s", ad.title, reason)
    return kept, rejected
