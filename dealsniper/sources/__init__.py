"""Sources d'annonces : emails d'alerte (IMAP) et flux RSS.

Chaque source renvoie des `Ad` et un `SourceReport` (santé de la source),
sans jamais lever d'exception : une source en panne n'arrête pas les autres.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..models import Ad

log = logging.getLogger(__name__)


@dataclass
class SourceReport:
    name: str
    ok: bool = True
    error: str | None = None
    items: int = 0      # emails lus / entrées de flux lues
    ads: int = 0        # annonces extraites


def collect_all(config: dict[str, Any], seen) -> tuple[list[Ad], list[SourceReport]]:
    """Interroge toutes les sources actives et retire doublons et annonces déjà vues."""
    from . import email_alerts, rss

    sources_cfg = config["sources"]
    ads: list[Ad] = []
    reports: list[SourceReport] = []
    if sources_cfg["email"].get("enabled", True):
        found, report = email_alerts.collect(sources_cfg["email"])
        ads += found
        reports.append(report)
    if sources_cfg["rss"].get("enabled", True) and sources_cfg["rss"].get("feeds"):
        found, rss_reports = rss.collect(sources_cfg["rss"])
        ads += found
        reports += rss_reports

    unique: dict[str, Ad] = {}
    for ad in ads:
        if ad.uid not in seen and ad.uid not in unique:
            unique[ad.uid] = ad
    log.info("Collecte : %d annonce(s) brute(s), %d nouvelle(s)", len(ads), len(unique))
    return list(unique.values()), reports
