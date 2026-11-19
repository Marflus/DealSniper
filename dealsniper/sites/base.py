"""Interface commune à tous les sites.

Un site = un fichier dans `dealsniper/sites/` avec une classe qui hérite de `Site`
et implémente :
  * `list_recent(http)`  — ÉTAPE 1 (obligatoire) : liste légère des annonces
    récentes (titre, prix, URL au minimum ; l'URL sert d'identifiant unique) ;
  * `fetch_detail(http, ad)` — ÉTAPE 2 (facultative) : compléter description,
    état, vendeur. Appelée UNIQUEMENT pour une annonce jamais vue qui a passé
    le filtre.

Tout le reste (déjà vu ?, filtres, analyse Claude, notifications, pauses) est
fait par `engine.py`, de la même façon pour tous les sites.
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
