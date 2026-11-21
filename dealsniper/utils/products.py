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
               "pack", "console", "vends", "vend", "superbe", "tres", "bon", "etat", "neuve", "neuf",
               "bonjour", "salut", "je", "j'ai", "ma", "mon", "mes", "ta", "a", "vendre", "donne", "cede", "ancienne"}
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


# Pas l'appareil lui-même : accessoire vendu seul, lot, boîte…
NOT_THE_DEVICE = ("seul", "seule", "lot de", "boite vide", "accessoire", "accessoires", "pieces detachees",
                  "compatible", "compatibles")  # « Hub USB-C … Compatible MacBook Air M1 »
# Titre qui commence par une pièce ou un accessoire : « Chargeur ThinkPad T480 », « Batterie Dyson V10 ».
ACCESSORY_FIRST = {"chargeur", "batterie", "clavier", "coque", "housse", "etui", "dalle", "nappe", "carte",
                   "alimentation", "cable", "adaptateur", "station", "dock", "telecommande", "pied", "support",
                   "brosse", "filtre", "tete", "ventilateur", "charniere", "disque", "ssd", "ram", "memoire",
                   "sacoche", "sac", "piece", "pieces", "accessoire", "kit", "bloc", "palmrest", "capot",
                   "chassis", "dissipateur", "ventilo", "plasturgie", "docking",
                   # boîtes, housses, hubs… y compris dans les annonces étrangères de Vinted (« Custodia », « Caja »)
                   "boite", "caja", "scatola", "verpakking", "custodia", "funda", "case", "cover", "sleeve",
                   "pochette", "hub", "film", "protection", "cargador", "bateria", "topcase", "skin"}
# Signal pour l'IA : seulement les cas nets (« lot de » peut être un lot d'appareils).
_PART_PHRASES = ("seul", "seule", "pieces detachees", "piece detachee")


def part_or_accessory(title: str) -> str | None:
    """« pièce ou accessoire » quand le titre vend une pièce détachée ou un accessoire seul
    (« Ventilateur ThinkPad T480 », « Clavier … T480 », « Chargeur seul »), et non l'appareil : sans ce
    signal, l'IA estimait un ventilateur à 6 € au prix d'un ThinkPad réparé (135 €)."""
    text = normalize(title)
    words = _WORD_RE.findall(text)[:2]
    alone = first_keyword(text, list(_PART_PHRASES)) and not first_keyword(text, ["console"])  # « console seule »
    if ACCESSORY_FIRST.intersection(words) or alone:
        return "pièce ou accessoire"
    return None


# « taille » : Vinted ajoute « Taille : » à presque tout, ce mot seul ne dit rien de la recherche.
_QUERY_STOPWORDS = {"de", "du", "des", "la", "le", "les", "et", "avec", "sans", "pour", "en", "a", "au", "aux",
                    "un", "une", "d", "l", "ou", "sur", "taille", "pointure", "t"}


def query_words(query: str) -> list[str]:
    """Mots significatifs d'une recherche (« chaussures blanches taille 43 » → chaussure, blanche, taille, 43)."""
    words = [w.strip("'-") for w in _WORD_RE.findall(normalize(query))]
    return [_stem(w) for w in words if w and w not in _QUERY_STOPWORDS]


def _stem(word: str) -> str:
    """Pluriel retiré, pour comparer « blanches » et « blanche », « chaussures » et « chaussure »."""
    return word[:-1] if len(word) > 3 and word.endswith(("s", "x")) else word


def matches_query(text: str, words: list[str]) -> bool:
    """Vrai si le texte contient au moins un mot de la recherche (début de mot : « t480 » trouve « t480s »)."""
    if not words:
        return True
    tokens = [w.strip("'-") for w in _WORD_RE.findall(normalize(text))]
    return any(t.startswith(w) for w in words for t in tokens if t)
