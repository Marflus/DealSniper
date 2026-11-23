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
from .base import Site, merge_description

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


def item_shipping(item: dict[str, Any]) -> float | None:
    """Frais d'envoi les plus bas proposés (€), 0 si l'envoi est possible sans tarif affiché, None si remise
    en main propre seulement. Sans cela, toutes les annonces leboncoin passaient pour « main propre »."""
    attrs = {a.get("key"): a.get("value") for a in item.get("attributes") or [] if isinstance(a, dict)}
    fees = [f.get("price") for f in item.get("shipping_fees") or [] if isinstance(f, dict)]
    prices = [float(p) / 100 for p in fees if isinstance(p, int | float) and p >= 0]
    if prices:
        return round(min(prices), 2)
    return 0.0 if str(attrs.get("shippable")).lower() == "true" else None


def _item_description(item: dict[str, Any]) -> str:
    """Combine état déclaré (attributs LBC) et corps de l'annonce pour le filtre et l'IA."""
    attrs = {}
    for attr in item.get("attributes") or []:
        if isinstance(attr, dict) and attr.get("key") and attr.get("value_label"):
            key = str(attr["key"])
            # « home_appliance_brand », « computer_brand »… : la marque, quel que soit le rayon
            attrs["brand" if key.endswith("_brand") and "brand" not in attrs else key] = str(attr["value_label"])
    prefix_parts = []
    for key, label in (("condition", "État"), ("item_condition", "État"), ("brand", "Marque")):
        if attrs.get(key) and not any(p.startswith(f"{label} :") for p in prefix_parts):
            prefix_parts.append(f"{label} : {attrs[key]}")
    body = clean_html(item.get("body") or "")
    combined = " | ".join(p for p in (*prefix_parts, body) if p)
    return combined[:2000]


class LeboncoinSite(Site):
    name = "leboncoin"
    details_default = False       # une page de plus par fiche chez un site qui bloque vite : à activer soi-même
    max_details_default = 5

    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        super().__init__(cfg, config)
        self.headless = bool(cfg.get("headless", True))
        self.profile_dir = Path(cfg.get("profile_dir") or (data_dir() / "lbc_profile"))

    def is_configured(self) -> bool:
        return bool(self.cfg.get("searches"))

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        limit = int(self.cfg.get("limit", 35))
        default_pages = max(1, int(self.cfg.get("max_pages", 1)))
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
            page = context.pages[0] if context.pages else await context.new_page()

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
                        # Page de protection au lieu des résultats : on arrête TOUT de suite, même après des
                        # recherches réussies (continuer les suivantes aggrave le blocage). Les annonces déjà
                        # lues reviendront au prochain passage ; le site est mis en pause.
                        await context.close()
                        hint = ("valide la vérification dans la fenêtre Chrome au prochain passage" if not self.headless
                                else "mets headless: false dans config.yaml pour valider toi-même la vérification")
                        raise AccessDenied(f"leboncoin : bloqué par DataDome sur « {query} » ({hint})") from None

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
                                shipping=item_shipping(item),
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
                if search is not self.cfg["searches"][-1]:  # pas d'attente inutile après la dernière recherche
                    search_delay = self.cfg.get("search_delay") or [8.0, 15.0]
                    await asyncio.sleep(random.uniform(*search_delay))

            await context.close()
        return ads

    async def fetch_detail(self, http: PoliteClient, ad: Ad) -> Ad:
        """Texte du vendeur (la liste de résultats ne le contient plus : `body` y est vide), lu sur la page
        de l'annonce. Désactivé par défaut (`fetch_details`) : chaque fiche est une page de plus chez un site
        qui bloque vite ; garder `max_details` bas. Page de protection au lieu de l'annonce : pause du site."""
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=self.headless,
                locale="fr-FR",
                viewport={"width": 1280, "height": 900},
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(ad.url, wait_until="domcontentloaded", timeout=30000)
                try:
                    raw = await page.locator("script#__NEXT_DATA__").text_content(timeout=8000)
                except PlaywrightTimeout:
                    raise AccessDenied("leboncoin : fiche bloquée par la protection anti-robots") from None
            finally:
                await context.close()
        body = ((json.loads(raw or "{}").get("props") or {}).get("pageProps") or {}).get("ad") or {}
        ad.description = merge_description(ad.description, clean_html(body.get("body") or ""))
        return ad
# --- DEALSNIPER_LBC_BACKENDS ---
import inspect as _lbc_inspect
import re as _lbc_re
from urllib.parse import urlsplit as _lbc_urlsplit

from ..config import env as _lbc_env

