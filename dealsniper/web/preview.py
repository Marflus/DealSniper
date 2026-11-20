"""Photo d'une annonce à partir de l'aperçu de son lien (balise og:image), comme le fait Telegram.

Une seule requête par annonce, uniquement pour les annonces affichées sans photo ; le résultat
(photo ou absence de photo) est mémorisé en base. Un site qui refuse (401/403/429) n'est plus
sollicité pendant quelques heures.
"""

from __future__ import annotations

import re
import threading
import time
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "DealSniper/3.0 (apercu de lien, usage personnel)"
REFUSED_PAUSE = 6 * 3600
_META_RE = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR_RE = re.compile(r'(\w[\w:-]*)\s*=\s*("([^"]*)"|\'([^\']*)\')')

_GENERIC = ("rsrc.php", "/images/logos/", "logo", "default-share", "og-default")

_refused: dict[str, float] = {}   # site → fin de la pause
_lock = threading.Lock()          # une requête à la fois


def og_image(html: str, base_url: str) -> str | None:
    """URL de og:image / twitter:image dans le <head> d'une page."""
    for tag in _META_RE.findall(html):
        attrs = {m[0].lower(): (m[2] or m[3]) for m in _ATTR_RE.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = (attrs.get("content") or "").strip()
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image") and content:
            if any(generic in content for generic in _GENERIC):  # logo du site, pas la photo de l'annonce
                continue
            return urljoin(base_url, unescape(content))
    return None


def fetch_image(url: str) -> str | None:
    """Photo de l'annonce, ou None (pas d'aperçu, site qui refuse, erreur réseau)."""
    host = urlsplit(url).hostname or ""
    if not url.startswith(("http://", "https://")) or _refused.get(host, 0) > time.time():
        return None
    with _lock:
        try:
            headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}
            with httpx.Client(timeout=8, follow_redirects=True, headers=headers) as client, \
                    client.stream("GET", url) as resp:
                if resp.status_code in (401, 403, 429):
                    _refused[host] = time.time() + REFUSED_PAUSE
                    return None
                if resp.status_code != 200:
                    return None
                head = b""
                for chunk in resp.iter_bytes():  # le <head> suffit : on s'arrête à 300 Ko
                    head += chunk
                    if len(head) > 300_000 or b"</head>" in head.lower():
                        break
        except httpx.HTTPError:
            return None
    return og_image(head.decode("utf-8", "replace"), url)
