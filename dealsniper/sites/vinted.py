"""Vinted — Playwright (pagination multi-pages + défilement + extraction fiabilisée)."""

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
_VINTED_META_RE = re.compile(
    r",\s*(?:marque|état|etat|taille|prix|brand|size|condition)\s*:.*$",
    re.IGNORECASE,
)
_VINTED_PRICE_TAIL_RE = re.compile(r",\s*\d[\d\s\u00a0\u202f.,]*€.*$")


_VINTED_FIELD_RE = re.compile(r",\s*(marque|modèle|modele|état|etat|taille)\s*:\s*([^,]+)", re.IGNORECASE)
_FIELD_LABELS = {"marque": "Marque", "modèle": "Modèle", "modele": "Modèle", "état": "État", "etat": "État",
                 "taille": "Taille"}


def _vinted_details(raw: str) -> str:
    """État, marque, modèle et taille que Vinted met déjà dans la carte de la liste (« …, État: Très bon état,
    Taille: 43, 49.95 € ») : gardés comme description pour le filtre et l'IA, sans ouvrir l'annonce."""
    match = _VINTED_META_RE.search(raw or "")
    if not match:
        return ""
    parts = [f"{_FIELD_LABELS[k.lower()]} : {clean_html(v).strip()}"
             for k, v in _VINTED_FIELD_RE.findall(match.group(0)) if clean_html(v).strip()]
    return " | ".join(dict.fromkeys(parts))[:300]


def _clean_vinted_title(raw: str) -> str:
    """Retire les métadonnées Vinted en fin d'attribut `title` sans couper les virgules du vrai titre."""
    text = _VINTED_META_RE.sub("", raw or "")
    text = _VINTED_PRICE_TAIL_RE.sub("", text)
    return clean_html(text)[:200]


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
        limit = int(self.cfg.get("limit", 60))
        default_pages = max(1, int(self.cfg.get("max_pages", 3)))
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
                query = search["query"]
                want = int(search.get("limit", limit))
                max_pages = min(int(search.get("max_pages", default_pages)), max(1, (want + 35) // 36))
                log.info("Vinted : recherche « %s » (cible %d, max %d page(s))...", query, want, max_pages)

                count = 0
                for page_num in range(1, max_pages + 1):
                    if count >= want:
                        break
                    url = f"{self.BASE_URL}/catalog?search_text={quote_plus(query)}&order=newest_first&page={page_num}"
                    if search.get("min_price") is not None:
                        url += f"&price_from={int(search['min_price'])}"
                    if search.get("max_price") is not None:
                        url += f"&price_to={int(search['max_price'])}"

                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as exc:
                        log.warning("Vinted : page %d de « %s » interrompue (%s).", page_num, query, exc)
                        break

                    await page.wait_for_timeout(2500)
                    # Défilement doux pour déclencher le chargement paresseux des cartes et vignettes
                    for _ in range(2):
                        await page.evaluate("window.scrollBy(0, 1400)")
                        await page.wait_for_timeout(700)

                    links = await page.locator('a[href*="/items/"]').all()
                    added_page = 0
                    for link in links:
                        if count >= want:
                            break
                        href = (await link.get_attribute("href")) or ""
                        if href.startswith("/"):
                            href = self.BASE_URL + href
                        item_url = href.split("?")[0]
                        if not _ITEM_URL_RE.match(item_url) or item_url in seen_urls:
                            continue
                        seen_urls.add(item_url)

                        raw_title = (await link.get_attribute("title")) or ""
                        if not raw_title:
                            raw_title = (await link.inner_text()) or ""
                        title = _clean_vinted_title(raw_title)
                        price = extract_price(raw_title)
                        if price is None:
                            try:
                                card_text = await link.locator("xpath=..").inner_text(timeout=1000)
                                price = extract_price(card_text)
                                if not title:
                                    title = _clean_vinted_title(card_text.splitlines()[0] if card_text else "")
                            except Exception:
                                pass

                        if not title:
                            continue

                        ads.append(
                            Ad(
                                title=title,
                                url=item_url,
                                source=self.name,
                                price=price,
                                description=_vinted_details(raw_title),
                                location="France",
                                image=await link_image(link),
                            )
                        )
                        count += 1
                        added_page += 1

                    if added_page == 0:
                        break
                    if page_num < max_pages and count < want:
                        await asyncio.sleep(random.uniform(1.8, 3.2))

                log.info("Vinted : %d annonce(s) trouvée(s) pour « %s »", count, query)
                self.report(query, count)
                await asyncio.sleep(random.uniform(2.0, 4.0))

            await context.close()
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        return ad