"""Interface d'un site.

`list_recent` renvoie les annonces récentes (titre, prix, URL au minimum).
`fetch_detail`, facultatif, complète une annonce nouvelle qui a passé le filtre.
"""

from __future__ import annotations

from typing import Any

from ..http import PoliteClient
from ..models import Ad


async def link_image(link) -> str | None:
    """Vignette d'une annonce dans une page de résultats (lien Playwright) : <img> du lien, sinon de son bloc."""
    for scope in (link, link.locator("xpath=..")):
        img = scope.locator("img").first
        if await img.count():
            src = await img.get_attribute("src") or await img.get_attribute("data-src")
            if src and src.startswith("http"):
                return src
    return None


def merge_description(listed: str, body: str, limit: int = 2000) -> str:
    """Description de la fiche (texte du vendeur : pannes, défauts…) ajoutée après les infos de la liste
    (« État : Bon état | Marque : Lenovo »)."""
    body = (body or "").strip()
    if not body:
        return listed
    return " | ".join(p for p in (listed.strip(), body) if p)[:limit]


class Site:
    name = "site"                 # clé du site dans config.yaml > sites
    label = ""                    # nom affiché (par défaut : name avec une majuscule)
    uses_condition = False        # True si le site sait filtrer l'état (« pour pièces », « occasion »)
    details_default = True        # `fetch_details` absent de config.yaml : lire les fiches ?
    max_details_default: int | None = None  # `max_details` absent : fiches lues au plus par passage

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        self.cfg = cfg            # le bloc config.yaml > sites > <name>
        self.config = config      # toute la configuration
        low, high = cfg.get("interval", [600, 900])
        self.interval = (float(low), float(high))
        self.label = self.label or self.name.capitalize()
        self.on_progress = None   # branché par le moteur

    def report(self, query: str, found: int) -> None:
        """À appeler dans list_recent après chaque recherche, avec le nombre d'annonces de CETTE recherche."""
        if self.on_progress:
            self.on_progress(query, found)

    def is_configured(self) -> bool:
        """False si un secret ou un réglage manque : le site est alors ignoré."""
        return True

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        raise NotImplementedError

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        return ad
