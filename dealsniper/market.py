"""Cote du marché : prix réel d'un modèle, calculé à partir des annonces vues (gratuit, sans IA).

Chaque annonce vue donne une observation « modèle → prix » (ex. « thinkpad t480 » → 180 €).
La cote d'un modèle est la médiane (réduite aux valeurs centrales) des annonces EN ÉTAT DE MARCHE
des 60 derniers jours. Tant qu'il n'y en a pas assez, un échantillon d'occasions fonctionnelles est
demandé à l'API eBay (aucun plafond de prix), gardé 7 jours.

Utilisations : écarter avant l'IA une annonce dont la marge est impossible, et donner à la notation
une revente estimée fondée sur de vrais prix plutôt que sur l'intuition du modèle.
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .filters import desirable_brand
from .models import Ad
from .utils.products import ACCESSORY_FIRST, NOT_THE_DEVICE, game_or_accessory
from .utils.text import first_keyword, normalize

log = logging.getLogger(__name__)

MIN_SAMPLES = 5          # en dessous, la médiane n'est pas fiable
LOCAL_DAYS = 60          # fenêtre des observations locales
SAMPLE_MAX_AGE_DAYS = 7  # durée de vie d'un échantillon eBay

# Mots-clés de catégorie trop vagues pour identifier un modèle (« pc portable 15 » ne veut rien dire).
GENERIC = {"pc portable", "ordinateur portable", "laptop", "mini pc", "mini-pc", "tv", "ecran", "moniteur",
           "televiseur", "television", "oled", "qled", "tiny", "nuc", "chromebook"}
# Jetons qui ne désignent pas un modèle : processeur, mémoire, puissance, taille…
NOISE = re.compile(r"^(i[3579]|r[3579]|ryzen\d?|\d+(go|gb|to|tb|mo|mb|w|v|mah|wh|hz|ghz|cm|mm|ah|g|p|k)|"
                   r"ddr\d|gen\d+|\d+(e|eme|th|nd|rd|st))$")
STOPWORDS = {"de", "du", "des", "la", "le", "les", "et", "avec", "sans", "pour", "en", "a", "au", "un", "une",
             "hs", "panne", "neuf", "occasion", "tres", "bon", "etat", "lot", "noir", "blanc", "gris", "rouge",
             "bleu", "vends", "vend", "vendu", "recherche", "pc", "portable", "ordinateur", "batterie", "chargeur"}
# Annonce en panne, incomplète ou à réparer : son prix ne dit rien de la valeur d'un appareil qui marche.
BROKEN = ("hs", "h.s", "panne", "en panne", "pour pieces", "pieces detachees", "a reparer", "reparer",
          "ne s'allume", "ne demarre", "ne fonctionne", "ne marche", "casse", "cassee", "defectueux",
          "defectueuse", "bloque", "bloquee", "sans ssd", "sans disque", "sans ecran", "sans carte mere",
          "ecran fissure", "dalle cassee", "oxyde", "probleme", "bug", "pour bricoleur")


def category_brands(config: dict[str, Any]) -> list[str]:
    """Mots-clés de catégorie servant de « marques » pour reconnaître un modèle (les plus longs d'abord)."""
    return sorted({k for cat in config["categories"] for k in cat.get("keywords") or []}, key=len, reverse=True)


@lru_cache(maxsize=4096)
def _brand(brand: str) -> tuple[str, re.Pattern[str] | None]:
    """Mot-clé normalisé et son expression compilée (mot entier) ; None pour un mot trop vague."""
    b = normalize(brand)
    if not b or b in GENERIC:
        return b, None
    return b, re.compile(r"(?<![a-z0-9])" + re.escape(b) + r"(?![a-z0-9])")


def model_key(title: str, brands: list[str]) -> str | None:
    """« Lenovo ThinkPad T480 i5 8Go » → « thinkpad t480 » ; None si le modèle n'est pas identifiable."""
    text = normalize(title)
    best: tuple[int, str] | None = None
    for brand in brands:
        b, pattern = _brand(brand)
        if pattern is None:
            continue
        m = pattern.search(text)
        if m and (best is None or m.start() < best[0] or (m.start() == best[0] and len(b) > len(best[1]))):
            best = (m.start(), b)
    if best is None:
        return None
    start, brand = best
    tokens = re.findall(r"[a-z0-9]+", text[start + len(brand):])[:5]
    for i, token in enumerate(tokens):
        if not any(c.isdigit() for c in token) or NOISE.match(token):
            continue
        before = tokens[i - 1] if i else ""
        named = before.isalpha() and len(before) >= 3 and before not in STOPWORDS  # « quietcomfort 35 »
        screen_size = token.isdigit() and len(token) < 3 and (10 <= int(token) <= 17 or not (i == 0 or named))
        if screen_size:
            continue  # « 13 », « 15 » : taille d'écran, pas un modèle
        key = f"{brand} {before} {token}" if named and i == 1 else f"{brand} {token}"
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if re.fullmatch(r"g\d{1,2}", nxt):  # EliteBook 840 G5
            key += f" {nxt}"
        return key
    if any(c.isdigit() for c in brand):  # le mot-clé est déjà un modèle (« m720q »)
        return brand
    words = [t for t in tokens[:2] if t.isalpha() and len(t) >= 3 and t not in STOPWORDS]
    return f"{brand} {words[0]}" if words else None


def fingerprint(ad: Ad, brands: list[str]) -> str | None:
    """Empreinte pour repérer une même annonce publiée sur plusieurs sites : modèle (ou mots du titre) + prix."""
    if ad.price is None:
        return None
    key = model_key(ad.title, brands)
    if not key:
        words = [t for t in re.findall(r"[a-z0-9]+", normalize(ad.title)) if len(t) > 1 and t not in STOPWORDS]
        if len(words) < 3:
            return None
        key = " ".join(sorted(set(words[:6])))
    return f"{key}|{round(ad.price)}"


def is_working(text: str) -> bool:
    """Vrai si rien n'indique une panne ou un appareil incomplet."""
    return first_keyword(normalize(text), list(BROKEN)) is None


def is_the_device(title: str) -> bool:
    """Faux pour un accessoire ou une pièce vendus seuls (leur prix fausserait la cote)."""
    text = normalize(title)
    first_words = re.findall(r"[a-z]+", text)[:2]
    if ACCESSORY_FIRST.intersection(first_words) or game_or_accessory(title):  # « Zelda … Switch » : un jeu
        return False
    return first_keyword(text, list(NOT_THE_DEVICE)) is None


def central_median(prices: list[float]) -> float | None:
    """Médiane des valeurs centrales (on retire 20 % en haut et en bas : annonces fantaisistes)."""
    if len(prices) < MIN_SAMPLES:
        return None
    values = sorted(prices)
    cut = len(values) // 5
    return round(statistics.median(values[cut:len(values) - cut] or values), 0)


@dataclass
class Quote:
    model: str
    median: float
    count: int
    source: str  # "annonces" (vues par Deal Sniper) ou "ebay" (échantillon d'occasions)

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "median": self.median, "count": self.count, "source": self.source}


