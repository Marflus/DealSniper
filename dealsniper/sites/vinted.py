"""Vinted — Playwright (lit directement les cartes d'annonces dans la page)."""

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
from ..utils.prices import extract_price
from ..utils.text import clean_html
from .base import Site, link_image

log = logging.getLogger(__name__)

_ITEM_URL_RE = re.compile(r"https://www\.vinted\.fr/items/\d+[^?\"'\s]*")


class VintedSite(Site):
    name = "vinted"
    BASE_URL = "https://www.vinted.fr"

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        super().__init__(cfg, config)
        self.headless = bool(cfg.get("headless", True))
        self.profile_dir = Path(cfg.get("profile_dir") or (data_dir() / "vinted_profile"))

    def is_configured(self) -> bool:
        return bool(self.cfg.get("searches"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        limit = int(self.cfg.get("limit", 25))
        ads: list[Ad] = []
        seen_urls: set[str] = set()

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                locale="fr-FR",
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = await context.new_page()

            for search in self.cfg["searches"]:
                log.info("Vinted : recherche « %s »...", search["query"])
                url = f"{self.BASE_URL}/catalog?search_text={quote_plus(search['query'])}&order=newest_first"
                if search.get("max_price") is not None:
                    url += f"&price_to={int(search['max_price'])}"

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3500)

                links = await page.locator('a[href*="/items/"]').all()
                count = 0
                for link in links:
                    if count >= int(search.get("limit", limit)):
                        break
                    href = (await link.get_attribute("href")) or ""
                    if href.startswith("/"):
                        href = self.BASE_URL + href
                    item_url = href.split("?")[0]
                    if not _ITEM_URL_RE.match(item_url) or item_url in seen_urls:
                        continue
                    seen_urls.add(item_url)

                    raw_title = (await link.get_attribute("title")) or (await link.inner_text()) or ""
                    title = clean_html(raw_title.split(",")[0])[:200]
                    price = extract_price(raw_title)

                    if not title:
                        continue

                    ads.append(
                        Ad(
                            title=title,
                            url=item_url,
                            source=self.name,
                            price=price,
                            location="France",
                            image=await link_image(link),
                        )
                    )
                    count += 1

                log.info("Vinted : %d annonce(s) trouvée(s) pour « %s »", count, search["query"])
                self.report(search["query"], count)
                await asyncio.sleep(random.uniform(2.0, 4.0))

            await context.close()
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        return ad