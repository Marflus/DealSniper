"""Facebook Marketplace — Playwright avec profil persistant, défilement et parsing robuste.

Premier lancement : mettre `headless: false` dans config.yaml pour se connecter
à Facebook dans la fenêtre Chromium. La session reste sauvegardée dans `data/fb_profile`,
ensuite repasser `headless: true`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from ..config import data_dir
from ..http import PoliteClient
from ..models import Ad
from ..utils.prices import parse_amount
from ..utils.text import clean_html
from .base import Site, link_image

log = logging.getLogger(__name__)

_ITEM_ID_RE = re.compile(r"/marketplace/item/(\d+)")
_PRICE_LINE_RE = re.compile(r"^(?:gratuit|free|[\d\s\u00a0\u202f.,]+\s*(?:€|eur)|(?:€|eur)\s*[\d\s\u00a0\u202f.,]+)$", re.I)
_AMOUNT_RE = re.compile(r"[\d\s\u00a0\u202f.,]+")


def _parse_card(lines: list[str], default_city: str) -> tuple[str, float | None, str]:
    """Extrait (titre, prix, ville) d'une carte Marketplace en ignorant l'ancien prix barré."""
    price: float | None = None
    non_price: list[str] = []
    for line in lines:
        low = line.lower().strip()
        if _PRICE_LINE_RE.match(line):
            if price is None:
                if low in ("gratuit", "free"):
                    price = 0.0
                else:
                    m = _AMOUNT_RE.search(line)
                    price = parse_amount(m.group(0)) if m else None
            continue  # ignore un second prix (ancien prix barré lors d'une baisse de prix)
        non_price.append(line)

    if not non_price:
        return "", price, default_city
    title = clean_html(non_price[0])[:200]
    location = clean_html(non_price[1])[:120] if len(non_price) > 1 else default_city
    return title, price, location


class FacebookMarketplaceSite(Site):
    name = "facebook"

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        super().__init__(cfg, config)
        self.city = str(cfg.get("city", "paris"))
        self.headless = bool(cfg.get("headless", True))
        self.profile_dir = Path(cfg.get("profile_dir") or (data_dir() / "fb_profile"))

    def is_configured(self) -> bool:
        return bool(self.cfg.get("searches"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        limit = int(self.cfg.get("limit", 50))
        max_scrolls = max(1, int(self.cfg.get("max_scrolls", 5)))
        ads: list[Ad] = []
        seen_ids: set[str] = set()

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                locale="fr-FR",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()

            for search in self.cfg["searches"]:
                query = search["query"]
                want = int(search.get("limit", limit))
                url = (
                    f"https://www.facebook.com/marketplace/{self.city}/search/"
                    f"?query={quote_plus(query)}&sortBy=creation_time_descend&exact=false"
                )
                if search.get("min_price") is not None:
                    url += f"&minPrice={int(search['min_price'])}"
                if search.get("max_price") is not None:
                    url += f"&maxPrice={int(search['max_price'])}"
                if search.get("days") is not None:
                    url += f"&daysSinceListed={int(search['days'])}"

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as exc:
                    log.warning("Facebook : recherche « %s » interrompue (%s).", query, exc)
                    continue

                await page.wait_for_timeout(3000)

                # Défilement progressif pour charger davantage d'annonces
                locator = page.locator('a[href*="/marketplace/item/"]')
                prev_total = 0
                stagnant = 0
                for _ in range(max_scrolls):
                    total = await locator.count()
                    if total >= want + 10:
                        break
                    await page.evaluate("window.scrollBy(0, 1600)")
                    await page.wait_for_timeout(1600)
                    new_total = await locator.count()
                    if new_total <= prev_total:
                        stagnant += 1
                        if stagnant >= 2:
                            break
                    else:
                        stagnant = 0
                    prev_total = new_total

                links = await locator.all()
                count = 0
                for link in links:
                    if count >= want:
                        break
                    href = (await link.get_attribute("href")) or ""
                    m = _ITEM_ID_RE.search(href)
                    if not m or m.group(1) in seen_ids:
                        continue
                    item_id = m.group(1)
                    seen_ids.add(item_id)

                    lines = [line.strip() for line in (await link.inner_text()).splitlines() if line.strip()]
                    title, price, location = _parse_card(lines, self.city)
                    if not title:
                        continue

                    ads.append(
                        Ad(
                            title=title,
                            url=f"https://www.facebook.com/marketplace/item/{item_id}/",
                            source=self.name,
                            price=price,
                            location=location,
                            image=await link_image(link),
                        )
                    )
                    count += 1

                log.info("Facebook : %d annonce(s) trouvée(s) pour « %s »", count, query)
                self.report(query, count)
                await asyncio.sleep(random.uniform(2.0, 3.5))

            await context.close()
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                locale="fr-FR",
            )
            page = await context.new_page()
            await page.goto(ad.url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2500)

            main = page.locator('div[role="main"]').first
            if await main.count():
                ad.description = clean_html(await main.inner_text())[:2000]

            await context.close()
        return ad