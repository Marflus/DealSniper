"""leboncoin — Patchright avec profil persistant et pagination multi-pages."""

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


def ad_image(item: dict[str, Any]) -> str | None:
    """Photo principale d'une annonce du JSON de recherche, quel que soit le format de `images`."""
    images = item.get("images") or {}
    if isinstance(images, list):
        images = {"urls": [i.get("url") if isinstance(i, dict) else i for i in images]}
    for key in ("urls_large", "urls", "urls_thumb"):
        urls = [u for u in images.get(key) or [] if isinstance(u, str) and u.startswith("http")]
        if urls:
            return urls[0]
    for key in ("large_url", "small_url", "thumb_url", "url"):
        if str(images.get(key) or "").startswith("http"):
            return images[key]
    return _first_image_url(item)


def _first_image_url(value: Any) -> str | None:
    """Dernier recours : première URL d'image trouvée n'importe où dans l'annonce."""
    if isinstance(value, str):
        return value if value.startswith("http") and ("img.leboncoin" in value or "/images/" in value) else None
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    for child in children:
        found = _first_image_url(child)
        if found:
            return found
    return None


def _item_price(item: dict[str, Any]) -> float | None:
    raw = item.get("price")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def location_label(loc: dict[str, Any]) -> str:
    """« Nemours 77140 » : le code postal n'est ajouté que s'il manque (city_label le contient déjà souvent)."""
    city = str(loc.get("city_label") or loc.get("city") or "").strip()
    zipcode = str(loc.get("zipcode") or "").strip()
    return f"{city} {zipcode}".strip() if zipcode and zipcode not in city else city


def _item_description(item: dict[str, Any]) -> str:
    """Combine état déclaré (attributs LBC) et corps de l'annonce pour le filtre et l'IA."""
    attrs = {}
    for attr in item.get("attributes") or []:
        if isinstance(attr, dict) and attr.get("key") and attr.get("value_label"):
            attrs[attr["key"]] = str(attr["value_label"])
    prefix_parts = []
    for key, label in (("condition", "État"), ("item_condition", "État"), ("brand", "Marque")):
        if attrs.get(key) and not any(p.startswith(f"{label} :") for p in prefix_parts):
            prefix_parts.append(f"{label} : {attrs[key]}")
    body = clean_html(item.get("body") or "")
    combined = " | ".join(p for p in (*prefix_parts, body) if p)
    return combined[:2000]


class LeboncoinSite(Site):
    name = "leboncoin"

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        super().__init__(cfg, config)
        self.headless = bool(cfg.get("headless", True))
        self.profile_dir = Path(cfg.get("profile_dir") or (data_dir() / "lbc_profile"))

    def is_configured(self) -> bool:
        return bool(self.cfg.get("searches"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        limit = int(self.cfg.get("limit", 70))
        default_pages = max(1, int(self.cfg.get("max_pages", 3)))
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
                query = search["query"]
                want = int(search.get("limit", limit))
                max_pages = min(int(search.get("max_pages", default_pages)), max(1, (want + 34) // 35))
                log.info("leboncoin : recherche « %s » (cible %d, max %d page(s))...", query, want, max_pages)

                base_url = f"https://www.leboncoin.fr/recherche?text={quote_plus(query)}&sort=time&order=desc"
                if search.get("category") is not None:
                    base_url += f"&category={search['category']}"
                if search.get("max_price") is not None:
                    base_url += f"&price={int(search.get('min_price', 1))}-{int(search['max_price'])}"

                count = 0
                for page_num in range(1, max_pages + 1):
                    if count >= want:
                        break
                    url = f"{base_url}&page={page_num}" if page_num > 1 else base_url

                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as exc:
                        log.warning("leboncoin : page %d de « %s » interrompue (%s).", page_num, query, exc)
                        break

                    # En mode visible (headless: false), laisse 45s pour résoudre un éventuel captcha DataDome
                    wait_ms = 8000 if self.headless else 45000
                    try:
                        raw_json = await page.locator("script#__NEXT_DATA__").text_content(timeout=wait_ms)
                    except PlaywrightTimeout:
                        if page_num == 1 and not ads:
                            await context.close()
                            raise AccessDenied(
                                "leboncoin : bloqué par DataDome (mets headless: false dans config.yaml pour valider le captcha)"
                            )
                        log.warning("leboncoin : blocage/timeout sur page %d de « %s », on garde le reste.", page_num, query)
                        break

                    data = json.loads(raw_json or "{}")
                    search_data = (
                        data.get("props", {}).get("pageProps", {}).get("searchData")
                        or data.get("props", {}).get("pageProps", {}).get("initialProps", {}).get("searchData")
                        or {}
                    )
                    page_items = search_data.get("ads") or []
                    if not page_items:
                        break

                    added_page = 0
                    for item in page_items:
                        if count >= want:
                            break
                        if item.get("status") not in (None, "active"):
                            continue
                        item_url = str(item.get("url", "")).split("?")[0]
                        if not item_url or item_url in seen_urls:
                            continue
                        seen_urls.add(item_url)

                        loc = item.get("location") or {}
                        place = location_label(loc)
                        owner = item.get("owner") or {}
                        seller = {
                            "name": owner.get("name"),
                            "type": "pro" if owner.get("type") == "pro" else "particulier" if owner else None,
                        }

                        ads.append(
                            Ad(
                                title=clean_html(item.get("subject"))[:200],
                                url=item_url,
                                source=self.name,
                                price=_item_price(item),
                                description=_item_description(item),
                                location=place or "France",
                                image=ad_image(item),
                                seller=seller if any(seller.values()) else None,
                            )
                        )
                        count += 1
                        added_page += 1

                    if added_page == 0 or len(page_items) < 20:
                        break
                    if page_num < max_pages and count < want:
                        await asyncio.sleep(random.uniform(3.5, 6.5))

                log.info("leboncoin : %d annonce(s) trouvée(s) pour « %s »", count, query)
                self.report(query, count)
                await asyncio.sleep(random.uniform(4.5, 8.0))

            await context.close()
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        return ad