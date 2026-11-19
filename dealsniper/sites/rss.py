"""Flux RSS / Atom — n'importe quel site qui publie un flux de ses annonces.

Réglages : config.yaml > sites > rss > feeds (name, url). Pas d'étape 2 : le flux
contient déjà titre, lien et description.
"""

from __future__ import annotations

from typing import Any

import feedparser

from ..http import PoliteClient
from ..models import Ad
from ..utils.location import extract_location
from ..utils.prices import detect_price_mode, extract_price, extract_shipping
from ..utils.text import clean_html
from ..utils.urls import unwrap_tracking_url
from .base import Site


def entry_to_ad(entry: Any, feed_name: str) -> Ad | None:
    link, title = unwrap_tracking_url(entry.get("link", "")), clean_html(entry.get("title", ""))
    if not (link and title):
        return None
    description = clean_html(entry.get("summary") or entry.get("description") or "")
    return Ad(title=title[:200], url=link, source=feed_name, description=description,
              price=extract_price(f"{title} {description}"), shipping=extract_shipping(description),
              location=extract_location(description), price_mode=detect_price_mode(f"{title} {description}"))


class RssSite(Site):
    name = "rss"

    def is_configured(self) -> bool:
        return bool(self.cfg.get("feeds"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        ads = []
        for feed in self.cfg["feeds"]:
            resp = await http.get(feed["url"])
            resp.raise_for_status()
            entries = feedparser.parse(resp.content).entries
            ads += [ad for ad in (entry_to_ad(e, feed.get("name", "rss")) for e in entries) if ad]
        return ads
