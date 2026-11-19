"""Moteur commun à tous les sites.

Par passage : liste → nouvelles annonces → filtre → fiche détaillée → filtre → Claude → notification.
`run_once` fait un passage par site ; `watch` boucle en continu, chaque site à son rythme.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
from datetime import datetime, timedelta, timezone
from typing import Any

from . import analyzer, filters, notifier
from . import progress as progress_module
from .config import data_dir
from .db import Database
from .http import AccessDenied, PoliteClient
from .progress import LiveLogHandler, Progress
from .sites import Site, load_sites

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, config: dict[str, Any], db: Database, http: PoliteClient, progress: Progress | None = None):
        self.config = config
        self.progress = progress or Progress()
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
        show = self.progress.update
        show(site.label, "recherche…")
        site.on_progress = lambda query, found: show(site.label, f'Recherche "{query}" — {found} annonces')
        listed = await site.list_recent(self.http)
        new_uids = await self.db.unseen([ad.uid for ad in listed])
        new_ads = list({ad.uid: ad for ad in listed if ad.uid in new_uids}.values())

        candidates = []
        for i, ad in enumerate(new_ads, 1):
            show(site.label, f"{len(listed)} annonces · tri des nouvelles {i}/{len(new_ads)}")
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

        show(site.label, f"{len(listed)} annonces · analyse IA de {len(candidates)}…")
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
            self.progress.done(site.label, f"en pause jusqu'à {paused.astimezone().strftime('%H:%M')}")
            return
        try:
            stats = await self.process(site)
            log.info("%s : %s", site.name, stats)
            self.progress.done(site.label, f"{stats['listées']} annonces · {stats['nouvelles']} nouvelles · "
                                           f"{stats['analysées']} analysées · {stats['notifiées']} notifiées")
        except AccessDenied as exc:
            hours = float(self.network["access_denied_pause_hours"])
            await self.db.pause(site.name, hours, str(exc))
            log.warning("%s refuse l'accès (%s) : pause de %.0f h", site.name, exc, hours)
            self.progress.done(site.label, f"accès refusé, en pause {hours:.0f} h")
            await asyncio.to_thread(notifier.Notifier().send_text,
                                    f"⚠️ Deal Sniper : {site.name} refuse l'accès ({exc}). Pause de {hours:.0f} h.")
        except Exception as exc:
            log.exception("%s : passage en échec (nouvel essai au prochain passage)", site.name)
            self.progress.done(site.label, f"erreur : {exc}"[:120])

    async def watch_site(self, site: Site) -> None:
        await self.sleep(random.uniform(0, 30))  # démarrages étalés
        while not self.stopping.is_set():
            await self.process_safely(site)
            paused = await self.db.paused_until(site.name)
            wait = (paused - datetime.now(timezone.utc)).total_seconds() if paused else random.uniform(*site.interval)
            last = self.progress.sites.get(site.label, {}).get("text", "")
            next_run = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M")
            self.progress.done(site.label, f"{last} · prochain passage à {next_run}")
            await self.sleep(wait)


def _live_logging(progress: Progress) -> None:
    """En affichage dynamique, seuls avertissements et erreurs s'affichent, au-dessus des lignes de progression."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler):
            root.removeHandler(handler)
    root.addHandler(LiveLogHandler(progress))


async def _main(config: dict[str, Any], forever: bool, live: bool = False) -> None:
    progress = Progress(live=live)
    progress_module.current = progress
    if progress.live:
        _live_logging(progress)
    sites = load_sites(config)
    if not sites:
        log.error("Aucun site configuré (voir config.yaml > sites et les secrets dans .env).")
        return
    db = Database(data_dir() / "dealsniper.db")
    await db.forget_older_than(int(config["storage"]["seen_retention_days"]))
    async with PoliteClient(config["network"]["user_agent"], float(config["network"]["min_host_interval"])) as http:
        engine = Engine(config, db, http, progress)
        spinner = asyncio.create_task(progress.animate()) if progress.live else None
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
            if spinner:
                spinner.cancel()
                progress.render()
            db.close()


def run_once(config: dict[str, Any], live: bool = False) -> None:
    asyncio.run(_main(config, forever=False, live=live))


def watch(config: dict[str, Any], live: bool = False) -> None:
    asyncio.run(_main(config, forever=True, live=live))
