"""MODÈLE À COPIER pour ajouter un site : `cp _template.py mon_site.py`.

Ensuite :
  1. renommez la classe et `name` (ex. "mon_site") ;
  2. écrivez `list_recent` (étape 1) et, si utile, `fetch_detail` (étape 2) ;
  3. ajoutez-le dans SITE_MODULES (sites/__init__.py) ;
  4. ajoutez dans config.yaml :

        sites:
          mon_site:
            enabled: true
            interval: [1800, 3600]     # pause entre deux passages (secondes)
            list_url: "https://exemple.fr/annonces?tri=recent"

Si le site a une API ou un flux RSS, utilisez-les (voir ebay.py) : c'est
plus fiable et plus léger que lire des pages HTML.

Pour une page HTML, `http.get(url, check_robots=True)` vérifie robots.txt.
Si le site répond 401/403 (refus, captcha), le moteur met le site en pause :
c'est un refus, ne cherchez pas à le contourner.
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import PoliteClient
from ..models import Ad
from ..utils.prices import extract_price
from ..utils.text import clean_html
from .base import Site


class TemplateSite(Site):
    name = "template"          # ← à changer : doit correspondre au bloc de config.yaml

    def is_configured(self) -> bool:
        return bool(self.cfg.get("list_url"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        """ÉTAPE 1 : une page de liste → une Ad par annonce (titre, prix, URL de détail)."""
        resp = await http.get(self.cfg["list_url"], check_robots=True)
        resp.raise_for_status()
        page = BeautifulSoup(resp.text, "html.parser")

        ads = []
        for block in page.select("article.annonce"):        # ← sélecteur d'un bloc d'annonce
            link = block.select_one("a")                     # ← lien vers la page de détail
            if not link or not link.get("href"):
                continue
            ads.append(Ad(
                title=clean_html(link.get_text(" "))[:200],
                url=urljoin(self.cfg["list_url"], link["href"]),   # l'URL sert d'identifiant unique
                source=self.name,
                price=extract_price(block.get_text(" ")),          # « 120 € » trouvé dans le bloc
            ))
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        """ÉTAPE 2 (facultative) : appelée seulement pour une annonce NOUVELLE qui a passé le filtre."""
        resp = await http.get(ad.url, check_robots=True)
        resp.raise_for_status()
        description = BeautifulSoup(resp.text, "html.parser").select_one(".description")  # ← sélecteur
        if description:
            ad.description = clean_html(description.get_text(" "))[:2000]
        return ad
