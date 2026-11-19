"""Connecteur API officielle eBay (Browse API) — famille « API ».

Étape 1 : une recherche par entrée de `sources.ebay_api.searches` (objets situés
en France, triés par date). Étape 2 : fiche complète (`get_item_by_legacy_id`)
pour les annonces nouvelles qui ont passé le filtre. Quota gratuit : 5 000
appels/jour — à 16 recherches toutes les 10-15 min, on en utilise ~2 000.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from ...config import env
from ...models import Ad
from ...sources import ebay_api as api
from ...urls import native_id
from ..http import PoliteClient
from .base import Connector


class EbayConnector(Connector):
    name = "ebay"
    default_interval = (600, 900)

    def __init__(self, cfg: dict[str, Any], app_config: dict[str, Any]):
        super().__init__(cfg, app_config)
        self.api_cfg = app_config["sources"]["ebay_api"]
        self._token: str | None = None
        self._token_expiry = 0.0

    def is_configured(self) -> bool:
        return bool(env("EBAY_CLIENT_ID") and env("EBAY_CLIENT_SECRET") and self.api_cfg.get("searches"))

    async def _headers(self, http: PoliteClient) -> dict[str, str]:
        if not self._token or time.time() > self._token_expiry - 60:
            credentials = base64.b64encode(f"{env('EBAY_CLIENT_ID')}:{env('EBAY_CLIENT_SECRET')}".encode()).decode()
            resp = await http.post(
                api.TOKEN_URL,
                headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials", "scope": api.SCOPE},
            )
            resp.raise_for_status()
            payload = resp.json()
            self._token = payload["access_token"]
            self._token_expiry = time.time() + float(payload.get("expires_in", 7200))
        marketplace = self.api_cfg.get("marketplace", "EBAY_FR")
        return {"Authorization": f"Bearer {self._token}", "X-EBAY-C-MARKETPLACE-ID": marketplace}

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        headers = await self._headers(http)
        ads: list[Ad] = []
        for search in self.api_cfg["searches"]:
            params = {
                "q": search["query"],
                "filter": api.build_filter(search, self.api_cfg),
                "sort": "newlyListed",
                "limit": min(int(search.get("limit", self.api_cfg.get("limit", 100))), 200),
            }
            resp = await http.get(api.SEARCH_URL, headers=headers, params=params)
            resp.raise_for_status()
            name = search.get("name") or search["query"]
            ads += [ad for ad in (api.item_to_ad(i, name) for i in resp.json().get("itemSummaries") or []) if ad]
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        nid = native_id(ad.url) or ""
        if not nid.startswith("ebay:"):
            return ad
        resp = await http.get(api.ITEM_URL, headers=await self._headers(http),
                              params={"legacy_item_id": nid.split(":", 1)[1]})
        resp.raise_for_status()
        info = api.details_to_fields(resp.json())
        parts = [f"État : {info['condition']}" if info["condition"] else "", info["description"][:2000], ad.description]
        ad.description = " | ".join(p for p in parts if p)
        ad.location = ad.location or info["location"]
        ad.seller = info["seller"] or ad.seller
        return ad
