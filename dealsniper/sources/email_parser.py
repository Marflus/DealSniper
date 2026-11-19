"""Lecture d'un email d'alerte (leboncoin, eBay, Vinted…) → liste d'annonces.

Fonctions pures, sans réseau (le suivi des liens de redirection est injecté),
donc testables hors-ligne.

Heuristique : un email d'alerte liste plusieurs annonces, chacune avec une image
et un titre cliquables. Pour chaque lien, on remonte dans le HTML jusqu'au plus
petit bloc qui contient un prix : ce bloc donne titre, prix, livraison et ville.
  * lien « fort » : l'URL ressemble à une annonce (`ad_url_patterns`) ;
  * lien « faible » : lien de suivi opaque, gardé seulement si son bloc contient
    un prix ; on tente alors de suivre la redirection pour retrouver l'URL réelle.
Les emails transférés depuis une autre boîte sont reconnus via leur contenu.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..location import extract_location
from ..models import Ad
from ..prices import detect_price_mode, extract_price, extract_shipping
from ..text import clean_html, normalize
from ..urls import matches_any, native_id, unwrap_tracking_url

log = logging.getLogger(__name__)

Resolver = Callable[[str], "str | None"]

MAX_BLOCK_CHARS = 900  # au-delà, le bloc englobe probablement plusieurs annonces
_FOOTER_WORDS = (
    "desinscri", "desabonn", "unsubscribe", "preference", "parametre", "confidentialite", "privacy",
    "aide", "help", "conditions", "cgu", "mentions legales", "app store", "google play", "telecharger",
    "voir toutes", "voir tous", "voir plus d'annonces", "modifier", "gerer", "contact", "facebook",
    "instagram", "twitter", "tiktok", "youtube", "linkedin", "mon compte", "se connecter",
)
_GENERIC_TITLES = re.compile(r"^(voir|see|view|consulter|decouvrir|acceder)\b.{0,30}$")
_FORWARD_SUBJECT = re.compile(r"^\s*(tr|fw|fwd|wg|rv)\s*:", re.IGNORECASE)


@dataclass
class ParsedEmail:
    sender: str
    subject: str
    source: str | None
    forwarded: bool
    ads: list[Ad] = field(default_factory=list)
    link_hosts: Counter = field(default_factory=Counter)


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # en-tête mal encodé
        return value


def message_bodies(msg: Message) -> tuple[str, str]:
    """(html, texte) d'un email, en tolérant les encodages exotiques."""
    html_parts, text_parts = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            content = payload.decode(charset, errors="replace")
        except LookupError:
            content = payload.decode("utf-8", errors="replace")
        if part.get_content_type() == "text/html":
            html_parts.append(content)
        elif part.get_content_type() == "text/plain":
            text_parts.append(content)
    return "\n".join(html_parts), "\n".join(text_parts)


def detect_source(sender: str, subject: str, body_text: str, senders: dict[str, str]) -> str | None:
    """Nom de la source ("leboncoin"…) ou None si l'email n'est pas une alerte suivie.

    Regarde l'expéditeur, puis — pour les emails transférés ou redirigés depuis
    une autre boîte — le sujet et le début du contenu.
    """
    if not senders:
        return "email"
    sender_n = normalize(sender)
    for name, needle in senders.items():
        if normalize(needle) in sender_n:
            return name
    head_n = normalize(f"{subject} {body_text[:4000]}")
    for name, needle in senders.items():
        if normalize(needle) in head_n:
            return name
    return None


def _block_text_around(anchor) -> str:
    """Plus petit bloc parent du lien contenant un prix (ou le plus grand sous la limite)."""
    best = clean_html(anchor.get_text(" "))
    node = anchor
    for _ in range(8):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]"):
            break
        text = clean_html(node.get_text(" "))
        if len(text) > MAX_BLOCK_CHARS:
            break
        best = text
        if len(text) > 20 and extract_price(text) is not None:
            break
    return best


