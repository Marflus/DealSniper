"""Distance entre vous et une annonce, à partir de la ville ou du code postal de l'annonce.

Les coordonnées des communes françaises viennent de l'API officielle et gratuite geo.api.gouv.fr :
téléchargées une seule fois (quelques Mo) dans data/communes.json, puis tout est calculé en local.
Code postal masqué (eBay : « 69*** ») → centre du département ; ville seule → commune la plus peuplée
de ce nom.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any

from .utils.text import normalize

log = logging.getLogger(__name__)

COMMUNES_URL = ("https://geo.api.gouv.fr/communes?fields=nom,codesPostaux,centre,population,codeDepartement"
                "&format=json&geometry=centre")
Point = tuple[float, float]  # (latitude, longitude)


def distance_km(a: Point, b: Point) -> float:
    """Distance à vol d'oiseau (formule de haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


class Geo:
    def __init__(self, path: Path):
        self.path = path
        self.postal: dict[str, Point] = {}
        self.dept: dict[str, Point] = {}
        self.names: dict[str, Point] = {}
        self.error: str | None = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.postal)

    def load(self, download=None) -> bool:
        """Charge les communes (en les téléchargeant la première fois). Sans exception : voir `error`."""
        with self._lock:
            if self.ready:
                return True
            try:
                if not self.path.exists():
                    data = (download or _download)()
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(json.dumps(data), encoding="utf-8")
                self._index(json.loads(self.path.read_text(encoding="utf-8")))
                self.error = None
            except Exception as exc:  # pas de réseau, fichier abîmé… : pas de distances, rien de bloqué
                self.error = str(exc)
                log.warning("Distances indisponibles : %s", exc)
            return self.ready

    def _index(self, communes: list[dict[str, Any]]) -> None:
        best_postal: dict[str, int] = {}
        best_name: dict[str, int] = {}
        dept_sum: dict[str, list[float]] = {}
        for c in communes:
            coords = (c.get("centre") or {}).get("coordinates")
            if not coords:
                continue
            point = (float(coords[1]), float(coords[0]))
            pop = int(c.get("population") or 0)
            for code in c.get("codesPostaux") or []:
                if pop >= best_postal.get(code, -1):
                    best_postal[code], self.postal[code] = pop, point
            name = normalize(c.get("nom"))
            if name and pop >= best_name.get(name, -1):
                best_name[name], self.names[name] = pop, point
            dept = str(c.get("codeDepartement") or "").lower()
            if dept:  # centre du département pondéré par la population
                weight = max(pop, 1)
                acc = dept_sum.setdefault(dept, [0.0, 0.0, 0.0])
                acc[0] += point[0] * weight
                acc[1] += point[1] * weight
                acc[2] += weight
        self.dept = {d: (lat / w, lon / w) for d, (lat, lon, w) in dept_sum.items()}

    def locate(self, text: str | None) -> Point | None:
        """« Lyon 69003 », « 69*** », « Laon (02) », « Paris, France » → coordonnées, ou None."""
        if not text or not self.ready:
            return None
        m = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
        if m and m.group(1) in self.postal:
            return self.postal[m.group(1)]
        m = re.search(r"(?<![\d*])(\d{2}|2[abAB])(?:\*{3}|\))", text) or re.search(r"\((\d{2}|2[abAB])\)", text)
        if m and m.group(1).lower() in self.dept:
            return self.dept[m.group(1).lower()]
        name = normalize(re.split(r"[,(\d]", text)[0]).strip(" -")
        return self.names.get(name)

    def distance(self, home: Point | None, place: str | None) -> float | None:
        point = self.locate(place)
        return round(distance_km(home, point)) if home and point else None


def _download() -> list[dict[str, Any]]:
    import httpx

    resp = httpx.get(COMMUNES_URL, timeout=60, headers={"User-Agent": "DealSniper/3.0 (distances)"})
    resp.raise_for_status()
    return resp.json()
