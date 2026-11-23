"""Lit une annonce à partir de son lien collé (onglet « Analyse »).

Ordre :
1. L'annonce est déjà en base (vue lors d'un passage) : on reprend ce qu'on sait, sans requête.
2. Sinon, UNE requête normale vers la page, avec le rythme poli et l'identité habituels de Deal Sniper,
   puis lecture des données publiques standard de la page : JSON-LD « Product » (Vinted, la plupart des
   boutiques), balises Open Graph (og:title, og:image…), données de page leboncoin (__NEXT_DATA__).
3. Le site refuse ou la page ne contient rien d'exploitable : `LinkUnreadable`. Aucun contournement :
   l'interface propose alors de coller le titre, le prix et la description à la main.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .models import Ad
from .utils.text import clean_html
from .utils.urls import canonical_url

MAX_PHOTOS = 4
SITES = {"vinted": "vinted", "leboncoin": "leboncoin", "facebook": "facebook", "ebay": "ebay",
         "backmarket": "backmarket", "rakuten": "rakuten", "label-emmaus": "label emmaüs"}


class LinkUnreadable(RuntimeError):
    """Page impossible à lire automatiquement (protection, connexion requise, format inconnu)."""


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for key, name in SITES.items():
        if key in host:
            return name
    return host.removeprefix("www.") or "?"


def check_url(url: str) -> str:
    """Lien http(s) vers un site public (pas d'adresse locale : l'interface ne sert pas de relais)."""
    url = (url or "").strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or "." not in host:
        raise ValueError("Collez le lien complet de l'annonce (https://…)")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if host == "localhost" or host.endswith((".local", ".lan", ".internal")) or (ip and not ip.is_global):
        raise ValueError("Lien local refusé : collez le lien d'une annonce en ligne")
    return url


# ---------- Lecture de la page ----------

def _meta(page: str) -> dict[str, str]:
    """Balises <meta property|name="…" content="…"> (Open Graph, product:price…)."""
    out: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", page, flags=re.I):
        key = re.search(r'(?:property|name)\s*=\s*["\']([^"\']+)', tag, flags=re.I)
        value = re.search(r'content\s*=\s*["\']([^"\']*)', tag, flags=re.I)
        if key and value and key.group(1).lower() not in out:
            out[key.group(1).lower()] = html.unescape(value.group(1)).strip()
    return out


def _json_blocks(page: str, kind: str) -> list[Any]:
    pattern = rf'<script[^>]+{kind}[^>]*>(.*?)</script>'
    blocks = []
    for raw in re.findall(pattern, page, flags=re.S | re.I):
        try:
            blocks.append(json.loads(raw.strip()))
        except ValueError:
            continue
    return blocks


def _products(data: Any) -> list[dict[str, Any]]:
    """Objets « Product » d'un bloc JSON-LD (liste, @graph ou objet seul)."""
    items = data if isinstance(data, list) else (data.get("@graph") if isinstance(data, dict) and "@graph" in data
                                                 else [data])
    out = []
    for item in items or []:
        if isinstance(item, dict):
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" in kinds or "Offer" in kinds or "Car" in kinds:
                out.append(item)
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:[.,]\d+)?", str(value or "").replace(" ", "").replace(" ", ""))
    return float(match.group().replace(",", ".")) if match else None


def _images(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(value.get("url") or value.get("contentUrl") or "")]
    if isinstance(value, list):
        return [u for v in value for u in _images(v)]
    return []


def parse_page(page: str, url: str) -> dict[str, Any]:
    """Titre, prix, description, photos et ville d'une page d'annonce ({} si rien d'exploitable)."""
    found: dict[str, Any] = {"photos": []}
    # leboncoin : données de la page (titre, prix, texte du vendeur, photos, ville)
    for data in _json_blocks(page, r'id=["\']__NEXT_DATA__["\']'):
        ad = (((data or {}).get("props") or {}).get("pageProps") or {}).get("ad") or {}
        if ad.get("subject"):
            price = ad.get("price")
            found.update(title=ad["subject"], description=clean_html(ad.get("body") or ""),
                         price=_number(price[0] if isinstance(price, list) and price else price),
                         location=" ".join(p for p in ((ad.get("location") or {}).get("city"),
                                                       (ad.get("location") or {}).get("zipcode")) if p) or None)
            found["photos"] += list((ad.get("images") or {}).get("urls_large") or
                                     (ad.get("images") or {}).get("urls") or [])
    # JSON-LD « Product » : format standard (Vinted, eBay, boutiques)
    for data in _json_blocks(page, r'type=["\']application/ld\+json["\']'):
        for item in _products(data):
            offers = item.get("offers") or {}
            offer = offers[0] if isinstance(offers, list) and offers else offers
            found.setdefault("title", item.get("name"))
            found.setdefault("description", clean_html(str(item.get("description") or "")))
            if found.get("price") is None and isinstance(offer, dict):
                found["price"] = _number(offer.get("price") or offer.get("lowPrice"))
                avail = str(offer.get("availability") or "").lower()
                if avail and not avail.endswith(("instock", "limitedavailability", "preorder", "onlineonly")):
                    found["available"] = False
            found["photos"] += _images(item.get("image"))
    # Open Graph : repli universel
    meta = _meta(page)
    found.setdefault("title", meta.get("og:title") or meta.get("twitter:title"))
    if not found.get("description"):
        found["description"] = meta.get("og:description") or meta.get("description") or ""
    if found.get("price") is None:
        found["price"] = _number(meta.get("product:price:amount") or meta.get("og:price:amount"))
    found["photos"] += [meta[k] for k in ("og:image", "og:image:url", "twitter:image") if meta.get(k)]
    photos = []
    for photo in found["photos"]:
        full = urljoin(url, photo.strip()) if photo else ""
        if full.startswith(("http://", "https://")) and full not in photos:
            photos.append(full)
    found["photos"] = photos[:MAX_PHOTOS]
    if not found.get("title"):
        return {}
    found["title"] = html.unescape(str(found["title"])).strip()[:200]
    found["description"] = str(found.get("description") or "")[:3000]
    return found


def fetch_ad(url: str, config: dict[str, Any], http: Any = None) -> tuple[Ad, list[str]]:
    """Lit la page de l'annonce (une requête) → (annonce, photos). Lève LinkUnreadable si impossible."""
    url = check_url(url)
    site = site_of(url)
    network = config.get("network") or {}
    headers = {"User-Agent": network.get("user_agent") or "DealSniper/3.0 (usage personnel)",
               "Accept-Language": "fr-FR,fr;q=0.9"}
    try:
        if http is None:
            resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        else:
            resp = http(url, headers)
    except httpx.HTTPError as exc:
        raise LinkUnreadable(f"{site} injoignable ({type(exc).__name__}) : collez les infos à la main.") from exc
    if resp.status_code in (404, 410):
        raise LinkUnreadable("Cette annonce n'existe plus (page introuvable).")
    if resp.status_code in (401, 403, 429) or resp.status_code >= 400:
        raise LinkUnreadable(f"{site} refuse la lecture automatique de cette page (code {resp.status_code}) : "
                             "collez le titre, le prix et la description à la main.")
    found = parse_page(resp.text[:3_000_000], str(resp.url))
    if not found:
        raise LinkUnreadable(f"Rien de lisible sur cette page {site} (connexion requise ou format inconnu) : "
                             "collez le titre, le prix et la description à la main.")
    ad = Ad(title=found["title"], url=canonical_url(url), source=site, price=found.get("price"),
            description=found.get("description") or "", location=found.get("location"),
            image=(found["photos"] or [None])[0], available=found.get("available", True))
    return ad, found["photos"]
