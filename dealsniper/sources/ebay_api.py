"""Source « eBay Browse API » : recherche officielle eBay, sans email ni scraping.

API publique et gratuite d'eBay (compte développeur : https://developer.ebay.com).
Chaque recherche configurée renvoie jusqu'à `limit` annonces récentes, filtrées
côté eBay : objets situés en France, prix maximum, état (pour pièces / occasion).

Authentification « application » (client credentials) : deux secrets,
EBAY_CLIENT_ID et EBAY_CLIENT_SECRET. Quota par défaut : 5 000 appels/jour,
largement suffisant (1 appel par recherche et par passage).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from ..config import env
from ..models import Ad
from . import SourceReport

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id"
SCOPE = "https://api.ebay.com/oauth/api_scope"
CONDITION_IDS = {"parts": "7000", "used": "3000|4000|5000|6000", "any": None}
CONDITION_LABELS = {"7000": "pour pièces / ne fonctionne pas"}


def get_token(session, client_id: str, client_secret: str, timeout: int) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = session.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": SCOPE},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def build_filter(search: dict[str, Any], cfg: dict[str, Any]) -> str:
    """Filtre eBay : France uniquement, prix max, état, type de vente."""
    parts = [f"itemLocationCountry:{cfg.get('country', 'FR')}", "priceCurrency:EUR"]
    if search.get("max_price") is not None:
        parts.append(f"price:[{search.get('min_price', 0)}..{search['max_price']}]")
    condition = CONDITION_IDS.get(str(search.get("condition", "any")))
    if condition:
        parts.append(f"conditionIds:{{{condition}}}")
    if search.get("buying") == "fixed":
        parts.append("buyingOptions:{FIXED_PRICE}")
    return ",".join(parts)


def _money(obj: dict[str, Any] | None) -> float | None:
    try:
        return float(obj["value"]) if obj and obj.get("currency", "EUR") == "EUR" else None
    except (KeyError, TypeError, ValueError):
        return None


def _seller(info: dict[str, Any]) -> dict[str, Any] | None:
    """Réputation eBay du vendeur : % d'avis positifs et nombre d'avis."""
    pct, score = info.get("feedbackPercentage"), info.get("feedbackScore")
    if pct is None and score is None:
        return None
    try:
        return {
            "positive_pct": float(pct) if pct is not None else None,
            "reviews": int(score) if score is not None else None,
        }
    except (TypeError, ValueError):
        return None


def item_to_ad(item: dict[str, Any], search_name: str) -> Ad | None:
    url = item.get("itemWebUrl")
    title = item.get("title")
    if not (url and title):
        return None
    auction = "AUCTION" in (item.get("buyingOptions") or []) and "FIXED_PRICE" not in (item.get("buyingOptions") or [])
    price = _money(item.get("currentBidPrice") if auction else item.get("price")) or _money(item.get("price"))
    shipping = None
    for option in item.get("shippingOptions") or []:
        cost = _money(option.get("shippingCost"))
        if cost is not None:
            shipping = cost if shipping is None else min(shipping, cost)
    location = item.get("itemLocation") or {}
    place = " ".join(p for p in (location.get("city"), location.get("postalCode")) if p) or None
    condition = item.get("condition") or CONDITION_LABELS.get(str(item.get("conditionId")), "")
    description = " | ".join(p for p in (
        f"État : {condition}" if condition else "",
        item.get("shortDescription") or "",
        f"Pays : {location.get('country')}" if location.get("country") else "",
        f"recherche eBay « {search_name} »",
    ) if p)
    seller = _seller(item.get("seller") or {})
    return Ad(
        title=title[:200], url=url.split("?")[0], source="ebay", price=price, shipping=shipping,
        description=description, location=place, price_mode="enchere" if auction else "fixe", seller=seller,
    )


def collect(cfg: dict[str, Any]) -> tuple[list[Ad], SourceReport]:
    import requests

    report = SourceReport(name="ebay-api")
    client_id, client_secret = env("EBAY_CLIENT_ID"), env("EBAY_CLIENT_SECRET")
    if not (client_id and client_secret):
        log.info("eBay API non configurée (EBAY_CLIENT_ID / EBAY_CLIENT_SECRET) : source ignorée.")
        return [], report

    timeout = int(cfg.get("timeout", 20))
    ads: list[Ad] = []
    with requests.Session() as session:
        try:
            token = get_token(session, client_id, client_secret, timeout)
        except (requests.RequestException, KeyError, ValueError) as exc:
            report.ok, report.error = False, f"authentification eBay impossible : {exc}"
            log.error(report.error)
            return [], report
        headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": cfg.get("marketplace", "EBAY_FR")}
        for search in cfg.get("searches") or []:
            name = search.get("name") or search["query"]
            params = {
                "q": search["query"],
                "filter": build_filter(search, cfg),
                "sort": "newlyListed",
                "limit": min(int(search.get("limit", cfg.get("limit", 100))), 200),
            }
            if search.get("category_id"):
                params["category_ids"] = str(search["category_id"])
            try:
                resp = session.get(SEARCH_URL, headers=headers, params=params, timeout=timeout)
                resp.raise_for_status()
                items = resp.json().get("itemSummaries") or []
            except (requests.RequestException, ValueError) as exc:
                report.ok, report.error = False, f"recherche « {name} » en échec : {exc}"
                log.warning(report.error)
                continue
            report.items += len(items)
            found = [ad for ad in (item_to_ad(i, name) for i in items) if ad]
            ads += found
            log.info("eBay « %s » : %d annonce(s)", name, len(found))
    report.ads = len(ads)
    return ads, report


# --------------------------------------------------------------------------- #
#  Fiche complète d'une annonce (description du vendeur, état détaillé, ville)
# --------------------------------------------------------------------------- #
def details_to_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Champs utiles de la réponse `get_item_by_legacy_id`."""
    from ..text import clean_html

    location = item.get("itemLocation") or {}
    return {
        "description": clean_html(item.get("description") or item.get("shortDescription") or ""),
        "condition": " — ".join(p for p in (item.get("condition"), item.get("conditionDescription")) if p),
        "location": " ".join(p for p in (location.get("city"), location.get("postalCode")) if p) or None,
        "seller": _seller(item.get("seller") or {}),
    }


