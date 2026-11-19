"""Moteur commun à tous les sites.

Par passage : liste → nouvelles annonces → filtre → fiche détaillée → filtre → Claude → notification.
`run_once` fait un passage par site ; `watch` boucle en continu, chaque site à son rythme.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
from datetime import datetime, timezone
from typing import Any

from . import analyzer, filters, notifier
from .config import data_dir
from .db import Database
from .http import AccessDenied, PoliteClient
from .sites import Site, load_sites

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, config: dict[str, Any], db: Database, http: PoliteClient):
        self.config = config
        self.db = db
        self.http = http
        self.network = config["network"]
        self.stopping = asyncio.Event()
        self._ai_lock = asyncio.Lock()  # une seule analyse / série de notifications à la fois

    async def sleep(self, seconds: float) -> None:
        """Pause interrompue proprement par un arrêt (Ctrl+C, systemd)."""
        try:
            await asyncio.wait_for(self.stopping.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    async def process(self, site: Site) -> dict[str, int]:
        """Un passage complet pour un site. Renvoie des compteurs pour les logs."""
        listed = await site.list_recent(self.http)
        new_uids = await self.db.unseen([ad.uid for ad in listed])
        new_ads = list({ad.uid: ad for ad in listed if ad.uid in new_uids}.values())

        candidates = []
        for ad in new_ads:
            reason = filters.check(ad, self.config)
            if reason:
                await self.db.remember(ad, "rejected", reason=reason)
                continue
            await self.sleep(random.uniform(*self.network["detail_delay"]))
            try:
                ad = await site.fetch_detail(self.http, ad)
            except AccessDenied:
                raise
            except Exception as exc:  # fiche illisible : on garde les infos de la liste
                log.debug("%s : fiche non lue (%s)", site.name, exc)
            reason = filters.check(ad, self.config)
            if reason:
                await self.db.remember(ad, "rejected", reason=reason)
            else:
                candidates.append(ad)

        stats = {"listées": len(listed), "nouvelles": len(new_ads), "retenues": len(candidates),
                 "analysées": 0, "notifiées": 0}
        if not candidates:
            return stats

        async with self._ai_lock:
            cap = self.config["analyzer"]["max_daily_cost_usd"]
            budget = None if cap is None else max(0.0, cap - await self.db.cost_today())
            result = await asyncio.to_thread(analyzer.analyze, candidates, self.config, budget)
            await self.db.add_cost(result.cost_usd)
            sent = await asyncio.to_thread(notifier.notify, result.analyzed, self.config)
        sent_uids = {ad.uid for ad in sent}
        for ad in result.analyzed:
            await self.db.remember(ad, "analyzed", notified=ad.uid in sent_uids)
        stats.update(analysées=len(result.analyzed), notifiées=len(sent))
        return stats

    async def process_safely(self, site: Site) -> None:
        """`process` + gestion des erreurs : un site en panne n'arrête jamais les autres."""
        paused = await self.db.paused_until(site.name)
        if paused:
            log.info("%s en pause jusqu'à %s", site.name, paused.strftime("%d/%m %H:%M UTC"))
            return
        try:
            log.info("%s : %s", site.name, await self.process(site))
        except AccessDenied as exc:
            hours = float(self.network["access_denied_pause_hours"])
            await self.db.pause(site.name, hours, str(exc))
            log.warning("%s refuse l'accès (%s) : pause de %.0f h", site.name, exc, hours)
            await asyncio.to_thread(notifier.Notifier().send_text,
                                    f"⚠️ Deal Sniper : {site.name} refuse l'accès ({exc}). Pause de {hours:.0f} h.")
        except Exception:
            log.exception("%s : passage en échec (nouvel essai au prochain passage)", site.name)

    async def watch_site(self, site: Site) -> None:
        await self.sleep(random.uniform(0, 30))  # démarrages étalés
        while not self.stopping.is_set():
            await self.process_safely(site)
            paused = await self.db.paused_until(site.name)
            wait = (paused - datetime.now(timezone.utc)).total_seconds() if paused else random.uniform(*site.interval)
            await self.sleep(wait)


async def _main(config: dict[str, Any], forever: bool) -> None:
    sites = load_sites(config)
    if not sites:
        log.error("Aucun site configuré (voir config.yaml > sites et les secrets dans .env).")
        return
    db = Database(data_dir() / "dealsniper.db")
    await db.forget_older_than(int(config["storage"]["seen_retention_days"]))
    async with PoliteClient(config["network"]["user_agent"], float(config["network"]["min_host_interval"])) as http:
        engine = Engine(config, db, http)
        try:
            if forever:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        asyncio.get_running_loop().add_signal_handler(sig, engine.stopping.set)
                    except NotImplementedError:  # Windows
                        pass
                log.info("Surveillance continue : %s", ", ".join(s.name for s in sites))
                await asyncio.gather(*(engine.watch_site(s) for s in sites))
            else:
                for site in sites:
                    await engine.process_safely(site)
        finally:
            db.close()


def run_once(config: dict[str, Any]) -> None:
    asyncio.run(_main(config, forever=False))


def watch(config: dict[str, Any]) -> None:
    asyncio.run(_main(config, forever=True))
