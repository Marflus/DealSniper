"""
collector.py — Récupération des nouvelles annonces, sans navigateur.

Deux sources, toutes deux « légères » et peu sujettes aux blocages anti-bot :

1. **IMAP** : une boîte mail dédiée reçoit les alertes email (leboncoin,
   Agorastore, eBay, Vinted…). On lit les emails récents, on repère les liens
   d'annonces dans le HTML, et on reconstitue titre / prix / livraison à partir
   du bloc de texte qui entoure chaque lien.
2. **RSS** : flux de recherches sauvegardées (eBay, RSS-Bridge, rss.app…).

Chaque annonce est normalisée en `core.Ad`. Les annonces déjà vues
(`seen_ads.json`) sont écartées ici ; c'est `main.py` qui les marque comme
vues une fois traitées.

Principe de robustesse : une erreur sur UN email ou UN flux est journalisée,
puis on passe au suivant. La collecte ne lève jamais d'exception.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import socket
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from typing import Any, Iterable

from bs4 import BeautifulSoup

from core import (
    Ad,
    clean_html,
    env,
    extract_price,
    extract_shipping,
    normalize,
    unwrap_tracking_url,
)

log = logging.getLogger("dealsniper.collector")

USER_AGENT = "Mozilla/5.0 (compatible; DealSniper/1.0; +rss-reader)"
_MAX_BLOCK_CHARS = 900  # au-delà, le « bloc » englobe probablement plusieurs annonces


# =========================================================================== #
#  IMAP
# =========================================================================== #
def _decode(value: str | None) -> str:
    """Décode un en-tête MIME (=?utf-8?q?...?=) sans jamais lever d'exception."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _message_bodies(msg: Message) -> tuple[str, str]:
    """Renvoie (html, texte) d'un email, en tolérant les encodages exotiques."""
    html_parts, text_parts = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                content = payload.decode(charset, errors="replace")
            except LookupError:  # charset inconnu
                content = payload.decode("utf-8", errors="replace")
        except Exception as exc:
            log.debug("Partie MIME illisible ignorée : %s", exc)
            continue
        ctype = part.get_content_type()
        if ctype == "text/html":
            html_parts.append(content)
        elif ctype == "text/plain":
            text_parts.append(content)
    return "\n".join(html_parts), "\n".join(text_parts)


def _guess_source(sender: str, senders: dict[str, str]) -> str | None:
    """Associe l'expéditeur à un nom de source. None = expéditeur non suivi."""
    sender_n = normalize(sender)
    for source_name, needle in (senders or {}).items():
        if normalize(needle) in sender_n:
            return source_name
    return None if senders else "email"


def _block_text_around(anchor) -> str:
    """Remonte les parents d'un lien jusqu'à trouver un bloc contenant un prix."""
    node = anchor
    best = clean_html(anchor.get_text(" "))
    for _ in range(7):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]"):
            break
        text = clean_html(node.get_text(" "))
        if len(text) > _MAX_BLOCK_CHARS:
            break
        best = text
        if extract_price(text) is not None and len(text) > 25:
            break
    return best


