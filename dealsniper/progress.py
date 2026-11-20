"""Progression d'un passage : une ligne par site, mise à jour en place dans le terminal.

    eBay : 312 annonces · 12 nouvelles · 3 analysées · 1 notifiée · 1 min 12 s · IA 0,0123 $
    Vinted : Recherche "thinkpad" — 25 annonces · 42 s /
    Total : 1 min 54 s · IA 0,0123 $

Le même état est lu par l'interface web (`snapshot`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import textwrap
import time

SPINNER = "|/-\\"


def duration(seconds: float) -> str:
    """42 → « 42 s », 130 → « 2 min 10 s », 3720 → « 1 h 02 »."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"


def dollars(usd: float) -> str:
    return f"{usd:.4f} $".replace(".", ",")


class Progress:
    def __init__(self, live: bool = False):
        self.live = live and sys.stdout.isatty()
        self.sites: dict[str, dict] = {}
        self.started = time.monotonic()
        self._frame = 0
        self._drawn = 0
        if self.live and os.name == "nt":
            os.system("")  # active les codes d'affichage ANSI dans la console Windows

    # --- état -----------------------------------------------------------------
    def _state(self, site: str) -> dict:
        state = self.sites.get(site)
        if state is None or state["done"]:  # nouveau passage pour ce site : le chrono repart
            state = self.sites[site] = {"text": "", "done": False, "start": time.monotonic(), "end": None, "cost": 0.0,
                                        "searches": []}
        return state

    def update(self, site: str, text: str) -> None:
        self._state(site)["text"] = text
        self.render()

    def done(self, site: str, text: str) -> None:
        state = self._state(site)
        state.update(text=text, done=True, end=time.monotonic())
        self.render()

    def append(self, site: str, text: str) -> None:
        """Complète la ligne d'un site sans relancer son chrono (« · prochain passage à 14:05 »)."""
        if site in self.sites:
            self.sites[site]["text"] += text
            self.render()

    def log_search(self, site: str, query: str, found: int) -> None:
        """Garde le résultat de chaque recherche pour le résumé de fin."""
        self._state(site)["searches"].append({"query": query, "found": found})

    def add_cost(self, site: str, usd: float) -> None:
        self._state(site)["cost"] += usd

    def elapsed(self, state: dict) -> float:
        return (state["end"] or time.monotonic()) - state["start"]

    @property
    def total_elapsed(self) -> float:
        """Durée du passage, figée quand tous les sites ont fini."""
        states = list(self.sites.values())  # copie : l'interface lit pendant que le moteur écrit
        if states and all(s["done"] for s in states):
            return max(s["end"] for s in states) - self.started
        return time.monotonic() - self.started

    @property
    def total_cost(self) -> float:
        return sum(s["cost"] for s in list(self.sites.values()))

    def snapshot(self) -> dict:
        """État pour l'interface web (durées en secondes)."""
        # Lu par l'interface (autre thread) pendant que le moteur ajoute des sites et des recherches :
        # on parcourt des copies (copie de dict/list faite d'un bloc, sans changement de thread au milieu).
        sites = [{"site": name, "text": s["text"], "done": s["done"], "elapsed": round(self.elapsed(s), 1),
                  "cost": round(s["cost"], 5), "searches": list(s["searches"])}
                 for name, s in list(self.sites.items())]
        return {"sites": sites, "elapsed": round(self.total_elapsed, 1),
                "cost": round(self.total_cost, 5), "done": bool(sites) and all(s["done"] for s in sites)}

    # --- affichage terminal ------------------------------------------------------
    def lines(self) -> list[str]:
        spin = SPINNER[self._frame % len(SPINNER)]
        lines = []
        for name, s in self.sites.items():
            extra = f" · {duration(self.elapsed(s))}" + (f" · IA {dollars(s['cost'])}" if s["cost"] else "")
            lines.append(f"{name} : {s['text']}{extra}" + ("" if s["done"] else f" {spin}"))
        if len(lines) > 1 or self.total_cost:
            lines.append(f"Total : {duration(self.total_elapsed)} · IA {dollars(self.total_cost)}")
        return lines

    def print_summary(self) -> None:
        """Fin d'un passage : nombre d'annonces de chaque recherche, sous les lignes de progression."""
        rows = [(name, s["searches"]) for name, s in self.sites.items() if s["searches"]]
        if not rows:
            return
        print("\nDétail des recherches :")
        for name, searches in rows:
            print(f"  {name} ({len(searches)} recherches, {sum(x['found'] for x in searches)} annonces)")
            print(textwrap.fill(" · ".join(f"{x['query']} {x['found']}" for x in searches), width=100,
                                initial_indent="    ", subsequent_indent="    "))
        self._drawn = 0  # le bloc de progression reste affiché tel quel

    def clear(self) -> None:
        if self.live and self._drawn:
            sys.stdout.write(f"\x1b[{self._drawn}F\x1b[J")  # remonte et efface le bloc
            self._drawn = 0

    def render(self) -> None:
        if not self.live:
            return
        self.clear()
        lines = self.lines()
        if lines:
            sys.stdout.write("\n".join(lines) + "\n")
            self._drawn = len(lines)
        sys.stdout.flush()

    async def animate(self) -> None:
        """Fait tourner le petit indicateur tant que le passage est en cours."""
        while True:
            self._frame += 1
            self.render()
            await asyncio.sleep(0.15)


class LiveLogHandler(logging.StreamHandler):
    """Affiche avertissements et erreurs au-dessus du bloc de progression sans le casser."""

    def __init__(self, progress: Progress):
        super().__init__(sys.stdout)
        self.progress = progress
        self.setLevel(logging.WARNING)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.progress.clear()
        super().emit(record)
        self.progress.render()


# Progression du passage en cours (lue par l'interface web).
current = Progress()