_SCRAPEBADGER_LBC_URL = "https://scrapebadger.com/v1/leboncoin/search"


def _lbc_query_str(search: dict[str, Any]) -> str:
    raw = search.get("query")
    if isinstance(raw, (list, tuple)):
        return " OR ".join(str(q).strip() for q in raw if str(q).strip())
    return _lbc_re.sub(r"\s+", " ", str(raw or "")).strip()


def _lbc_owner_dict(owner: Any) -> dict[str, Any] | None:
    if not owner:
        return None
    if not isinstance(owner, dict):
        owner = {"name": getattr(owner, "name", None), "type": getattr(owner, "type", None)}
    raw_type = str(owner.get("type") or "").lower()
    kind = "pro" if raw_type == "pro" else "particulier" if raw_type in ("private", "particulier") else None
    out = {"name": owner.get("name"), "type": kind}
    return out if any(out.values()) else None


def _lbc_safe_place(loc: Any) -> str:
    if not loc:
        return "France"
    if not isinstance(loc, dict):
        loc = {
            "city_label": getattr(loc, "city_label", None),
            "city": getattr(loc, "city", None),
            "zipcode": getattr(loc, "zipcode", None),
        }
    if "location_label" in globals():
        try:
            res = location_label(loc)
            if res:
                return res
        except Exception:
            pass
    city = str(loc.get("city_label") or loc.get("city") or "").strip()
    zipcode = str(loc.get("zipcode") or "").strip()
    if city and zipcode and zipcode not in city:
        return f"{city} {zipcode}"
    return city or zipcode or "France"


def _lbc_safe_desc(body: str, attrs: list[dict[str, Any]], brand: str | None = None) -> str:
    item_like: dict[str, Any] = {"body": body or "", "attributes": attrs}
    if brand:
        item_like["brand"] = brand
    if "_item_description" in globals():
        try:
            res = _item_description(item_like)
            if res:
                return res
        except Exception:
            pass
    return clean_html(body or "")[:2000]


def _lbc_safe_shipping(item_like: dict[str, Any]) -> float | None:
    if "item_shipping" in globals():
        try:
            return item_shipping(item_like)
        except Exception:
            return None
    return None


def _lbc_price_val(raw: Any) -> float | None:
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _lbc_resolve_enum(enum_cls: Any, value: Any) -> Any:
    if enum_cls is None or value is None or str(value).strip() == "":
        return None
    val = str(value).strip()
    try:
        return enum_cls(val)
    except Exception:
        for member in enum_cls:
            if str(getattr(member, "value", "")).lower() == val.lower() or member.name.lower() == val.lower():
                return member
    return None


def _lbc_build_proxy(lbc_mod: Any, proxy_cfg: Any) -> Any:
    proxy_cls = getattr(lbc_mod, "Proxy", None)
    if not proxy_cfg or proxy_cls is None:
        return None
    if isinstance(proxy_cfg, str):
        u = _lbc_urlsplit(proxy_cfg)
        if not u.hostname or not u.port:
            return None
        return proxy_cls(host=u.hostname, port=u.port, username=u.username, password=u.password, scheme=u.scheme or "http")
    if isinstance(proxy_cfg, dict) and proxy_cfg.get("host") and proxy_cfg.get("port"):
        return proxy_cls(
            host=str(proxy_cfg["host"]),
            port=int(proxy_cfg["port"]),
            username=proxy_cfg.get("username"),
            password=proxy_cfg.get("password"),
            scheme=str(proxy_cfg.get("scheme") or "http"),
        )
    return None


def _lbc_obj_to_ad(item: Any, source: str) -> Ad | None:
    status = getattr(item, "status", None)
    if status not in (None, "active"):
        return None
    item_url = str(getattr(item, "url", "") or "").split("?")[0]
    if not item_url:
        return None

    raw_images = getattr(item, "images", None)
    image = None
    if isinstance(raw_images, list):
        image = next((u for u in raw_images if isinstance(u, str) and u.startswith("http")), None)
    elif isinstance(raw_images, dict):
        image = ad_image({"images": raw_images})

    attrs_raw = getattr(item, "attributes", None) or []
    attrs = [
        a if isinstance(a, dict) else {
            "key": getattr(a, "key", None),
            "value": getattr(a, "value", None),
            "value_label": getattr(a, "value_label", None),
        }
        for a in attrs_raw
    ]
    item_like = {"body": getattr(item, "body", "") or "", "attributes": attrs}

    return Ad(
        title=clean_html(getattr(item, "subject", "") or "")[:200],
        url=item_url,
        source=source,
        price=_lbc_price_val(getattr(item, "price", None)),
        shipping=_lbc_safe_shipping(item_like),
        description=_lbc_safe_desc(item_like["body"], attrs),
        location=_lbc_safe_place(getattr(item, "location", None)),
        image=image,
        seller=_lbc_owner_dict(getattr(item, "owner", None)),
    )


