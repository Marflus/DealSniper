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


def merge_searches(common: list[dict], own: list[dict], uses_condition: bool) -> list[dict]:
    """Recherches communes + recherches propres au site, sans doublon.

    Un site qui ne sait pas filtrer l'état (Vinted, Leboncoin…) ignore `condition` :
    « ps5 pour pièces ≤ 200 € » et « ps5 occasion ≤ 260 € » y deviennent une seule recherche « ps5 ≤ 260 € ».
    """
    merged: dict[tuple, dict] = {}
    for search in [*common, *own]:
        search = dict(search)
        query = str(search.get("query") or "").strip()
        if not query:
            continue
        if not uses_condition:
            search.pop("condition", None)
        key = (query.lower(), search.get("condition")) if uses_condition else (query.lower(),)
        previous = merged.get(key)
        if previous and not uses_condition:
            prices = [previous.get("max_price"), search.get("max_price")]
            search["max_price"] = None if None in prices else max(prices)  # le plus large des deux
            search = {**previous, **search}
            if search["max_price"] is None:
                search.pop("max_price")
        merged[key] = search
    return list(merged.values())


def load_sites(config: dict[str, Any]) -> list[Site]:
    """Sites activés dans config.yaml et correctement configurés."""
    sites = []
    common = config.get("searches") or []
    for name, cfg in config["sites"].items():
        if not (cfg or {}).get("enabled", True):
            continue
        if name not in SITE_MODULES:
            log.warning("Site « %s » absent de SITE_MODULES (dealsniper/sites/__init__.py).", name)
            continue
        cls = _load_class(name)
        if cls is None:
            continue
        own = cfg.get("searches") or []
        use_common = cfg.get("common_searches", True)
        cfg = {**cfg, "searches": merge_searches(common if use_common else [], own, cls.uses_condition)}
        site = cls(cfg, config)
        if site.is_configured():
            sites.append(site)
        else:
            log.info("Site %s ignoré : secrets ou réglages manquants.", name)
    return sites
