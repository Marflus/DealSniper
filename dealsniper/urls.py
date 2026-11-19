"""URL : décodage des liens de suivi des emails, URL canonique, identifiant stable d'annonce."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

from .text import normalize

log = logging.getLogger(__name__)

# Paramètres qui contiennent souvent la vraie destination d'un lien de suivi
# (mpre : rover.ebay.com ; url : Outlook Safe Links, Google…).
_REDIRECT_PARAMS = ("url", "u", "redirect", "redirect_url", "target", "dest", "destination", "link", "mpre")
_TRACKING_PREFIXES = (
    "utm_", "xtor", "at_", "mc_", "fbclid", "gclid", "campid", "mkevt", "mkcid", "mkrid", "_trk", "hash",
)
_NATIVE_ID_RES = (
    ("lbc", re.compile(r"leboncoin\.fr/(?:ad/[^/?#]+/|[^/?#]+/)(\d{6,})")),
    ("ebay", re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{9,})")),
    ("vinted", re.compile(r"vinted\.[a-z.]+/items/(\d{5,})")),
)


def unwrap_tracking_url(url: str) -> str:
    """Renvoie la cible d'un lien de redirection d'emailing si elle est lisible dans l'URL."""
    try:
        for key in _REDIRECT_PARAMS:
            for value in parse_qs(urlparse(url).query).get(key, []):
                value = unquote(value)
                if value.startswith("http"):
                    return unwrap_tracking_url(value)
    except (ValueError, TypeError):
        pass
    return url


def canonical_url(url: str) -> str:
    """URL sans fragment ni paramètres de suivi."""
    try:
        p = urlparse(url.strip())
        kept = [q for q in p.query.split("&") if q and not q.lower().startswith(_TRACKING_PREFIXES)]
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", "&".join(kept), ""))
    except ValueError:
        return url.strip()


def native_id(url: str) -> str | None:
    """Identifiant natif de la plateforme (ex. "lbc:2512345678"), si l'URL en contient un."""
    for prefix, rx in _NATIVE_ID_RES:
        m = rx.search(url or "")
        if m:
            return f"{prefix}:{m.group(1)}"
    return None


def ad_uid(url: str, title: str = "", price: float | None = None, url_is_stable: bool = True) -> str:
    """Identifiant stable servant au dédoublonnage.

    Priorité : identifiant natif > URL canonique > titre+prix (pour les liens de
    suivi opaques, qui changent d'un email à l'autre pour la même annonce).
    """
    nid = native_id(url)
    if nid:
        return nid
    basis = canonical_url(url) if (url and url_is_stable) else f"{normalize(title)}|{price}"
    return "h:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def matches_any(url: str, patterns: Iterable[re.Pattern]) -> bool:
    return any(p.search(url) for p in patterns)


class RedirectResolver:
    """Suit les redirections d'un lien de suivi pour retrouver l'URL de l'annonce.

    Seuls les en-têtes `Location` sont lus (pas de téléchargement de page), avec
    un nombre de liens limité par passage et un cache.
    """

    def __init__(self, patterns: list[re.Pattern], max_links: int = 60, timeout: float = 6.0):
        self.patterns = patterns
        self.max_links = max_links
        self.timeout = timeout
        self._cache: dict[str, str | None] = {}
        self._session = None

    def __call__(self, url: str) -> str | None:
        if url in self._cache:
            return self._cache[url]
        if len(self._cache) >= self.max_links:
            return None
        self._cache[url] = result = self._resolve(url)
        return result

    def _resolve(self, url: str) -> str | None:
        import requests

        if self._session is None:
            self._session = requests.Session()
            self._session.headers["User-Agent"] = "Mozilla/5.0 (compatible; DealSniper/2.0)"
        current = url
        for _ in range(6):
            try:
                resp = self._session.head(current, allow_redirects=False, timeout=self.timeout)
                if resp.status_code in (405, 501):  # HEAD refusé : GET sans lire le corps
                    resp = self._session.get(current, allow_redirects=False, timeout=self.timeout, stream=True)
                    resp.close()
            except requests.RequestException as exc:
                log.debug("Redirection non résolue (%s) : %s", exc, current[:80])
                return None
            location = resp.headers.get("Location")
            if not location:
                return None
            current = unwrap_tracking_url(urljoin(current, location))
            if matches_any(current, self.patterns):
                return current
        return None
