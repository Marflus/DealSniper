"""eBay via l'API officielle Browse (secrets EBAY_CLIENT_ID et EBAY_CLIENT_SECRET)."""

from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any

from ..config import env
from ..http import PoliteClient
from ..models import Ad
from ..utils.text import clean_html
from ..utils.urls import native_id
from .base import Site

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/get_item_by_legacy_id"
CONDITIONS = {"parts": "7000", "used": "3000|4000|5000|6000"}  # pour pièces / occasion


def build_filter(search: dict[str, Any], country: str = "FR") -> str:
    """Filtre eBay : pays de l'objet, prix max, état."""
    parts = [f"itemLocationCountry:{country}", "priceCurrency:EUR"]
    if search.get("max_price") is not None:
        parts.append(f"price:[{search.get('min_price', 0)}..{search['max_price']}]")
    if search.get("condition") in CONDITIONS:
        parts.append(f"conditionIds:{{{CONDITIONS[search['condition']]}}}")
    return ",".join(parts)


def _euros(amount: dict[str, Any] | None) -> float | None:
    try:
        return float(amount["value"]) if amount and amount.get("currency", "EUR") == "EUR" else None
    except (KeyError, TypeError, ValueError):
        return None


def _seller(info: dict[str, Any]) -> dict[str, Any] | None:
    """Réputation du vendeur : % d'avis positifs et nombre d'avis."""
    pct, count = info.get("feedbackPercentage"), info.get("feedbackScore")
    if pct is None and count is None:
        return None
    return {
        "positive_pct": float(pct) if pct is not None else None,
        "reviews": int(count) if count is not None else None,
    }


def _place(location: dict[str, Any]) -> str | None:
    return " ".join(p for p in (location.get("city"), location.get("postalCode")) if p) or None


def _image(item: dict[str, Any]) -> str | None:
    images = [item.get("image") or {}, *(item.get("thumbnailImages") or [])]
    return next((i["imageUrl"] for i in images if i.get("imageUrl")), None)


def is_available(item: dict[str, Any]) -> bool:
    """False si l'objet est épuisé ou si la vente est terminée."""
    statuses = {a.get("estimatedAvailabilityStatus") for a in item.get("estimatedAvailabilities") or []}
    if statuses and statuses <= {"OUT_OF_STOCK"}:
        return False
    end = item.get("itemEndDate")
    if end:
        try:
            return datetime.fromisoformat(end.replace("Z", "+00:00")) > datetime.now(timezone.utc)
        except ValueError:
            pass
    return True


def item_to_ad(item: dict[str, Any]) -> Ad | None:
    """Une entrée de résultat de recherche eBay → Ad."""
    if not (item.get("itemWebUrl") and item.get("title")):
        return None
    options = item.get("buyingOptions") or []
    auction = "AUCTION" in options and "FIXED_PRICE" not in options
    price = _euros(item.get("currentBidPrice") if auction else item.get("price")) or _euros(item.get("price"))
    shipping = [_euros(o.get("shippingCost")) for o in item.get("shippingOptions") or []]
    shipping_costs = [c for c in shipping if c is not None]
    return Ad(
        title=item["title"][:200],
        url=item["itemWebUrl"].split("?")[0],
        source="ebay",
        price=price,
        shipping=min(shipping_costs) if shipping_costs else None,
        description=f"État : {item['condition']}" if item.get("condition") else "",
        location=_place(item.get("itemLocation") or {}),
        price_mode="enchere" if auction else "fixe",
        seller=_seller(item.get("seller") or {}),
        image=_image(item),
    )


class EbaySite(Site):
    name = "ebay"
    label = "eBay"

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        super().__init__(cfg, config)
        self._token: str | None = None
        self._token_expiry = 0.0

    def is_configured(self) -> bool:
        return bool(env("EBAY_CLIENT_ID") and env("EBAY_CLIENT_SECRET") and self.cfg.get("searches"))

    async def _headers(self, http: PoliteClient) -> dict[str, str]:
        """Jeton d'application (valable ~2 h, renouvelé automatiquement)."""
        if not self._token or time.time() > self._token_expiry - 60:
            credentials = base64.b64encode(f"{env('EBAY_CLIENT_ID')}:{env('EBAY_CLIENT_SECRET')}".encode()).decode()
            resp = await http.post(TOKEN_URL, headers={"Authorization": f"Basic {credentials}"},
                                   data={"grant_type": "client_credentials",
                                         "scope": "https://api.ebay.com/oauth/api_scope"})
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
            self._token_expiry = time.time() + float(resp.json().get("expires_in", 7200))
        return {"Authorization": f"Bearer {self._token}", "X-EBAY-C-MARKETPLACE-ID": self.cfg["marketplace"]}

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        """ÉTAPE 1 : une recherche par entrée de `searches`, les plus récentes d'abord."""
        headers, ads = await self._headers(http), []
        for search in self.cfg["searches"]:
            params = {"q": search["query"], "filter": build_filter(search, self.cfg["country"]),
                      "sort": "newlyListed", "limit": min(int(search.get("limit", self.cfg["limit"])), 200)}
            resp = await http.get(SEARCH_URL, headers=headers, params=params)
            resp.raise_for_status()
            ads += [ad for ad in map(item_to_ad, resp.json().get("itemSummaries") or []) if ad]
            self.report(search["query"], len(ads))
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        """ÉTAPE 2 : fiche complète (description du vendeur, état détaillé, ville, réputation)."""
        nid = native_id(ad.url)
        if not nid:
            return ad
        item_id = nid.split(":", 1)[1]
        resp = await http.get(ITEM_URL, headers=await self._headers(http), params={"legacy_item_id": item_id})
        resp.raise_for_status()
        item = resp.json()
        condition = " — ".join(p for p in (item.get("condition"), item.get("conditionDescription")) if p)
        description = clean_html(item.get("description") or item.get("shortDescription") or "")[:2000]
        ad.description = " | ".join(p for p in (f"État : {condition}" if condition else "", description) if p)
        ad.location = ad.location or _place(item.get("itemLocation") or {})
        ad.seller = _seller(item.get("seller") or {}) or ad.seller
        ad.image = _image(item) or ad.image
        ad.available = is_available(item)
        return ad