def _pick_title(titles: list[str], block: str) -> str:
    good = [t for t in titles if len(t) >= 6 and not _GENERIC_TITLES.match(normalize(t))]
    title = max(good, key=len) if good else block[:90]
    # retire un prix collé en fin de titre : "ThinkPad T480 95 €"
    stripped = re.sub(r"\s*\d[\d\s.,]*\s*(€|eur\b).*$", "", title, flags=re.IGNORECASE).strip()
    return (stripped or title)[:200]


def extract_ads(
    html: str,
    source: str,
    patterns: Iterable[re.Pattern],
    resolver: Resolver | None = None,
    link_hosts: Counter | None = None,
) -> list[Ad]:
    """Annonces contenues dans le HTML d'un email d'alerte."""
    patterns = list(patterns)
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        log.warning("HTML d'email illisible : %s", exc)
        return []

    groups: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = unwrap_tracking_url(a["href"].strip())
        if not href.startswith("http"):
            continue
        parsed = urlparse(href)
        if link_hosts is not None:
            first_segment = parsed.path.strip("/").split("/")[0][:20]
            link_hosts[f"{parsed.netloc}/{first_segment}"] += 1

        anchor_text = clean_html(a.get_text(" "))
        if any(word in normalize(anchor_text) for word in _FOOTER_WORDS):
            continue
        strong = matches_any(href, patterns)
        block = _block_text_around(a)
        if not strong:
            # Lien de suivi opaque : on ne le garde que s'il entoure un prix.
            if extract_price(block) is None or not (len(anchor_text) >= 6 or a.find("img")):
                continue
        key = (native_id(href) or href) if strong else "blk:" + normalize(block)[:160]
        group = groups.setdefault(key, {"url": href, "strong": strong, "titles": [], "block": ""})
        if anchor_text:
            group["titles"].append(anchor_text)
        if len(block) > len(group["block"]):
            group["block"] = block

    # Un lien opaque dont le bloc englobe celui d'une autre annonce est un lien de
    # structure (logo, en-tête…) : son « prix » appartient en fait à l'autre annonce.
    blocks = [g["block"] for g in groups.values()]
    groups = {
        key: g for key, g in groups.items()
        if g["strong"] or not any(other != g["block"] and other in g["block"] for other in blocks)
    }

    ads: list[Ad] = []
    for group in groups.values():
        url, stable = group["url"], group["strong"]
        if not stable and resolver is not None:
            resolved = resolver(url)
            if resolved:
                url, stable = resolved, True
        block = group["block"]
        title = _pick_title(group["titles"], block)
        ads.append(
            Ad(
                title=title,
                url=url,
                source=source,
                price=extract_price(block),
                shipping=extract_shipping(block),
                description=block.replace(title, "", 1).strip(" -|·"),
                location=extract_location(block),
                price_mode=detect_price_mode(block),
                url_is_stable=stable,
            )
        )
    return ads


def parse_email(
    msg: Message,
    senders: dict[str, str],
    patterns: Iterable[re.Pattern],
    resolver: Resolver | None = None,
) -> ParsedEmail:
    """Analyse complète d'un email : source, transfert éventuel, annonces."""
    sender = decode_header_value(msg.get("From"))
    subject = decode_header_value(msg.get("Subject"))
    html, text = message_bodies(msg)
    body_text = clean_html(html) if html else clean_html(text)
    address = parseaddr(sender)[1]
    source = detect_source(f"{address} {sender}", subject, body_text, senders)
    forwarded = bool(_FORWARD_SUBJECT.match(subject)) or (source is not None and not any(
        normalize(n) in normalize(sender) for n in senders.values()
    ))
    result = ParsedEmail(sender=address, subject=subject, source=source, forwarded=forwarded)
    if source is None:
        return result
    if html:
        result.ads = extract_ads(html, source, patterns, resolver, result.link_hosts)
    elif text:
        # Email en texte brut : on fabrique un HTML minimal à partir des URL.
        pseudo = "".join(f'<p><a href="{u}">{u}</a></p>' for u in re.findall(r"https?://[^\s<>\"']+", text))
        result.ads = [ad for ad in extract_ads(pseudo, source, patterns, resolver, result.link_hosts)
                      if matches_any(ad.url, list(patterns))]
    return result
