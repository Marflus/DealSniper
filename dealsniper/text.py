"""Outils texte : nettoyage HTML, normalisation sans accents, recherche de mots-clés."""

from __future__ import annotations

import re
import unicodedata

from bs4 import BeautifulSoup

_WS_RE = re.compile(r"\s+")


def clean_html(raw: str | None) -> str:
    """Retire balises, scripts et styles ; compresse les espaces."""
    if not raw:
        return ""
    if "<" not in raw:
        return _WS_RE.sub(" ", raw).strip()
    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "head", "title", "meta"]):
            tag.decompose()
        text = soup.get_text(" ")
    except Exception:  # HTML très abîmé : on retire grossièrement les balises
        text = re.sub(r"<[^>]+>", " ", raw)
    return _WS_RE.sub(" ", text).strip()


def normalize(text: str | None) -> str:
    """Minuscules, sans accents, espaces compressés : "Carte Mère HS" → "carte mere hs"."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("’", "'").replace(" ", " ")
    return _WS_RE.sub(" ", text.lower()).strip()


def contains_keyword(text_normalized: str, keyword: str) -> bool:
    """Recherche un mot-clé en mot entier, en tolérant les accords français.

    `text_normalized` doit déjà être passé par `normalize`.
    "oxyde" trouve "oxydee" / "oxydes" ; "hs" ne trouve pas "hstore".
    """
    kw = normalize(keyword)
    if not kw:
        return False
    suffix = r"(?:e|s|es|x)?" if kw[-1].isalpha() else ""
    pattern = r"(?<![a-z0-9])" + re.escape(kw) + suffix + r"(?![a-z0-9])"
    return re.search(pattern, text_normalized) is not None


def first_keyword(text_normalized: str, keywords: list[str] | None) -> str | None:
    """Premier mot-clé de la liste présent dans le texte, ou None."""
    for kw in keywords or []:
        if contains_keyword(text_normalized, kw):
            return kw
    return None


def truncate(text: str, max_chars: int) -> str:
    """Tronque sur une frontière de mot et ajoute « … »."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"
