"""Source « emails d'alerte » : lecture d'une boîte dédiée par IMAP.

Les sites (leboncoin, eBay, Vinted, Agorastore…) envoient des alertes email ;
on lit les emails récents de la boîte dédiée et on en extrait les annonces
(voir `email_parser.py`). Aucun navigateur, aucun scraping de site.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Any

from ..config import env
from ..models import Ad
from ..urls import RedirectResolver
from . import SourceReport
from .email_parser import ParsedEmail, parse_email

log = logging.getLogger(__name__)


def _patterns(cfg: dict[str, Any]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in cfg.get("ad_url_patterns", [])]


def iter_messages(cfg: dict[str, Any], report: SourceReport) -> Iterator[Message]:
    """Emails des `lookback_days` derniers jours, du plus récent au plus ancien."""
    server, user, password = env("IMAP_SERVER"), env("IMAP_EMAIL"), env("IMAP_PASSWORD")
    if not (server and user and password):
        report.ok, report.error = False, "IMAP non configuré (IMAP_SERVER / IMAP_EMAIL / IMAP_PASSWORD)"
        log.warning(report.error)
        return
    conn: imaplib.IMAP4_SSL | None = None
    try:
        conn = imaplib.IMAP4_SSL(server, int(env("IMAP_PORT", "993")), timeout=30)
        conn.login(user, password)
        readonly = not cfg.get("mark_as_read", False)
        status, _ = conn.select(f'"{cfg.get("folder", "INBOX")}"', readonly=readonly)
        if status != "OK":
            raise imaplib.IMAP4.error(f"dossier « {cfg.get('folder')} » introuvable")
        since = (datetime.now(timezone.utc) - timedelta(days=int(cfg.get("lookback_days", 3)))).strftime("%d-%b-%Y")
        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            raise imaplib.IMAP4.error(f"recherche IMAP refusée ({status})")
        ids = data[0].split()
        if cfg.get("max_emails"):
            ids = ids[-int(cfg["max_emails"]):]
        log.info("IMAP : %d email(s) depuis le %s", len(ids), since)
        for msg_id in reversed(ids):
            try:
                status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[])")  # PEEK : ne marque pas comme lu
                if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                report.items += 1
                yield email.message_from_bytes(msg_data[0][1])
                if not readonly:
                    conn.store(msg_id, "+FLAGS", "\\Seen")
            except (imaplib.IMAP4.error, OSError, ValueError) as exc:
                log.warning("Email %s ignoré : %s", msg_id.decode(errors="replace"), exc)
    except (imaplib.IMAP4.error, OSError) as exc:
        report.ok, report.error = False, f"connexion IMAP impossible : {exc}"
        log.error(report.error)
    finally:
        if conn is not None:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def _parse_all(cfg: dict[str, Any], report: SourceReport) -> Iterator[ParsedEmail]:
    patterns = _patterns(cfg)
    resolver = RedirectResolver(patterns) if cfg.get("resolve_tracking_links", True) else None
    for msg in iter_messages(cfg, report):
        try:
            yield parse_email(msg, cfg.get("senders") or {}, patterns, resolver)
        except Exception as exc:  # un email mal formé ne doit rien bloquer
            log.warning("Email illisible ignoré (%s: %s)", type(exc).__name__, exc)


def collect(cfg: dict[str, Any]) -> tuple[list[Ad], SourceReport]:
    report = SourceReport(name="emails")
    ads: list[Ad] = []
    for parsed in _parse_all(cfg, report):
        if parsed.source:
            log.debug("« %s » (%s) → %d annonce(s)", parsed.subject[:60], parsed.source, len(parsed.ads))
        ads += parsed.ads
    report.ads = len(ads)
    return ads, report


def inspect(cfg: dict[str, Any]) -> int:
    """Diagnostic : affiche, pour chaque email, la source reconnue et les annonces extraites.

    N'affiche ni le contenu des emails ni d'adresse complète (seulement le domaine
    de l'expéditeur), pour pouvoir partager la sortie sans risque.
    """
    report = SourceReport(name="emails")
    first_urls: list[str] = []
    for parsed in _parse_all(cfg, report):
        first_urls += [ad.url for ad in parsed.ads if ad.url_is_stable and ad.source in ("leboncoin", "vinted")]
        domain = parsed.sender.split("@")[-1] if parsed.sender else "?"
        flag = " (transféré)" if parsed.forwarded else ""
        print(f"\n✉️  [{domain}] {parsed.subject[:90]}")
        print(f"    source : {parsed.source or 'ignoré (expéditeur non suivi)'}{flag}")
        if parsed.source is None:
            continue
        top = ", ".join(f"{host} ×{n}" for host, n in parsed.link_hosts.most_common(6))
        print(f"    liens  : {sum(parsed.link_hosts.values())} — {top or 'aucun'}")
        print(f"    annonces extraites : {len(parsed.ads)}")
        for ad in parsed.ads[:15]:
            price = f"{ad.price:.0f} €" if ad.price is not None else "prix ?"
            ship = f" + {ad.shipping:.2f} € port" if ad.shipping else ""
            where = f" | {ad.location}" if ad.location else ""
            link = "ok" if ad.url_is_stable else "lien de suivi"
            print(f"      • {price}{ship} | {ad.title[:70]}{where} | {ad.url[:70]} ({link})")
    if first_urls and cfg.get("preview_pages", True):
        from ..enrich import preview

        print("\nTest de lecture des pages d'annonce (description / vendeur) :")
        for url in first_urls[:3]:
            print(f"  {url[:70]}\n    → {preview(url)}")
    print(f"\nBilan : {report.items} email(s) lus" + ("" if report.ok else f" — ERREUR : {report.error}"))
    return 0 if report.ok else 1
