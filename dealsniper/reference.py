"""Prix de référence d'occasion (France, appareil en état de marche) pour l'électronique surveillée.

Pourquoi : sans cote du marché (modèle encore peu vu), la notation rapide n'a que le titre et ses estimations
varient d'un lot à l'autre (même Steam Deck OLED 512 Go estimé 300 € puis 420 €). Le repère est donné à l'IA
et, sans cote, l'estimation est ramenée dans la fourchette (analyzer.make_realistic).

Fourchettes indicatives (fin 2026), volontairement prudentes : le bas = vente rapide entre particuliers.
La cote réelle (annonces vues) reste prioritaire. À ajuster ou compléter dans config.yaml :
    analyzer:
      reference_prices:
        - {match: "steam deck oled 1 to", low: 450, high: 520}
`match` : tous les mots doivent être dans le titre ; « sauf » : mots qui excluent la ligne.
"""

from __future__ import annotations

import re
from typing import Any

from .utils.products import game_or_accessory, part_or_accessory
from .utils.text import first_keyword, normalize

# (mots tous présents, mots exclus, bas €, haut €) — du plus précis au plus général : la première ligne gagne.
REFERENCE: list[tuple[tuple[str, ...], tuple[str, ...], int, int]] = [
    # --- Consoles portables / de salon
    (("steam deck", "oled"), ("1 to", "1to", "1 tb", "1tb"), 380, 440),
    (("steam deck", "oled"), (), 450, 520),
    (("steam deck", "512"), (), 250, 300),
    (("steam deck", "256"), (), 220, 270),
    (("steam deck", "64"), (), 180, 220),
    (("steam deck",), (), 220, 300),
    (("ps portal",), (), 140, 170),
    (("ps5", "pro"), (), 550, 650),
    (("playstation 5", "pro"), (), 550, 650),
    (("ps5", "digital"), (), 280, 340),
    (("playstation 5", "digital"), (), 280, 340),
    (("ps5",), (), 320, 380),
    (("playstation 5",), (), 320, 380),
    (("ps4", "pro"), (), 150, 200),
    (("playstation 4", "pro"), (), 150, 200),
    (("ps4",), (), 100, 140),
    (("playstation 4",), (), 100, 140),
    (("xbox series x",), (), 280, 340),
    (("xbox series s",), (), 160, 200),
    (("switch 2",), (), 380, 430),
    (("switch", "oled"), (), 200, 240),
    (("switch", "lite"), (), 100, 130),
    (("nintendo switch",), (), 150, 190),
    # --- MacBook
    (("macbook air", "m3"), (), 750, 850),
    (("macbook air", "m2"), (), 550, 650),
    (("macbook air", "m1"), (), 380, 450),
    (("macbook pro", "m1"), (), 550, 650),
    (("macbook pro", "2017"), (), 200, 280),
    (("macbook air", "2017"), (), 160, 220),
    (("macbook air", "2015"), (), 120, 170),
    # --- ThinkPad (configuration de base i5 / 8 Go / SSD)
    (("thinkpad", "t14"), ("gen 3", "gen 4", "gen 5", "gen 6", "g3", "g4", "g5", "g6", "t14s"), 260, 340),
    (("thinkpad", "t490"), (), 210, 270),
    (("thinkpad", "t480s"), (), 190, 240),
    (("thinkpad", "t480"), (), 170, 220),
    (("thinkpad", "t470"), (), 130, 170),
    (("thinkpad", "t460"), (), 100, 140),
    (("thinkpad", "x390"), (), 180, 230),
    (("thinkpad", "x280"), (), 150, 190),
    (("thinkpad", "x270"), (), 120, 160),
    (("thinkpad", "x260"), (), 100, 140),
    # --- Dell Latitude
    (("latitude", "7490"), (), 170, 220),
    (("latitude", "7480"), (), 140, 180),
    (("latitude", "7390"), (), 160, 200),
    (("latitude", "5490"), (), 150, 190),
    (("latitude", "5400"), (), 180, 230),
    (("latitude", "e7470"), (), 120, 160),
    (("latitude", "e7450"), (), 100, 130),
    # --- Mini-PC
    (("m920q",), (), 160, 210),
    (("m720q",), (), 130, 170),
    (("m710q",), (), 100, 140),
    (("thinkcentre", "m700"), (), 70, 100),
    (("thinkcentre", "m73"), (), 50, 80),
    # --- Dyson (aspirateur complet avec batterie)
    (("dyson", "v15"), (), 280, 350),
    (("dyson", "v12"), (), 200, 260),
    (("dyson", "v11"), (), 170, 230),
    (("dyson", "v10"), (), 120, 170),
    (("dyson", "v8"), (), 80, 120),
    (("dyson", "v7"), (), 60, 90),
    # --- Sonos
    (("sonos", "era 100"), (), 150, 190),
    (("sonos", "beam"), (), 250, 300),
    (("sonos", "move"), (), 220, 270),
    (("sonos", "roam"), (), 100, 130),
    (("sonos", "one"), (), 100, 130),
    (("sonos", "play:1"), (), 60, 80),
    # --- Imprimantes 3D
    (("ender 3", "s1"), (), 150, 190),
    (("ender 3", "v3"), (), 110, 150),
    (("ender 3", "v2"), (), 90, 130),
    (("ender 3",), (), 70, 100),
]


def _rows(config: dict[str, Any] | None) -> list[tuple[tuple[str, ...], tuple[str, ...], int, int]]:
    """Lignes de config.yaml (prioritaires) puis la table intégrée."""
    extra = []
    for row in ((config or {}).get("analyzer") or {}).get("reference_prices") or []:
        try:
            words = tuple(w for w in normalize(str(row["match"])).split(" ") if w)
            sauf = tuple(normalize(str(w)) for w in row.get("sauf") or [])
            extra.append((words, sauf, int(row["low"]), int(row["high"])))
        except (KeyError, TypeError, ValueError):
            continue  # ligne mal écrite : ignorée
    return extra + REFERENCE


def reference_price(title: str, config: dict[str, Any] | None = None) -> tuple[int, int] | None:
    """Fourchette (bas, haut) en € du modèle cité dans le titre ; None pour un jeu, un accessoire, une pièce
    ou un modèle absent de la table."""
    if game_or_accessory(title) or part_or_accessory(title):
        return None
    text = re.sub(r"(\d)(go|gb|to|tb)\b", r"\1 \2", normalize(title))  # « 512GB » → « 512 gb »
    for words, excluded, low, high in _rows(config):
        if all(first_keyword(text, [w]) for w in words) and not (excluded and first_keyword(text, list(excluded))):
            return low, high
    return None
