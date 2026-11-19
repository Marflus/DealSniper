"""URL : identifiant unique d'une annonce (pour ne jamais la traiter deux fois)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse

# Paramètres de suivi publicitaire, sans intérêt pour identifier une annonce.
_TRACKING_PREFIXES = ("utm_", "xtor", "at_", "mc_", "fbclid", "gclid", "campid", "mkevt", "mkcid", "mkrid", "hash")
_EBAY_ID = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]+/)?(\d{9,})")


def canonical_url(url: str) -> str:
    """URL sans fragment ni paramètres de suivi."""
    p = urlparse(url.strip())
    kept = [q for q in p.query.split("&") if q and not q.lower().startswith(_TRACKING_PREFIXES)]
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", "&".join(kept), ""))


def native_id(url: str) -> str | None:
    """« ebay:123456789012 » pour une annonce eBay, sinon None."""
    m = _EBAY_ID.search(url or "")
    return f"ebay:{m.group(1)}" if m else None


def ad_uid(url: str) -> str:
    """Identifiant unique : numéro eBay, sinon empreinte de l'URL canonique."""
    return native_id(url) or "h:" + hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]
