"""Facebook Marketplace — Playwright avec profil navigateur persistant.

Premier lancement : mettre `headless: false` dans config.yaml pour se connecter
à Facebook dans la fenêtre Chromium. La session reste sauvegardée dans `data/fb_profile`,
ensuite repasser `headless: true`.
"""

from __future__ import annotations

import asyncio
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
from .base import Site

_ITEM_ID_RE = re.compile(r"/marketplace/item/(\d+)")


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
        limit = int(self.cfg.get("limit", 20))
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
                url = (
                    f"https://www.facebook.com/marketplace/{self.city}/search/"
                    f"?query={quote_plus(search['query'])}&sortBy=creation_time_descend&exact=false"
                )
                if search.get("max_price") is not None:
                    url += f"&maxPrice={int(search['max_price'])}"

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3500)

                links = await page.locator('a[href*="/marketplace/item/"]').all()
                count = 0
                for link in links:
                    if count >= int(search.get("limit", limit)):
                        break
                    href = (await link.get_attribute("href")) or ""
                    m = _ITEM_ID_RE.search(href)
                    if not m or m.group(1) in seen_ids:
                        continue
                    item_id = m.group(1)
                    seen_ids.add(item_id)

                    lines = [l.strip() for l in (await link.inner_text()).splitlines() if l.strip()]
                    if len(lines) < 2:
                        continue

                    price_m = re.search(r"[\d\s\u202f.,]+", lines[0])
                    ads.append(
                        Ad(
                            title=clean_html(lines[1])[:200],
                            url=f"https://www.facebook.com/marketplace/item/{item_id}/",
                            source=self.name,
                            price=parse_amount(price_m.group(0)) if price_m else None,
                            location=lines[2] if len(lines) > 2 else self.city,
                        )
                    )
                    count += 1

                await asyncio.sleep(2.5)

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