def _scrapebadger_dict_to_ad(item: dict[str, Any], source: str) -> Ad | None:
    if item.get("status") not in (None, "active"):
        return None
    item_url = str(item.get("url") or "").split("?")[0]
    if not item_url:
        list_id = item.get("list_id")
        if not list_id:
            return None
        item_url = f"https://www.leboncoin.fr/ad/offres/{list_id}"
    attrs = [a for a in (item.get("attributes") or []) if isinstance(a, dict)]
    return Ad(
        title=clean_html(item.get("subject") or "")[:200],
        url=item_url,
        source=source,
        price=_lbc_price_val(item.get("price_eur") if item.get("price_eur") is not None else item.get("price")),
        shipping=_lbc_safe_shipping(item),
        description=_lbc_safe_desc(item.get("body") or "", attrs, brand=item.get("brand")),
        location=_lbc_safe_place(item.get("location")),
        image=ad_image(item),
        seller=_lbc_owner_dict(item.get("owner")),
    )


_BaseLeboncoinSite = LeboncoinSite


class LeboncoinSite(_BaseLeboncoinSite):
    def __init__(self, cfg: dict[str, Any], config: dict[str, Any]):
        super().__init__(cfg, config)
        wanted = str(cfg.get("backend") or "browser").strip().lower()
        if wanted == "auto":
            if _lbc_env("SCRAPEBADGER_API_KEY"):
                wanted = "scrapebadger"
            else:
                try:
                    import lbc  # noqa: F401
                    wanted = "lbc"
                except ImportError:
                    wanted = "browser"
        self.backend = wanted if wanted in ("lbc", "scrapebadger", "browser") else "browser"

    def is_configured(self) -> bool:
        if not super().is_configured():
            return False
        if self.backend == "scrapebadger":
            return bool(_lbc_env("SCRAPEBADGER_API_KEY"))
        return True

    async def list_recent(self, http: PoliteClient) -> list[Ad]:
        if self.backend == "scrapebadger":
            return await self._list_scrapebadger(http)
        if self.backend == "lbc":
            try:
                return await self._list_lbc()
            except ImportError:
                log.warning("leboncoin : module `lbc` absent, repli sur le navigateur.")
        return await super().list_recent(http)

    async def _list_lbc(self) -> list[Ad]:
        import lbc

        limit = int(self.cfg.get("limit", 70))
        default_pages = max(1, int(self.cfg.get("max_pages", 3)))
        default_owner = self.cfg.get("owner_type")
        default_title_only = bool(self.cfg.get("title_only", False))
        proxy = _lbc_build_proxy(lbc, self.cfg.get("proxy"))
        client = lbc.Client(proxy=proxy) if proxy else lbc.Client()
        allowed_params = set(_lbc_inspect.signature(client.search).parameters)

        ads: list[Ad] = []
        seen_urls: set[str] = set()

        for search in self.cfg["searches"]:
            query = _lbc_query_str(search)
            if not query:
                continue
            want = int(search.get("limit", limit))
            max_pages = min(int(search.get("max_pages", default_pages)), max(1, (want + 34) // 35))
            log.info("leboncoin [lbc] : recherche « %s » (cible %d, max %d page(s))...", query, want, max_pages)

            raw_kwargs: dict[str, Any] = {
                "text": query,
                "limit": min(35, want),
                "limit_alu": 0,
                "sort": getattr(getattr(lbc, "Sort", None), "NEWEST", None),
                "ad_type": getattr(getattr(lbc, "AdType", None), "OFFER", None),
                "search_in_title_only": bool(search.get("title_only", default_title_only)),
            }
            cat = _lbc_resolve_enum(getattr(lbc, "Category", None), search.get("category"))
            if cat is not None:
                raw_kwargs["category"] = cat
            owner_key = str(search.get("owner_type", default_owner) or "").lower()
            if owner_key == "particulier":
                owner_key = "private"
            owner = _lbc_resolve_enum(getattr(lbc, "OwnerType", None), owner_key)
            if owner is not None:
                raw_kwargs["owner_type"] = owner
            if search.get("max_price") is not None:
                raw_kwargs["price"] = [int(search.get("min_price", 1)), int(search["max_price"])]
            if search.get("lat") is not None and search.get("lng") is not None and hasattr(lbc, "City"):
                raw_kwargs["locations"] = [
                    lbc.City(
                        lat=float(search["lat"]),
                        lng=float(search["lng"]),
                        radius=int(float(search.get("radius_km", 30)) * 1000),
                        city=str(search.get("city") or ""),
                    )
                ]

            kwargs = {k: v for k, v in raw_kwargs.items() if k in allowed_params and v is not None}

            count = 0
            for page_num in range(1, max_pages + 1):
                if count >= want:
                    break
                try:
                    res = await asyncio.to_thread(client.search, page=page_num, **kwargs)
                except Exception as exc:
                    msg = str(exc).lower()
                    if any(tok in msg for tok in ("403", "datadome", "forbidden", "blocked")):
                        if not ads:
                            raise AccessDenied(f"leboncoin [lbc] : refus DataDome ({exc})") from exc
                        log.warning("leboncoin [lbc] : 403 page %d de « %s », on garde le reste.", page_num, query)
                        break
                    log.warning("leboncoin [lbc] : erreur page %d de « %s » (%s).", page_num, query, exc)
                    break

                page_items = getattr(res, "ads", None) or []
                if not page_items:
                    break

                added_page = 0
                for item in page_items:
                    if count >= want:
                        break
                    ad = _lbc_obj_to_ad(item, self.name)
                    if not ad or ad.url in seen_urls:
                        continue
                    seen_urls.add(ad.url)
                    ads.append(ad)
                    count += 1
                    added_page += 1

                if added_page == 0 or len(page_items) < 20:
                    break
                if page_num < max_pages and count < want:
                    await asyncio.sleep(random.uniform(2.5, 5.0))

            log.info("leboncoin [lbc] : %d annonce(s) trouvée(s) pour « %s »", count, query)
            self.report(query, count)
            await asyncio.sleep(random.uniform(4.0, 7.5))

        return ads

    async def _list_scrapebadger(self, http: PoliteClient) -> list[Ad]:
        api_key = _lbc_env("SCRAPEBADGER_API_KEY")
        if not api_key:
            raise AccessDenied("SCRAPEBADGER_API_KEY manquante dans .env")

        headers = {"X-API-Key": api_key}
        limit = int(self.cfg.get("limit", 100))
        default_pages = max(1, int(self.cfg.get("max_pages", 2)))
        default_owner = self.cfg.get("owner_type")
        ads: list[Ad] = []
        seen_urls: set[str] = set()

        for search in self.cfg["searches"]:
            query = _lbc_query_str(search)
            if not query:
                continue
            want = int(search.get("limit", limit))
            per_page = min(100, want)
            max_pages = min(int(search.get("max_pages", default_pages)), max(1, (want + per_page - 1) // per_page))
            log.info("leboncoin [scrapebadger] : recherche « %s » (cible %d)...", query, want)

            count = 0
            for page_num in range(1, max_pages + 1):
                if count >= want:
                    break
                params: dict[str, Any] = {
                    "text": query,
                    "ad_type": "offer",
                    "sort": "newest",
                    "page": page_num,
                    "limit": min(per_page, want - count),
                }
                if search.get("category") is not None:
                    params["category"] = str(search["category"])
                if search.get("min_price") is not None:
                    params["price_min"] = int(search["min_price"])
                if search.get("max_price") is not None:
                    params["price_max"] = int(search["max_price"])
                owner = str(search.get("owner_type", default_owner) or "").lower()
                if owner in ("all", "pro", "private"):
                    params["owner_type"] = owner
                for geo_key in ("region_id", "department_id", "city", "zipcode"):
                    val = search.get(geo_key, self.cfg.get(geo_key))
                    if val is not None and str(val).strip():
                        params[geo_key] = str(val).strip()

                resp = await http.get(_SCRAPEBADGER_LBC_URL, headers=headers, params=params)
                if resp.status_code == 402:
                    raise AccessDenied("ScrapeBadger : crédits épuisés (402)")
                if resp.status_code == 429:
                    raise AccessDenied("ScrapeBadger : limite de requêtes atteinte (429)")
                resp.raise_for_status()

                payload = resp.json()
                page_items = payload.get("ads") or []
                if not page_items:
                    break

                added_page = 0
                for item in page_items:
                    if count >= want:
                        break
                    ad = _scrapebadger_dict_to_ad(item, self.name)
                    if not ad or ad.url in seen_urls:
                        continue
                    seen_urls.add(ad.url)
                    ads.append(ad)
                    count += 1
                    added_page += 1

                if added_page == 0 or page_num >= int(payload.get("max_pages") or page_num):
                    break

            log.info("leboncoin [scrapebadger] : %d annonce(s) trouvée(s) pour « %s »", count, query)
            self.report(query, count)

        return ads