def enrich_ads(ads: list[Ad], cfg: dict[str, Any]) -> int:
    """Complète les annonces eBay retenues par le filtre avec leur fiche complète (1 appel par annonce)."""
    import requests

    from ..urls import native_id

    targets = [a for a in ads if a.source == "ebay" and (native_id(a.url) or "").startswith("ebay:")]
    client_id, client_secret = env("EBAY_CLIENT_ID"), env("EBAY_CLIENT_SECRET")
    if not (targets and client_id and client_secret):
        return 0
    timeout = int(cfg.get("timeout", 20))
    done = 0
    with requests.Session() as session:
        try:
            token = get_token(session, client_id, client_secret, timeout)
        except (requests.RequestException, KeyError, ValueError) as exc:
            log.warning("Fiches eBay non lues (authentification : %s)", exc)
            return 0
        headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": cfg.get("marketplace", "EBAY_FR")}
        for ad in targets:
            legacy_id = native_id(ad.url).split(":", 1)[1]
            try:
                resp = session.get(ITEM_URL, headers=headers, params={"legacy_item_id": legacy_id}, timeout=timeout)
                resp.raise_for_status()
                info = details_to_fields(resp.json())
            except (requests.RequestException, ValueError) as exc:
                log.debug("Fiche eBay %s non lue : %s", legacy_id, exc)
                continue
            parts = [f"État : {info['condition']}" if info["condition"] else "", info["description"][:2000]]
            ad.description = " | ".join(p for p in parts + [ad.description] if p)
            ad.location = ad.location or info["location"]
            ad.seller = info["seller"] or ad.seller
            done += 1
    log.info("eBay : %d fiche(s) complète(s) lue(s)", done)
    return done