Sampler = Callable[[str], Awaitable[list[tuple[str, float]]]]


def margin_score(margin: float, desirable: bool = False) -> int:
    """Note du mode sans IA, d'après la marge ; +1 pour une marque recherchée rentable (se revend mieux)."""
    score = 8 if margin >= 60 else 6 if margin >= 30 else 4 if margin >= 10 else 2
    return min(score + 1, 10) if desirable and margin >= 10 else score


def free_estimate(ad, quote, haircut: float = 0.9) -> dict:
    """Estimation SANS IA, à partir de la cote du marché seule (mode sans IA).

    Pas de diagnostic de panne ni de facilité : juste une revente et une marge fondées sur les prix réels.
    """
    resale = round(quote.median * haircut)
    repair = ad.price_mode == "enchere" or ad.total_price is None
    margin = None if repair else round(resale - ad.total_price - 0.10 * resale)
    brand = bool(desirable_brand(ad))
    score = 5 if margin is None else margin_score(margin, brand)
    broken = not is_working(f"{ad.title} {ad.description[:200]}")
    return {
        "type_affaire": "reparation" if broken else "revente_directe",
        "score_rentabilite": score, "panne_probable": "" if broken else "aucune",
        "valeur_revente_estimee": float(resale), "valeur_revente_ia": None,
        "marge_nette_estimee": None if margin is None else float(margin),
        "cout_piece_estime": 0.0, "source_revente": "cote", "sans_ia": True, "realiste": True,
        "marque_recherchee": brand,
    }


class Market:
    def __init__(self, db, config: dict[str, Any], sampler: Sampler | None = None, samples_per_pass: int = 10):
        self.db = db
        self.brands = category_brands(config)
        self.min_price = float(config["filters"].get("min_price") or 0)
        self.sampler = sampler
        self.samples_per_hour = samples_per_pass
        self._reset()

    def _reset(self) -> None:
        """Cotes recalculées et quota d'échantillons eBay renouvelé chaque heure (mode surveillance continue)."""
        self.samples_left = self.samples_per_hour
        self._cache: dict[str, Quote | None] = {}
        self._since = time.monotonic()

    def key(self, ad: Ad) -> str | None:
        """Modèle de l'appareil vendu ; None pour un jeu ou un accessoire qui le cite (pas sa cote)."""
        if game_or_accessory(ad.title):
            return None
        return model_key(ad.title, self.brands)

    async def observe(self, ads: list[Ad]) -> None:
        """Enregistre le prix de chaque annonce dont le modèle est identifiable (aucune requête)."""
        rows = []
        for ad in ads:
            key = self.key(ad)
            price = ad.total_price
            if not key or price is None or price < max(self.min_price, 5) or ad.price_mode == "enchere":
                continue
            text = f"{ad.title} {ad.description[:300]}"
            if is_the_device(ad.title):
                rows.append((ad.uid, key, price, int(is_working(text)), ad.source))
        await self.db.record_prices(rows)

    async def quote(self, ad: Ad) -> Quote | None:
        """Cote du modèle de l'annonce, ou None si inconnue."""
        key = self.key(ad)
        if not key:
            return None
        if time.monotonic() - self._since > 3600:
            self._reset()
        if key not in self._cache:
            self._cache[key] = await self._quote(key)
        return self._cache[key]

    async def _quote(self, key: str) -> Quote | None:
        local = await self.db.working_prices(key, LOCAL_DAYS)
        median = central_median(local)
        if median:
            return Quote(key, median, len(local), "annonces")
        samples = await self.db.market_sample(key, SAMPLE_MAX_AGE_DAYS)
        if samples is None and self.sampler and self.samples_left > 0:
            self.samples_left -= 1
            try:
                found = await self.sampler(key)
            except Exception as exc:  # cote indisponible : l'IA estimera seule
                log.debug("Échantillon eBay impossible pour « %s » : %s", key, exc)
                return None
            samples = [p for title, p in found
                       if p >= max(self.min_price, 5) and is_working(title) and is_the_device(title)
                       and model_key(title, self.brands) == key]
            await self.db.save_market_sample(key, samples)
        median = central_median(samples or [])
        return Quote(key, median, len(samples), "ebay") if median else None
