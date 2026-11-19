"""Source « flux RSS / Atom » (recherches sauvegardées exposées en RSS, RSS-Bridge…)."""

from __future__ import annotations

import logging
from typing import Any

from ..location import extract_location
from ..models import Ad
from ..prices import detect_price_mode, extract_price, extract_shipping
from ..text import clean_html
from ..urls import unwrap_tracking_url
from . import SourceReport

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; DealSniper/2.0; +rss-reader)"


def _entry_price(entry: Any, text: str) -> float | None:
    # Certains flux eBay exposent <ebay:CurrentPrice> en centimes.
    raw = str(entry.get("ebay_currentprice") or "").strip()
    if raw.isdigit():
        return int(raw) / 100
    return extract_price(text)


def _entry_to_ad(entry: Any, source: str) -> Ad | None:
    link = unwrap_tracking_url(entry.get("link", ""))
    title = clean_html(entry.get("title", ""))
    if not (link and title):
        return None
    summary = entry.get("summary") or entry.get("description") or ""
    if not summary and entry.get("content"):
        summary = entry["content"][0].get("value", "")
    desc = clean_html(summary)
    full = f"{title} {desc}"
    return Ad(
        title=title[:200],
        url=link,
        source=source,
        price=_entry_price(entry, full),
        shipping=extract_shipping(desc),
        description=desc,
        location=extract_location(desc),
        price_mode=detect_price_mode(full),
    )


def collect(cfg: dict[str, Any]) -> tuple[list[Ad], list[SourceReport]]:
    import feedparser
    import requests

    ads: list[Ad] = []
    reports: list[SourceReport] = []
    for feed in cfg.get("feeds") or []:
        report = SourceReport(name=f"rss:{feed.get('name', 'rss')}")
        reports.append(report)
        try:
            resp = requests.get(feed["url"], timeout=int(cfg.get("timeout", 20)), headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"flux invalide ({parsed.bozo_exception})")
        except (requests.RequestException, ValueError, KeyError) as exc:
            report.ok, report.error = False, str(exc)
            log.warning("Flux « %s » ignoré : %s", report.name, exc)
            continue
        for entry in parsed.entries:
            report.items += 1
            ad = _entry_to_ad(entry, feed.get("name", "rss"))
            if ad:
                ads.append(ad)
                report.ads += 1
        log.info("%s : %d annonce(s)", report.name, report.ads)
    return ads, reports
