"""Vérifie si des annonces déjà enregistrées sont toujours en ligne (bouton de l'onglet Annonces).

- eBay : API officielle (fiche de l'objet) si les secrets sont configurés : vendu, terminé ou supprimé → disparue.
- Vinted et autres sites simples : on ouvre le lien ; 404 / 410 → disparue, page affichée → active.
- Leboncoin et Facebook : non vérifiables sans contourner leur protection (ou sans être connecté) : on ne
  les interroge pas, elles sont comptées « non vérifiables ».

Aucune IA, aucun coût. Les requêtes respectent le rythme poli configuré (`network`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import env
from .http import AccessDenied, PoliteClient
from .utils.urls import native_id

log = logging.getLogger(__name__)

UNCHECKABLE = {"leboncoin": "protection anti-robots", "facebook": "connexion requise"}
GONE_CODES = {404, 410}
# Pages encore affichées mais annonce terminée (eBay sans clé API, Vinted…) : début de la page seulement.
ENDED_PHRASES = ("cette annonce est terminée", "l'annonce est terminée", "cet objet n'est plus disponible",
                 "this listing has ended", "listing was ended", "cet article a été vendu", "l'enchère est terminée",
                 "cette vente est terminée",
                 "cette annonce n'est plus disponible", "l'article n'est plus disponible")

ACTIVE, GONE, UNKNOWN = "active", "gone", "unknown"


async def _ebay_status(http: PoliteClient, ebay: Any, ad: dict[str, Any]) -> str:
    from .sites.ebay import ITEM_URL, is_available

    nid = native_id(ad.get("url") or "")
    if not nid:
        return UNKNOWN
    resp = await http.get(ITEM_URL, headers=await ebay._headers(http), params={"legacy_item_id": nid.split(":", 1)[1]})
    if resp.status_code in GONE_CODES:
        return GONE
    resp.raise_for_status()
    return ACTIVE if is_available(resp.json()) else GONE


async def _link_status(http: PoliteClient, ad: dict[str, Any]) -> str:
    url = ad.get("url") or ""
    resp = await http.get(url)
    if resp.status_code in GONE_CODES:
        return GONE
    if resp.status_code >= 400:
        return UNKNOWN
    # Redirigée ailleurs (accueil, catalogue) : l'annonce n'existe plus.
    item_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if item_id and item_id not in str(resp.url):
        return GONE
    page = resp.text[:200_000].lower().replace("\u2019", "'").replace("’", "'")
    return GONE if any(p in page for p in ENDED_PHRASES) else ACTIVE


async def check_one(http: PoliteClient, ebay: Any, ad: dict[str, Any]) -> tuple[str, str]:
    """(statut, précision) d'une annonce : active / gone / unknown."""
    source = ad.get("source") or ""
    if source in UNCHECKABLE:
        return UNKNOWN, UNCHECKABLE[source]
    try:
        if source == "ebay" and ebay is not None:
            return await _ebay_status(http, ebay, ad), ""
        return await _link_status(http, ad), ""
    except AccessDenied as exc:
        return UNKNOWN, f"accès refusé ({exc})"
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        return UNKNOWN, type(exc).__name__


async def check_ads(ads: list[dict[str, Any]], config: dict[str, Any],
                    on_progress: Callable[[dict[str, Any]], None] | None = None,
                    stop: Callable[[], bool] | None = None, http: PoliteClient | None = None) -> dict[str, Any]:
    """Vérifie les annonces ; renvoie le bilan et, par annonce, son statut (pour l'enregistrer).
    `http` : client à utiliser (tests) ; sinon un client poli est créé avec les réglages `network`."""
    network = config["network"]
    ebay = None
    if env("EBAY_CLIENT_ID") and env("EBAY_CLIENT_SECRET") and "ebay" in config["sites"]:
        from .sites.ebay import EbaySite

        ebay = EbaySite({"marketplace": "EBAY_FR", **config["sites"]["ebay"]}, config)
    report: dict[str, Any] = {"total": len(ads), "done": 0, "active": 0, "gone": [], "unknown": [], "cancelled": False}
    statuses: dict[str, str] = {}
    owned = http is None
    client = http or PoliteClient(network["user_agent"], float(network["min_host_interval"]),
                                  host_intervals=network.get("host_intervals"), max_retries=1)
    async with client if owned else contextlib.nullcontext(client) as http:
        # Un site à la fois mais tous les sites en parallèle : chacun garde son propre rythme poli.
        by_site: dict[str, list[dict[str, Any]]] = {}
        for ad in ads:
            by_site.setdefault(ad.get("source") or "?", []).append(ad)

        async def run_site(items: list[dict[str, Any]]) -> None:
            for ad in items:
                if stop and stop():
                    report["cancelled"] = True
                    return
                status, note = await check_one(http, ebay, ad)
                statuses[ad["uid"]] = status
                entry = {"uid": ad["uid"], "title": ad.get("title", ""), "url": ad.get("url", ""),
                         "source": ad.get("source", ""), "note": note}
                if status == ACTIVE:
                    report["active"] += 1
                else:
                    report["gone" if status == GONE else "unknown"].append(entry)
                report["done"] += 1
                if on_progress:
                    on_progress(report)

        await asyncio.gather(*(run_site(items) for items in by_site.values()))
    report["statuses"] = statuses
    report["checked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return report
