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


async def check_one(http: PoliteClient, ebay: Any, ad: dict[str, Any]) -> tuple[str, str, bool]:
    """(statut, précision, arrêter ce site) d'une annonce : active / gone / unknown.

    Le site qui refuse (401/403) ou limite (429) n'est plus interrogé pendant cette vérification :
    insister ne ferait qu'empirer le blocage (et la vérification semblerait figée en attendant)."""
    source = ad.get("source") or ""
    if source in UNCHECKABLE:
        return UNKNOWN, UNCHECKABLE[source], False
    try:
        if source == "ebay" and ebay is not None:
            return await _ebay_status(http, ebay, ad), "", False
        return await _link_status(http, ad), "", False
    except AccessDenied:
        return UNKNOWN, "le site refuse ces vérifications", True
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            return UNKNOWN, "le site limite les vérifications : réessayez plus tard", True
        return UNKNOWN, f"erreur du site ({exc.response.status_code if exc.response is not None else '?'})", False
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        return UNKNOWN, type(exc).__name__, False


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
    report: dict[str, Any] = {"total": len(ads), "done": 0, "active": 0, "gone": [], "unknown": [], "cancelled": False,
                              "pending": {}, "eta_s": 0}
    statuses: dict[str, str] = {}
    owned = http is None
    # Pas de nouvel essai : une erreur passagère donne « non vérifiable », plutôt qu'une longue attente.
    client = http or PoliteClient(network["user_agent"], float(network["min_host_interval"]),
                                  host_intervals=network.get("host_intervals"), max_retries=0)
    interval = float(network["min_host_interval"]) * 1.1  # rythme poli moyen (avec l'aléa)
    async with client if owned else contextlib.nullcontext(client) as http:
        # Un site à la fois mais tous les sites en parallèle : chacun garde son propre rythme poli.
        by_site: dict[str, list[dict[str, Any]]] = {}
        for ad in ads:
            by_site.setdefault(ad.get("source") or "?", []).append(ad)

        def record(ad: dict[str, Any], status: str, note: str) -> None:
            statuses[ad["uid"]] = status
            entry = {"uid": ad["uid"], "title": ad.get("title", ""), "url": ad.get("url", ""),
                     "source": ad.get("source", ""), "note": note}
            if status == ACTIVE:
                report["active"] += 1
            else:
                report["gone" if status == GONE else "unknown"].append(entry)
            report["done"] += 1

        def update_eta() -> None:
            """Restant par site et durée estimée (le site le plus long fixe la fin)."""
            pending = report["pending"]
            report["eta_s"] = round(max([n * (1.2 if site == "ebay" and ebay else interval)
                                         for site, n in pending.items() if site not in UNCHECKABLE] or [0]))

        async def run_site(site: str, items: list[dict[str, Any]]) -> None:
            for i, ad in enumerate(items):
                if stop and stop():
                    report["cancelled"] = True
                    return
                status, note, stop_site = await check_one(http, ebay, ad)
                record(ad, status, note)
                report["pending"][site] = len(items) - i - 1
                if stop_site:  # site qui refuse ou limite : le reste de ses annonces sans requête
                    for rest in items[i + 1:]:
                        record(rest, UNKNOWN, note)
                    report["pending"][site] = 0
                update_eta()
                if on_progress:
                    on_progress(report)
                if stop_site:
                    return

        report["pending"] = {site: len(items) for site, items in by_site.items()}
        update_eta()
        await asyncio.gather(*(run_site(site, items) for site, items in by_site.items()))
    report["statuses"] = statuses
    report["checked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return report
