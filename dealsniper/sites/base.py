"""Interface d'un site.

`list_recent` renvoie les annonces récentes (titre, prix, URL au minimum).
`fetch_detail`, facultatif, complète une annonce nouvelle qui a passé le filtre.
"""

from __future__ import annotations

from typing import Any

from ..http import PoliteClient
from ..models import Ad


class Site:
    name = "site"                 # clé du site dans config.yaml > sites
    reads_web_pages = False       # True pour un site lu en HTML : robots.txt sera vérifié

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        self.cfg = cfg            # le bloc config.yaml > sites > <name>
        self.config = config      # toute la configuration
        low, high = cfg.get("interval", [600, 900])
        self.interval = (float(low), float(high))

    def is_configured(self) -> bool:
        """False si un secret ou un réglage manque : le site est alors ignoré."""
        return True

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        raise NotImplementedError

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        return ad
