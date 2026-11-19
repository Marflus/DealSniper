"""URL : lien de suivi décodé, URL canonique, identifiant unique d'une annonce."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

# Paramètres d'URL qui cachent la vraie destination d'un lien de suivi (mpre : eBay).
_REDIRECT_PARAMS = ("url", "u", "redirect", "target", "dest", "destination", "link", "mpre")
# Paramètres de suivi publicitaire, sans intérêt pour identifier une annonce.
_TRACKING_PREFIXES = ("utm_", "xtor", "at_", "mc_", "fbclid", "gclid", "campid", "mkevt", "mkcid", "mkrid", "hash")
# Identifiant natif par site : le plus fiable pour reconnaître une annonce déjà vue.
_NATIVE_IDS = (
    ("ebay", re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{9,})")),
    ("lbc", re.compile(r"leboncoin\.fr/(?:ad/[^/?#]+/|[^/?#]+/)(\d{6,})")),
    ("vinted", re.compile(r"vinted\.[a-z.]+/items/(\d{5,})")),
)


def unwrap_tracking_url(url: str) -> str:
    """Si `url` est un lien de redirection, renvoie la cible lisible dans ses paramètres."""
    for key in _REDIRECT_PARAMS:
        for value in parse_qs(urlparse(url).query).get(key, []):
            if unquote(value).startswith("http"):
                return unwrap_tracking_url(unquote(value))
    return url


def canonical_url(url: str) -> str:
    """URL sans fragment ni paramètres de suivi."""
    p = urlparse(url.strip())
    kept = [q for q in p.query.split("&") if q and not q.lower().startswith(_TRACKING_PREFIXES)]
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", "&".join(kept), ""))


def native_id(url: str) -> str | None:
    """« ebay:123456789012 » si l'URL contient l'identifiant du site, sinon None."""
    for prefix, rx in _NATIVE_IDS:
        m = rx.search(url or "")
        if m:
            return f"{prefix}:{m.group(1)}"
    return None


def ad_uid(url: str) -> str:
    """Identifiant unique d'une annonce : identifiant natif, sinon empreinte de l'URL canonique."""
    return native_id(url) or "h:" + hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]
