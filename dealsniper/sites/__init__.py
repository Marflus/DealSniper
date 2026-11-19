"""Les sites surveillés. Pour en ajouter un :

  1. copiez `_template.py` en `mon_site.py` et adaptez-le ;
  2. ajoutez sa classe dans SITES ci-dessous ;
  3. ajoutez un bloc `mon_site:` sous `sites:` dans config.yaml.
"""

from __future__ import annotations

from typing import Any

from .base import Site
from .ebay import EbaySite
from .rss import RssSite

SITES: list[type[Site]] = [EbaySite, RssSite]


def load_sites(config: dict[str, Any]) -> list[Site]:
    """Sites activés dans config.yaml et correctement configurés (secrets présents…)."""
    import logging

    log = logging.getLogger(__name__)
    by_name = {cls.name: cls for cls in SITES}
    sites = []
    for name, cfg in config["sites"].items():
        if not (cfg or {}).get("enabled", True):
            continue
        if name not in by_name:
            log.warning("Site « %s » dans config.yaml mais absent de SITES (dealsniper/sites/__init__.py)", name)
            continue
        site = by_name[name](cfg, config)
        if site.is_configured():
            sites.append(site)
        else:
            log.info("Site %s ignoré : secrets ou réglages manquants.", name)
    return sites
