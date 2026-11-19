"""MODÈLE de connecteur « web » à adapter — famille « web ».

À utiliser UNIQUEMENT pour un site public qui autorise la lecture automatique :
  * son robots.txt l'autorise (vérifié automatiquement avant chaque page) ;
  * ses conditions d'utilisation ne l'interdisent pas ;
  * il ne vous oppose pas de captcha / 403 (sinon le connecteur se met en pause :
    c'est un refus, on ne le contourne pas).
Si le site propose un flux RSS ou une API, préférez `rss.py` ou un connecteur API.

Tout est piloté par config.yaml (sélecteurs CSS), par exemple :

    daemon:
      connectors:
        example_site:
          enabled: true
          interval: [1800, 3600]
          list_url: "https://exemple.fr/annonces?tri=recent"
          item_selector: "article.annonce"        # un bloc par annonce
          link_selector: "a.titre"                # lien vers la page de détail (sert d'ID)
          title_selector: "a.titre"
          price_selector: ".prix"
          location_selector: ".ville"             # optionnel
          detail_description_selector: ".description"  # étape 2, optionnel
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...models import Ad
from ...prices import extract_price
from ...text import clean_html
from ..http import PoliteClient
from .base import Connector


class ExampleSiteConnector(Connector):
    name = "example_site"
    reads_web_pages = True            # → robots.txt vérifié par le client HTTP
    default_interval = (1800, 3600)   # pages web : on espace beaucoup plus qu'une API

    def is_configured(self) -> bool:
        return bool(self.cfg.get("list_url") and self.cfg.get("item_selector") and self.cfg.get("link_selector"))

    def _text(self, node, selector: str | None) -> str:
        found = node.select_one(selector) if selector else None
        return clean_html(found.get_text(" ")) if found else ""

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        # Étape 1 : UNE page de liste. On n'en extrait que l'essentiel ; l'ID est l'URL de détail.
        resp = await http.get(self.cfg["list_url"], check_robots=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        ads: list[Ad] = []
        for block in soup.select(self.cfg["item_selector"]):
            link = block.select_one(self.cfg["link_selector"])
            if not link or not link.get("href"):
                continue
            title = self._text(block, self.cfg.get("title_selector")) or clean_html(link.get_text(" "))
            ads.append(Ad(
                title=title[:200],
                url=urljoin(self.cfg["list_url"], link["href"]),
                source=self.name,
                price=extract_price(self._text(block, self.cfg.get("price_selector"))),
                location=self._text(block, self.cfg.get("location_selector")) or None,
            ))
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        # Étape 2 : seulement pour une annonce jamais vue (l'orchestrateur s'en charge),
        # après un petit délai aléatoire et en respectant robots.txt.
        selector = self.cfg.get("detail_description_selector")
        if not selector:
            return ad
        resp = await http.get(ad.url, check_robots=True)
        resp.raise_for_status()
        ad.description = self._text(BeautifulSoup(resp.text, "html.parser"), selector)[:2000]
        return ad
