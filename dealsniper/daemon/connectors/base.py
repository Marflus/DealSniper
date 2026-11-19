"""Interface commune des connecteurs.

Un connecteur = une source. Il implémente au minimum `list_recent` (étape 1 :
liste légère des éléments récents, avec un identifiant unique) et, s'il le peut,
`fetch_detail` (étape 2 : fiche complète, appelée UNIQUEMENT pour les éléments
jamais vus). Tout le reste (dédoublonnage, filtres, analyse, notifications,
pauses) est géré par l'orchestrateur, identique pour toutes les sources.

Deux familles :
  * connecteurs « API » (eBay…) : données structurées, quota officiel ;
  * connecteurs « web » (`reads_web_pages = True`) : robots.txt vérifié avant
    chaque page, délai par site. À n'utiliser que pour des sites qui l'autorisent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...models import Ad
from ..http import PoliteClient


class Connector(ABC):
    #: nom unique (clé dans config.yaml > daemon.connectors)
    name: str = "base"
    #: True si le connecteur lit des pages HTML : robots.txt sera vérifié
    reads_web_pages: bool = False
    #: intervalle par défaut entre deux passages (secondes, tiré au hasard dans la plage)
    default_interval: tuple[float, float] = (600, 900)

    def __init__(self, cfg: dict[str, Any], app_config: dict[str, Any]):
        self.cfg = cfg
        self.app_config = app_config
        low, high = cfg.get("interval") or self.default_interval
        self.interval = (float(low), float(high))

    def is_configured(self) -> bool:
        """False si des secrets ou réglages manquent : le connecteur n'est pas lancé."""
        return True

    @abstractmethod
    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        """Étape 1 : éléments récents (titre, prix, URL au minimum). Doit être léger."""

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        """Étape 2 (optionnelle) : compléter description / état / vendeur d'un élément NOUVEAU."""
        return ad
