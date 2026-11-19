"""Progression d'un passage : une ligne par site, mise à jour en place dans le terminal.

    eBay : 312 annonces · 12 nouvelles · 3 analysées · 1 notifiée
    Vinted : Recherche "thinkpad" — 25 annonces /

Le même état est lu par l'interface web (`snapshot`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

SPINNER = "|/-\\"


class Progress:
    def __init__(self, live: bool = False):
        self.live = live and sys.stdout.isatty()
        self.sites: dict[str, dict] = {}
        self._frame = 0
        self._drawn = 0
        if self.live and os.name == "nt":
            os.system("")  # active les codes d'affichage ANSI dans la console Windows

    # --- état -----------------------------------------------------------------
    def update(self, site: str, text: str) -> None:
        self.sites[site] = {"text": text, "done": False}
        self.render()

    def done(self, site: str, text: str) -> None:
        self.sites[site] = {"text": text, "done": True}
        self.render()

    def snapshot(self) -> list[dict]:
        return [{"site": name, **state} for name, state in self.sites.items()]

    # --- affichage terminal ------------------------------------------------------
    def lines(self) -> list[str]:
        spin = SPINNER[self._frame % len(SPINNER)]
        return [f"{name} : {s['text']}" + ("" if s["done"] else f" {spin}") for name, s in self.sites.items()]

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
