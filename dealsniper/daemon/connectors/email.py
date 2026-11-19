"""Connecteur emails d'alerte (IMAP) : réutilise la source existante dans un thread."""

from __future__ import annotations

import asyncio

from ...config import env
from ...models import Ad
from ...sources import email_alerts
from ..http import PoliteClient
from .base import Connector


class EmailConnector(Connector):
    name = "email"
    default_interval = (900, 1200)

    def is_configured(self) -> bool:
        cfg = self.app_config["sources"]["email"]
        return bool(cfg.get("enabled", True) and env("IMAP_SERVER") and env("IMAP_EMAIL") and env("IMAP_PASSWORD"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        ads, report = await asyncio.to_thread(email_alerts.collect, self.app_config["sources"]["email"])
        if not report.ok:
            raise RuntimeError(report.error)
        return ads
