"""Outils texte : nettoyage HTML, normalisation sans accents, recherche de mots-clés."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

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


class _CombiningMarks(dict):
    """Table pour str.translate : supprime les accents (signes combinants), garde le reste.
    Remplie au fil de l'eau (un calcul par caractère distinct) : la traduction se fait ensuite en C."""

    def __missing__(self, code: int) -> int | None:
        value = None if unicodedata.combining(chr(code)) else code
        self[code] = value
        return value


_STRIP_ACCENTS = _CombiningMarks()


def normalize(text: str | None) -> str:
    """Minuscules, sans accents, espaces compressés : "Carte Mère HS" → "carte mere hs"."""
    if not text:
        return ""
    if not text.isascii():  # texte ASCII : ni accents ni formes à décomposer
        text = unicodedata.normalize("NFKD", text).translate(_STRIP_ACCENTS)
    text = text.replace("’", "'").replace(" ", " ")
    return _WS_RE.sub(" ", text.lower()).strip()


def contains_keyword(text_normalized: str, keyword: str) -> bool:
    """Recherche un mot-clé en mot entier, en tolérant les accords français.

    `text_normalized` doit déjà être passé par `normalize`.
    "oxyde" trouve "oxydee" / "oxydes" ; "hs" ne trouve pas "hstore".
    """
    pattern = _keyword_pattern(keyword)
    return pattern is not None and pattern.search(text_normalized) is not None


_BEFORE, _AFTER = r"(?<![a-z0-9])", r"(?![a-z0-9])"  # mot entier


@lru_cache(maxsize=8192)
def _keyword_core(keyword: str) -> str | None:
    """Cœur de l'expression d'un mot-clé (sans les bornes de mot) : accords tolérés après une lettre."""
    kw = normalize(keyword)
    if not kw:
        return None
    return re.escape(kw) + (r"(?:e|s|es|x)?" if kw[-1].isalpha() else "")


@lru_cache(maxsize=8192)
def _keyword_pattern(keyword: str) -> re.Pattern[str] | None:
    """Expression compilée d'un mot-clé, gardée en mémoire : les mêmes mots-clés sont testés sur chaque
    annonce (normaliser et compiler à chaque fois coûtait l'essentiel du temps du filtre)."""
    core = _keyword_core(keyword)
    return re.compile(_BEFORE + core + _AFTER) if core else None


def first_keyword(text_normalized: str, keywords: list[str] | tuple[str, ...] | None) -> str | None:
    """Premier mot-clé de la liste présent dans le texte, ou None.

    Une seule recherche combinée d'abord (le cas courant : aucun mot présent) ; si elle trouve quelque
    chose, on parcourt la liste dans l'ordre pour renvoyer le PREMIER mot-clé de la liste qui correspond.
    """
    if not keywords:
        return None
    if len(keywords) == 1:
        return keywords[0] if contains_keyword(text_normalized, keywords[0]) else None
    combined = _combined_pattern(tuple(keywords))
    if combined is None or combined.search(text_normalized) is None:
        return None
    for kw in keywords:
        if contains_keyword(text_normalized, kw):
            return kw
    return None


@lru_cache(maxsize=512)
def _combined_pattern(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    """Tous les mots-clés d'une liste en une seule expression (mêmes règles que `_keyword_pattern`)."""
    cores = [c for c in map(_keyword_core, keywords) if c]
    return re.compile(_BEFORE + "(?:" + "|".join(f"(?:{c})" for c in cores) + ")" + _AFTER) if cores else None


def truncate(text: str, max_chars: int) -> str:
    """Tronque sur une frontière de mot et ajoute « … »."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"
