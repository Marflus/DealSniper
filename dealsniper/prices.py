"""Lecture des montants, frais de livraison et modes de prix dans un texte libre."""

from __future__ import annotations

import re

from .text import normalize

# Un montant : "1 234,56" / "1.234,56" / "45.00" / "45"
_AMOUNT = r"(\d{1,3}(?:[   .]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_EURO = r"(?:€|eur\b|euros?\b)"

_PRICE_RES = (
    re.compile(_AMOUNT + r"\s*" + _EURO, re.IGNORECASE),        # 45 €
    re.compile(r"(?:€|eur\b)\s*" + _AMOUNT, re.IGNORECASE),      # EUR 45,00
)
_SHIPPING_WORDS = r"(?:livraison|frais de port|frais d'envoi|envoi|shipping|postage)"
_SHIPPING_RES = (
    # "Livraison : 6,50 €" / "Frais de port EUR 7,90"
    re.compile(_SHIPPING_WORDS + r"[^0-9€]{0,25}(?:€\s*)?" + _AMOUNT, re.IGNORECASE),
    # "+ 4,99 € livraison" / "+EUR 4,99 de frais de port"
    re.compile(r"\+\s*(?:eur\s*|€\s*)?" + _AMOUNT + r"\s*(?:€|eur)?\s*(?:de\s+)?" + _SHIPPING_WORDS, re.IGNORECASE),
    # "4,99 € de livraison" (mais pas "40 € Livraison 5 €")
    re.compile(_AMOUNT + r"\s*(?:€|eur)\s*de\s+(?:livraison|frais de port|port)\b", re.IGNORECASE),
)
_FREE_SHIPPING_RE = re.compile(
    r"(livraison|frais de port|port|shipping|envoi)\s*(gratuite?|offerte?|free|inclus)", re.IGNORECASE
)
_FOREIGN_CURRENCY_RE = re.compile(r"(£|us\s?\$|\$\s?\d|\d\s?\$|\busd\b|\bgbp\b|\bchf\b|\bcad\b)", re.IGNORECASE)
_AUCTION_WORDS = ("enchere", "encheres", "offre actuelle", "mise a prix", "derniere offre", "bid", "bids")


def parse_amount(raw: str) -> float | None:
    """Convertit "1 234,56" / "1.234,56" / "45.00" en nombre (None si illisible)."""
    if not raw:
        return None
    s = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in s and "." in s:  # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:  # 45,90
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # 1.234
        s = s.replace(".", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return value if 0 <= value < 1_000_000 else None


def extract_shipping(text: str) -> float | None:
    """Frais de livraison mentionnés (0.0 si gratuite, None si absents)."""
    if not text:
        return None
    if _FREE_SHIPPING_RE.search(text):
        return 0.0
    for rx in _SHIPPING_RES:
        m = rx.search(text)
        if m:
            return parse_amount(m.group(1))
    return None


def extract_price(text: str) -> float | None:
    """Premier montant en euros qui n'est pas un montant de livraison."""
    if not text:
        return None
    shipping_spans = [m.span(1) for rx in _SHIPPING_RES for m in rx.finditer(text)]
    candidates = []
    for rx in _PRICE_RES:
        for m in rx.finditer(text):
            if not any(start <= m.start(1) < end for start, end in shipping_spans):
                candidates.append((m.start(), m.group(1)))
    for _, raw in sorted(candidates):
        value = parse_amount(raw)
        if value is not None:
            return value
    return None


def has_foreign_currency(text: str) -> bool:
    """Vrai si le texte affiche un prix en devise étrangère et aucun prix en euros."""
    return bool(text) and _FOREIGN_CURRENCY_RE.search(text) is not None and extract_price(text) is None


def detect_price_mode(text: str) -> str:
    """"enchere" si le prix est une enchère en cours (donc provisoire), sinon "fixe"."""
    text_n = normalize(text)
    return "enchere" if any(re.search(r"\b" + w + r"\b", text_n) for w in _AUCTION_WORDS) else "fixe"
