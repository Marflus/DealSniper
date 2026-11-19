"""Enrichissement : récupère la description et la réputation du vendeur sur la page de l'annonce.

Les alertes email (leboncoin, Vinted) ne contiennent que titre, prix et ville.
Pour les annonces retenues par le filtre — quelques-unes par passage — on ouvre
la page de l'annonce avec une requête HTTPS ordinaire :
  * un User-Agent honnête (pas de déguisement en navigateur), aucune rotation d'IP ;
  * une requête par annonce, une pause entre deux, aucune nouvelle tentative ;
  * au premier refus (403, captcha…), on s'arrête pour ce passage : l'analyse
    continue avec le titre seul.

Les informations sont lues dans les métadonnées publiques de la page (balises
Open Graph, JSON-LD, données de la page) de façon défensive : un champ absent
est simplement ignoré.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from .models import Ad
from .text import clean_html

log = logging.getLogger(__name__)

USER_AGENT = "DealSniper/2.0 (outil personnel de veille ; 1 requete par annonce)"
_BLOCK_MARKERS = ("captcha-delivery", "datadome", "cf-challenge", "challenge-platform")
_RATING_KEYS = ("rating_score", "feedback_reputation", "reputation", "ratingValue", "rating")
_COUNT_KEYS = ("rating_count", "feedback_count", "reviews_count", "reviewCount", "ratingCount")
_SINCE_KEYS = ("registered_at", "account_creation_date", "created_at", "memberSince")


class Blocked(RuntimeError):
    """Le site refuse les requêtes automatiques : on n'insiste pas."""


@dataclass
class PageInfo:
    description: str = ""
    condition: str = ""
    seller_type: str = ""
    seller: dict[str, Any] = field(default_factory=dict)


def _walk(node: Any, path: tuple[str, ...] = ()):
    """Parcourt un JSON en renvoyant (chemin, clé, valeur) pour chaque feuille."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (str(key),))
            if not isinstance(value, (dict, list)):
                yield path, str(key), value
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, path)


def _first(data: Any, keys: tuple[str, ...], within: tuple[str, ...] = ()) -> Any:
    for path, key, value in _walk(data):
        if key in keys and value not in (None, "") and (not within or any(w in path for w in within)):
            return value
    return None


def parse_page(html: str) -> PageInfo:
    """Description, état et vendeur extraits d'une page d'annonce (champs absents = vides)."""
    info = PageInfo()
    soup = BeautifulSoup(html, "html.parser")

    blobs: list[Any] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            blobs.append(json.loads(script.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        try:
            blobs.append(json.loads(next_data.string))
        except json.JSONDecodeError:
            pass

    for blob in blobs:
        info.description = info.description or clean_html(str(_first(blob, ("body", "description")) or ""))
        condition = _first(blob, ("itemCondition", "condition", "status"), within=("attributes", "offers", "item"))
        info.condition = info.condition or (str(condition).rsplit("/", 1)[-1] if condition else "")
        seller_type = _first(blob, ("type",), within=("owner", "user", "seller"))
        info.seller_type = info.seller_type or (str(seller_type) if seller_type else "")
        owners = ("owner", "user", "seller", "aggregateRating")
        for target, keys in (("rating", _RATING_KEYS), ("reviews", _COUNT_KEYS), ("since", _SINCE_KEYS)):
            if target not in info.seller:
                value = _first(blob, keys, within=owners)
                if value is not None:
                    info.seller[target] = value

    if not info.description:
        meta = soup.find("meta", attrs={"property": "og:description"})
        meta = meta or soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            info.description = clean_html(meta["content"])
    return info


def fetch(url: str, session, timeout: float) -> PageInfo:
    resp = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR"})
    body = resp.text[:200_000] if resp.text else ""
    if resp.status_code in (401, 403, 429) or any(m in body[:5000].lower() for m in _BLOCK_MARKERS):
        raise Blocked(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return parse_page(body)


def _apply(ad: Ad, info: PageInfo) -> bool:
    extra = []
    if info.condition:
        extra.append(f"État : {info.condition}")
    if info.seller_type:
        extra.append(f"Vendeur : {'professionnel' if 'pro' in info.seller_type.lower() else info.seller_type}")
    if info.description:
        extra.append(info.description)
    if info.seller:
        seller = dict(ad.seller or {})
        if info.seller.get("rating") is not None:
            try:
                rating = float(info.seller["rating"])
                # Notes sur 1 (0,96), sur 5 (4,8) ou en % : ramenées en %.
                seller["positive_pct"] = rating * 100 if rating <= 1 else rating * 20 if rating <= 5 else rating
            except (TypeError, ValueError):
                pass
        if info.seller.get("reviews") is not None:
            try:
                seller["reviews"] = int(info.seller["reviews"])
            except (TypeError, ValueError):
                pass
        if info.seller.get("since"):
            seller["since"] = str(info.seller["since"])[:10]
        ad.seller = seller or None
    if not extra:
        return False
    ad.description = " | ".join(extra + ([ad.description] if ad.description else []))
    return True


def enrich(ads: list[Ad], cfg: dict[str, Any]) -> dict[str, Any]:
    """Complète les annonces des sources concernées. Renvoie des statistiques (jamais d'exception)."""
    import requests

    stats: dict[str, Any] = {"enriched": 0, "failed": 0, "blocked": {}}
    if not cfg.get("enabled", True):
        return stats
    # eBay : fiche complète via l'API officielle (aucune page web lue).
    from .sources import ebay_api

    stats["enriched"] += ebay_api.enrich_ads(ads, cfg.get("ebay_api") or {})
    sources = set(cfg.get("sources") or [])
    delay = float(cfg.get("delay_seconds", 2))
    timeout = float(cfg.get("timeout", 15))
    with requests.Session() as session:
        for ad in ads:
            if ad.source not in sources or not ad.url_is_stable or ad.source in stats["blocked"]:
                continue
            try:
                if _apply(ad, fetch(ad.url, session, timeout)):
                    stats["enriched"] += 1
            except Blocked as exc:
                stats["blocked"][ad.source] = str(exc)
                log.warning("%s refuse les requêtes (%s) : enrichissement arrêté pour ce passage.", ad.source, exc)
            except (requests.RequestException, ValueError) as exc:
                stats["failed"] += 1
                log.debug("Page non lue (%s) : %s", exc, ad.url)
            time.sleep(delay)
    log.info("Enrichissement : %d annonce(s) complétée(s), %d échec(s), bloqué : %s",
             stats["enriched"], stats["failed"], ", ".join(stats["blocked"]) or "non")
    return stats


def preview(url: str, timeout: float = 15) -> str:
    """Diagnostic lisible pour une URL (utilisé par `inspect-emails`)."""
    import requests

    try:
        with requests.Session() as session:
            info = fetch(url, session, timeout)
    except Blocked as exc:
        return f"refusé par le site ({exc})"
    except (requests.RequestException, ValueError) as exc:
        return f"erreur ({exc})"
    seller = ", ".join(f"{k}={v}" for k, v in info.seller.items()) or "?"
    return (f"OK — état : {info.condition or '?'} | vendeur : {info.seller_type or '?'} ({seller}) | "
            f"description : {info.description[:120] or '(vide)'}")

