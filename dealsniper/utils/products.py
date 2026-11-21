"""Ce que l'annonce vend vraiment : la console, ou un jeu / accessoire qui la cite.

« The legend of Zelda Echoes of Wisdom NINTENDO Switch » contient « Switch » mais vend un JEU (35 €),
pas une console (250 €). Sans cette distinction, la cote et l'IA prennent le prix de la console.
Règle, seulement quand le titre cite une plateforme de jeu : le titre d'une console commence par elle
(« Nintendo Switch OLED », « PS5 digital + manette ») ou dit « console » ; quand la plateforme n'arrive
qu'après le nom d'un produit, ou que le titre commence par « jeu », « manette »…, c'est un jeu ou un
accessoire.
"""

from __future__ import annotations

import re

from .text import _keyword_pattern, first_keyword, normalize

# Plateformes de jeu : une console se nomme en tête de titre ; un jeu ou un accessoire les cite après.
PLATFORMS = ("nintendo switch", "switch", "ps5", "ps4", "ps3", "ps2", "playstation", "xbox", "wii u", "wii",
             "3ds", "2ds", "nintendo ds", "game boy", "gameboy", "steam deck", "game cube", "gamecube")
# Premier mot d'un titre de jeu ou d'accessoire de console (« Manette PS5 », « Jeu Switch Mario »).
GAME_FIRST = {"jeu", "jeux", "game", "games", "manette", "manettes", "cartouche", "cartouches", "amiibo", "volant",
              "steelbook", "boitier", "joy-con", "joycon", "joy-cons", "controller", "chargeur", "coque", "housse",
              "etui", "station", "dock", "cable", "carte", "support", "batterie", "sacoche", "skin", "protection"}
# Mots qui peuvent précéder la plateforme dans le titre d'une console (« Lot Nintendo Switch »…).
_LEAD_WORDS = {"nintendo", "sony", "microsoft", "valve", "new", "nouvelle", "la", "le", "une", "un", "lot",
               "pack", "console", "vends", "vend", "superbe", "tres", "bon", "etat", "neuve", "neuf"}
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")


def game_or_accessory(title: str) -> str | None:
    """« jeu ou accessoire » si l'annonce vend un jeu / accessoire de console et non la console, sinon None."""
    text = normalize(title)
    starts = [m.start() for p in PLATFORMS if (pattern := _keyword_pattern(p)) and (m := pattern.search(text))]
    if not starts:
        return None  # pas une plateforme de jeu : rien à dire
    if min(starts) == 0:
        return None  # le titre commence par la plateforme : c'est la console (« Game Boy Color », « PS5 »)
    words = _WORD_RE.findall(text)
    if words and words[0] in GAME_FIRST:
        return "jeu ou accessoire"
    if first_keyword(text, ["console"]):
        return None
    before = [w for w in _WORD_RE.findall(text[:min(starts)]) if w not in _LEAD_WORDS]
    return "jeu ou accessoire" if len(before) >= 2 else None  # « Zelda … Switch », « FIFA 24 PS4 »
