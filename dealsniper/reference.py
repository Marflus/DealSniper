"""Prix de référence d'occasion (France, appareil en état de marche) pour l'électronique surveillée.

Pourquoi : sans cote du marché (modèle encore peu vu), la notation rapide n'a que le titre et ses estimations
varient d'un lot à l'autre (même Steam Deck OLED 512 Go estimé 300 € puis 420 €). Le repère est donné à l'IA
et, sans cote, l'estimation est ramenée dans la fourchette (analyzer.make_realistic).

Fourchettes de revente entre particuliers (relevé du 22/11/2026, sources en commentaire) : le bas = vente
rapide, le haut = bon état. À revoir quand les prix bougent (hausse des prix neufs en 2026).
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
    # Relevé du 22/11/2026 : ventes eBay France (PrixRetro), médianes leboncoin, reprises Micromania (nov. 2026),
    # reconditionnés Back Market / Dero / Combak (dont on retire la marge du revendeur). Hausse générale des
    # prix neufs en 2026 (PS5 649 €, Steam Deck OLED 779 €, Xbox Series X 799 €) : l'occasion a suivi.
    # --- Consoles portables / de salon
    (("steam deck", "oled"), ("1 to", "1to", "1 tb", "1tb"), 430, 520),  # annonces 450-600 €, neuf 779 €
    (("steam deck", "oled"), (), 520, 620),                              # 1 To, neuf 919 €
    (("steam deck", "512"), (), 320, 380),
    (("steam deck", "256"), (), 280, 340),                               # vendu 300 €, annonces 350 €
    (("steam deck", "64"), (), 220, 270),
    (("steam deck",), (), 280, 350),
    (("ps portal",), (), 140, 180),
    (("ps5", "pro"), (), 620, 750),                                      # reprise Micromania 700 €
    (("playstation 5", "pro"), (), 620, 750),
    (("ps5", "digital"), (), 360, 420),                                  # eBay Slim Digital ≈ 420 €
    (("playstation 5", "digital"), (), 360, 420),
    (("ps5",), (), 400, 460),                                            # eBay Slim ≈ 446 €, reprise 425-475 €
    (("playstation 5",), (), 400, 460),
    (("ps4", "pro"), (), 130, 180),                                      # reprise 125 €, reconditionnée 181 €
    (("playstation 4", "pro"), (), 130, 180),
    (("ps4",), (), 100, 130),                                            # reconditionnée 121 €
    (("playstation 4",), (), 100, 130),
    (("xbox series x",), (), 400, 480),                                  # reprise 500 € (temporaire), neuf 799 €
    (("xbox series s",), (), 230, 290),                                  # reprise 285-320 €
    (("switch 2",), (), 370, 420),                                       # leboncoin médiane 400 €, eBay 404 €
    (("switch", "oled"), (), 190, 230),                                  # reconditionnée 199-236 €
    (("switch", "lite"), (), 110, 140),                                  # reconditionnée 148-150 €
    (("nintendo switch",), (), 140, 170),                                # médiane Vinted / leboncoin ≈ 150 €
    # --- MacBook
    (("macbook air", "m3"), (), 780, 880),
    (("macbook air", "m2"), (), 600, 680),                               # reconditionné dès 749 €
    (("macbook air", "m1"), (), 400, 470),                               # reconditionné dès 514 €
    (("macbook pro", "m1"), (), 470, 540),                               # 13" reconditionné dès 574 €
    (("macbook pro", "2017"), (), 150, 220),                             # annonces 145-240 €
    (("macbook air", "2017"), (), 130, 190),                             # annonces 115-225 €
    (("macbook air", "2015"), (), 100, 140),
    # --- ThinkPad (i5, 8 Go, SSD ; reconditionnés : T480 dès 189 €, T490 278-368 €, médianes 400-600 €)
    (("thinkpad", "t14"), ("gen 3", "gen 4", "gen 5", "gen 6", "g3", "g4", "g5", "g6", "t14s"), 320, 400),
    (("thinkpad", "t490"), (), 250, 320),
    (("thinkpad", "t480s"), (), 240, 300),
    (("thinkpad", "t480"), (), 220, 280),                                # annonces leboncoin 250-580 €
    (("thinkpad", "t470"), (), 160, 210),
    (("thinkpad", "t460"), (), 120, 160),
    (("thinkpad", "x390"), (), 230, 290),
    (("thinkpad", "x280"), (), 180, 240),
    (("thinkpad", "x270"), (), 140, 180),
    (("thinkpad", "x260"), (), 110, 150),
    # --- Dell Latitude (reconditionnés : 7490 dès 275 €, 5400 dès 210 €, 5490 dès 186 €, 7480 dès 211 €)
    (("latitude", "7490"), (), 250, 320),
    (("latitude", "7480"), (), 200, 260),
    (("latitude", "7390"), (), 220, 280),
    (("latitude", "5490"), (), 190, 250),
    (("latitude", "5400"), (), 220, 280),
    (("latitude", "e7470"), (), 160, 210),
    (("latitude", "e7450"), (), 130, 170),
    # --- Mini-PC (M720q reconditionné dès 138 €, Tiny 214-229 € ; leboncoin 100-200 €)
    (("m920q",), (), 180, 240),
    (("m720q",), (), 140, 190),
    (("m710q",), (), 100, 140),
    (("thinkcentre", "m700"), (), 70, 100),
    (("thinkcentre", "m73"), (), 50, 80),
    # --- Dyson complet avec batterie (annonces : V8 119-180 €, V10 140-310 €, V11 250-350 €, V15 380 €)
    (("dyson", "v15"), (), 330, 400),
    (("dyson", "v12"), (), 230, 290),
    (("dyson", "v11"), (), 220, 280),
    (("dyson", "v10"), (), 130, 180),
    (("dyson", "v8"), (), 90, 130),
    (("dyson", "v7"), (), 60, 90),
    # --- Sonos (Era 100 : 140-200 € ; Play:1 : 110-180 € annoncés)
    (("sonos", "era 100"), (), 150, 190),
    (("sonos", "beam"), (), 250, 320),
    (("sonos", "move"), (), 250, 330),
    (("sonos", "roam"), (), 120, 160),
    (("sonos", "one"), (), 110, 140),
    (("sonos", "play:1"), (), 90, 130),
    (("sonos", "play 1"), (), 90, 130),
    # --- Imprimantes 3D (leboncoin : V3 SE 110-130 €, V2 70-150 €, Ender 3 50-80 €)
    (("ender 3", "s1"), (), 110, 150),
    (("ender 3", "v3"), (), 110, 140),
    (("ender 3", "v2"), (), 80, 120),
    (("ender 3",), (), 50, 80),
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
