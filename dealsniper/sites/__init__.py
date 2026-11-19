"""Sites surveillés.

Pour en ajouter un : copiez `_template.py`, ajoutez-le dans SITE_MODULES,
puis ajoutez un bloc à son nom sous `sites:` dans config.yaml.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from .base import Site

log = logging.getLogger(__name__)

# nom dans config.yaml → "module:Classe"
SITE_MODULES = {
    "ebay": "ebay:EbaySite",
    "vinted": "vinted:VintedSite",
    "leboncoin": "leboncoin:LeboncoinSite",
    "facebook": "facebook:FacebookMarketplaceSite",
}


def _load_class(name: str) -> type[Site] | None:
    """Importe la classe d'un site ; un site dont une dépendance manque est ignoré sans bloquer les autres."""
    module, cls = SITE_MODULES[name].split(":")
    try:
        return getattr(importlib.import_module(f".{module}", __name__), cls)
    except ImportError as exc:
        log.warning("Site %s ignoré : dépendance manquante (%s).", name, exc.name or exc)
        return None


def load_sites(config: dict[str, Any]) -> list[Site]:
    """Sites activés dans config.yaml et correctement configurés."""
    sites = []
    for name, cfg in config["sites"].items():
        if not (cfg or {}).get("enabled", True):
            continue
        if name not in SITE_MODULES:
            log.warning("Site « %s » absent de SITE_MODULES (dealsniper/sites/__init__.py).", name)
            continue
        cls = _load_class(name)
        if cls is None:
            continue
        site = cls(cfg, config)
        if site.is_configured():
            sites.append(site)
        else:
            log.info("Site %s ignoré : secrets ou réglages manquants.", name)
    return sites
