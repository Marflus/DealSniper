"""Localisation : extraction « Ville 69003 » et détection des annonces hors de France.

Deal Sniper ne garde que les annonces situées en France (métropole par défaut) :
  * une localisation étrangère explicite (« Provenance : Chine », « Lieu où se
    trouve l'objet : Allemagne », « ships from United Kingdom ») → rejet ;
  * un code postal ou un nom de DOM-TOM → rejet si `exclude_overseas` (frais de
    port et délais rarement compatibles avec la revente).
"""

from __future__ import annotations

import re

from .text import normalize

FOREIGN_COUNTRIES = (
    "allemagne", "germany", "deutschland", "espagne", "spain", "italie", "italy", "belgique", "belgium",
    "pays-bas", "pays bas", "netherlands", "hollande", "suisse", "switzerland", "luxembourg", "portugal",
    "autriche", "austria", "pologne", "poland", "irlande", "ireland", "royaume-uni", "royaume uni",
    "united kingdom", "angleterre", "england", "republique tcheque", "czech republic", "roumanie", "romania",
    "bulgarie", "bulgaria", "grece", "greece", "lituanie", "lettonie", "estonie", "hongrie", "hungary",
    "suede", "sweden", "danemark", "denmark", "norvege", "norway", "finlande", "finland", "turquie", "turkey",
    "chine", "china", "hong kong", "hong-kong", "taiwan", "japon", "japan", "coree", "korea", "singapour",
    "singapore", "inde", "india", "israel", "australie", "australia", "canada", "etats-unis", "etats unis",
    "united states", "usa", "u.s.a", "maroc", "tunisie", "algerie",
)
OVERSEAS_NAMES = (
    "guadeloupe", "martinique", "guyane", "la reunion", "ile de la reunion", "mayotte",
    "saint-pierre-et-miquelon", "polynesie", "nouvelle-caledonie", "nouvelle caledonie", "wallis",
    "saint-barthelemy", "saint-martin 97",
)

# Expressions qui introduisent une localisation (sur texte normalisé, sans accents).
_CUE = (
    r"(?:provenance|en provenance (?:de|d'|du|des)|lieu ou se trouve l'objet|localisation|"
    r"situee?s? (?:en|au|aux|a)|expediee?s? (?:de|depuis|d'|du|des)|envoyee?s? (?:de|depuis|d'|du|des)|"
    r"livraison depuis|depuis (?:la|le|l'|les)|item location|located in|ships from|location)"
)
_FOREIGN_RE = re.compile(
    _CUE + r"\s*[:\-]?\s*(?:la |le |l'|les |the )?("
    + "|".join(re.escape(c) for c in sorted(FOREIGN_COUNTRIES, key=len, reverse=True))
    + r")(?![a-z])"
)
_OVERSEAS_POSTCODE_RE = re.compile(r"(?<![\d.,])(97[1-6]\d{2}|98[6-9]\d{2})(?![\d.,]|\s*(?:€|eur))")
_CITY_POSTCODE_RE = re.compile(r"([A-ZÀ-Ÿ][A-Za-zÀ-ÿ'\-]+(?: [A-ZÀ-Ÿa-zà-ÿ'\-]+){0,3}) (\d{5})(?![\d.,]|\s*(?:€|eur))")


def extract_location(text: str) -> str | None:
    """« Lyon 69003 » si le texte contient une ville suivie d'un code postal."""
    m = _CITY_POSTCODE_RE.search(text or "")
    return f"{m.group(1)} {m.group(2)}" if m else None


def foreign_country(text: str) -> str | None:
    """Pays étranger explicitement indiqué comme localisation, sinon None."""
    m = _FOREIGN_RE.search(normalize(text))
    return m.group(1) if m else None


def is_overseas(text: str) -> bool:
    """Vrai si le texte situe l'annonce dans un DOM-TOM (code postal ou nom)."""
    if not text:
        return False
    if _OVERSEAS_POSTCODE_RE.search(text):
        return True
    text_n = normalize(text)
    return any(name in text_n for name in OVERSEAS_NAMES)
