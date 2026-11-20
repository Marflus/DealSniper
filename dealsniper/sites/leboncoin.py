"""leboncoin — Patchright (Playwright furtif) avec profil persistant."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from patchright.async_api import TimeoutError as PlaywrightTimeout, async_playwright

from ..config import data_dir
from ..http import AccessDenied, PoliteClient
from ..models import Ad
from ..utils.text import clean_html
from .base import Site

log = logging.getLogger(__name__)


class LeboncoinSite(Site):
    name = "leboncoin"

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        super().__init__(cfg, config)
        self.headless = bool(cfg.get("headless", True))
        self.profile_dir = Path(cfg.get("profile_dir") or (data_dir() / "lbc_profile"))

    def is_configured(self) -> bool:
        return bool(self.cfg.get("searches"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        limit = int(self.cfg.get("limit", 25))
        ads: list[Ad] = []
        seen_urls: set[str] = set()

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=self.headless,
                locale="fr-FR",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()

            for search in self.cfg["searches"]:
                log.info("leboncoin : recherche « %s »...", search["query"])
                url = f"https://www.leboncoin.fr/recherche?text={quote_plus(search['query'])}&sort=time&order=desc"
                if search.get("category") is not None:
                    url += f"&category={search['category']}"
                if search.get("max_price") is not None:
                    url += f"&price={int(search.get('min_price', 1))}-{int(search['max_price'])}"

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as exc:
                    log.warning("leboncoin : recherche « %s » interrompue (%s), ignorée.", search["query"], exc)
                    await asyncio.sleep(random.uniform(5.0, 8.0))
                    continue

                # En mode visible (headless: false), laisse 45s pour résoudre un éventuel captcha DataDome
                wait_ms = 8000 if self.headless else 45000
                try:
                    raw_json = await page.locator("script#__NEXT_DATA__").text_content(timeout=wait_ms)
                except PlaywrightTimeout:
                    await context.close()
                    raise AccessDenied(
                        "leboncoin : bloqué par DataDome (mets headless: false dans config.yaml pour valider le captcha)"
                    )

                data = json.loads(raw_json or "{}")
                search_data = (
                    data.get("props", {}).get("pageProps", {}).get("searchData")
                    or data.get("props", {}).get("pageProps", {}).get("initialProps", {}).get("searchData")
                    or {}
                )

                count = 0
                for item in (search_data.get("ads") or [])[: int(search.get("limit", limit))]:
                    item_url = str(item.get("url", "")).split("?")[0]
                    if not item_url or item_url in seen_urls:
                        continue
                    seen_urls.add(item_url)

                    prices = item.get("price") or []
                    price = float(prices[0]) if prices else None
                    loc = item.get("location") or {}
                    place = " ".join(p for p in (loc.get("city_label") or loc.get("city"), loc.get("zipcode")) if p)
                    images = item.get("images") or {}
                    image = (images.get("urls") or [None])[0] or images.get("small_url") or images.get("thumb_url")

                    ads.append(
                        Ad(
                            title=clean_html(item.get("subject"))[:200],
                            url=item_url,
                            source=self.name,
                            price=price,
                            description=clean_html(item.get("body") or "")[:2000],
                            location=place or "France",
                            image=image,
                        )
                    )
                    count += 1

                log.info("leboncoin : %d annonce(s) trouvée(s) pour « %s »", count, search["query"])
                self.report(search["query"], count)
                await asyncio.sleep(random.uniform(5.0, 9.0))

            await context.close()
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        return ad