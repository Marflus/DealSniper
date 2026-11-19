"""Connecteur flux RSS / Atom — famille « API » (un flux est fait pour être lu par des programmes)."""

from __future__ import annotations

import feedparser

from ...models import Ad
from ...sources.rss import _entry_to_ad
from ..http import PoliteClient
from .base import Connector


class RssConnector(Connector):
    name = "rss"
    default_interval = (900, 1800)

    def is_configured(self) -> bool:
        return bool(self.app_config["sources"]["rss"].get("feeds"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        ads: list[Ad] = []
        for feed in self.app_config["sources"]["rss"]["feeds"]:
            resp = await http.get(feed["url"])
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            ads += [ad for ad in (_entry_to_ad(e, feed.get("name", "rss")) for e in parsed.entries) if ad]
        return ads