def extract_ads_from_email_html(
    html: str, source: str, url_patterns: Iterable[re.Pattern]
) -> list[Ad]:
    """Extrait 0..N annonces d'un email d'alerte HTML (heuristique générique).

    Les emails d'alerte listent généralement plusieurs annonces, chacune avec
    une image + un titre cliquables pointant vers la même URL. On regroupe donc
    les liens par URL et on garde le meilleur titre.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        log.warning("HTML d'email illisible : %s", exc)
        return []

    found: dict[str, dict[str, Any]] = {}
    for a in soup.find_all("a", href=True):
        href = unwrap_tracking_url(a["href"].strip())
        if not href.startswith("http"):
            continue
        if not any(p.search(href) for p in url_patterns):
            continue
        anchor_text = clean_html(a.get_text(" "))
        block = _block_text_around(a)
        entry = found.setdefault(href, {"titles": [], "block": ""})
        if anchor_text:
            entry["titles"].append(anchor_text)
        if len(block) > len(entry["block"]):
            entry["block"] = block

    ads: list[Ad] = []
    for url, info in found.items():
        # Le titre = le texte de lien le plus « parlant » (ni vide, ni « Voir l'annonce »)
        titles = [
            t for t in info["titles"]
            if len(t) >= 6 and not re.fullmatch(r"(voir|see|view|consulter).{0,25}", normalize(t))
        ]
        title = max(titles, key=len) if titles else info["block"][:90]
        title = re.sub(r"\s*\d[\d\s.,]*\s*€.*$", "", title).strip() or title  # retire un prix collé au titre
        block = info["block"]
        description = block.replace(title, "", 1).strip(" -|·")
        ads.append(
            Ad(
                title=title[:200],
                url=url,
                source=source,
                price=extract_price(block),
                shipping=extract_shipping(block),
                description=description,
            )
        )
    return ads


def _first_ad_link(html: str, text: str, patterns: list[re.Pattern]) -> str:
    """Premier lien du mail, en préférant ceux qui ressemblent à une annonce."""
    links = re.findall(r'href=["\'](https?://[^"\']+)', html or "") + re.findall(r"https?://[^\s<>\"']+", text or "")
    links = [unwrap_tracking_url(l) for l in links]
    for link in links:
        if any(p.search(link) for p in patterns):
            return link
    return links[0] if links else ""


def collect_imap(cfg: dict[str, Any]) -> list[Ad]:
    """Lit les emails d'alerte récents de la boîte dédiée."""
    server, user, password = env("IMAP_SERVER"), env("IMAP_EMAIL"), env("IMAP_PASSWORD")
    if not (server and user and password):
        log.info("IMAP non configuré (IMAP_SERVER / IMAP_EMAIL / IMAP_PASSWORD) — ignoré.")
        return []

    port = int(env("IMAP_PORT", "993"))
    folder = cfg.get("folder", "INBOX")
    lookback = int(cfg.get("lookback_days", 3))
    max_emails = int(cfg.get("max_emails", 100))
    senders = cfg.get("senders") or {}
    patterns = [re.compile(p, re.IGNORECASE) for p in cfg.get("ad_url_patterns", [])]
    mark_as_read = bool(cfg.get("mark_as_read", False))

    ads: list[Ad] = []
    conn: imaplib.IMAP4_SSL | None = None
    try:
        conn = imaplib.IMAP4_SSL(server, port, timeout=30)
        conn.login(user, password)
        status, _ = conn.select(f'"{folder}"', readonly=not mark_as_read)
        if status != "OK":
            log.error("Dossier IMAP « %s » introuvable.", folder)
            return []

        since = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%d-%b-%Y")
        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            log.error("Recherche IMAP échouée : %s", status)
            return []
        ids = data[0].split()[-max_emails:]  # les plus récents
        log.info("IMAP : %d email(s) depuis le %s", len(ids), since)

        for msg_id in reversed(ids):
            try:
                # BODY.PEEK ne marque pas l'email comme lu
                status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                sender = _decode(msg.get("From"))
                source = _guess_source(parseaddr(sender)[1] + " " + sender, senders)
                if source is None:
                    continue  # pas un email d'alerte suivi
                subject = _decode(msg.get("Subject"))
                html, text = _message_bodies(msg)

                found = extract_ads_from_email_html(html, source, patterns) if html else []
                if not found:
                    # Repli : l'email entier = une annonce (alerte « une annonce par mail »)
                    body = clean_html(html) if html else clean_html(text)
                    url = _first_ad_link(html, text, patterns)
                    if body and url:
                        found = [Ad(
                            title=subject[:200] or body[:90],
                            url=url,
                            source=source,
                            price=extract_price(subject) or extract_price(body),
                            shipping=extract_shipping(body),
                            description=body,
                        )]
                log.debug("Email « %s » → %d annonce(s)", subject[:60], len(found))
                ads.extend(found)
                if mark_as_read:
                    conn.store(msg_id, "+FLAGS", "\\Seen")
            except Exception as exc:  # email mal formé : on passe au suivant
                log.warning("Email %s ignoré (%s: %s)", msg_id, type(exc).__name__, exc)
    except (imaplib.IMAP4.error, socket.error, OSError) as exc:
        log.error("Connexion IMAP impossible : %s", exc)
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
    return ads


# =========================================================================== #
#  RSS
# =========================================================================== #
def _entry_price(entry: Any, text: str) -> float | None:
    # Certains flux eBay exposent <ebay:CurrentPrice> en centimes
    for key in ("ebay_currentprice", "currentprice", "price", "g_price"):
        raw = entry.get(key)
        if raw:
            raw = str(raw).strip()
            if raw.isdigit() and key.endswith("currentprice"):
                return int(raw) / 100
            value = extract_price(raw + " €") if re.search(r"\d", raw) else None
            if value is not None:
                return value
    return extract_price(text)


def collect_rss(cfg: dict[str, Any]) -> list[Ad]:
    """Lit chaque flux RSS/Atom configuré."""
    import feedparser
    import requests

    feeds = cfg.get("feeds") or []
    timeout = int(cfg.get("timeout", 20))
    ads: list[Ad] = []
    for feed in feeds:
        name = feed.get("name") or "rss"
        url = feed.get("url")
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"flux invalide ({parsed.bozo_exception})")
        except Exception as exc:
            log.warning("Flux RSS « %s » ignoré : %s", name, exc)
            continue

        count = 0
        for entry in parsed.entries:
            try:
                link = unwrap_tracking_url(entry.get("link", ""))
                title = clean_html(entry.get("title", ""))
                if not (link and title):
                    continue
                summary = entry.get("summary") or entry.get("description") or ""
                if not summary and entry.get("content"):
                    summary = entry["content"][0].get("value", "")
                desc = clean_html(summary)
                ads.append(
                    Ad(
                        title=title[:200],
                        url=link,
                        source=name,
                        price=_entry_price(entry, f"{title} {desc}"),
                        shipping=extract_shipping(desc),
                        description=desc,
                    )
                )
                count += 1
            except Exception as exc:
                log.debug("Entrée RSS ignorée (%s)", exc)
        log.info("RSS « %s » : %d annonce(s)", name, count)
    return ads


# =========================================================================== #
#  Point d'entrée
# =========================================================================== #
def collect(config: dict[str, Any], seen) -> list[Ad]:
    """Collecte toutes les sources et retire les doublons / annonces déjà vues."""
    sources = config.get("sources", {})
    raw: list[Ad] = []

    imap_cfg = sources.get("imap") or {}
    if imap_cfg.get("enabled", True):
        raw += collect_imap(imap_cfg)

    rss_cfg = sources.get("rss") or {}
    if rss_cfg.get("enabled", True):
        raw += collect_rss(rss_cfg)

    unique: dict[str, Ad] = {}
    for ad in raw:
        uid = ad.uid
        if uid in seen or uid in unique:
            continue
        unique[uid] = ad
    log.info("Collecte : %d annonce(s) brute(s), %d nouvelle(s)", len(raw), len(unique))
    return list(unique.values())
