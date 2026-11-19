"""Orchestrateur du démon : une boucle asyncio par connecteur.

Pour chaque passage d'un connecteur :
  1. étape 1 : `list_recent` (liste légère) ;
  2. on ne garde que les identifiants jamais vus (SQLite) ;
  3. pré-filtre gratuit sur les infos de la liste (catégorie, budget, exclusions…) ;
  4. étape 2 : `fetch_detail` pour les seuls nouveaux candidats, avec un petit
     délai aléatoire avant chaque requête, puis nouveau passage du filtre ;
  5. analyse Claude par lots, notifications, mémorisation.

Un refus d'accès (401/403, robots.txt) met le connecteur en pause plusieurs
heures. Une erreur quelconque est journalisée et le passage suivant réessaie.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
from datetime import datetime, timezone
from typing import Any

from .. import analyzer, filters, notifier
from ..config import data_dir, env
from ..models import Ad
from .connectors import REGISTRY, Connector
from .http import AccessDenied, PoliteClient
from .store import Store

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "user_agent": "DealSniper/3.0 (veille personnelle non commerciale)",
    "min_host_interval": 10,
    "detail_delay": [2, 6],
    "access_denied_pause_hours": 6,
    "seen_retention_days": 90,
    "connectors": {"ebay": {"enabled": True}, "email": {"enabled": True}, "rss": {"enabled": True}},
}


class Daemon:
    def __init__(self, config: dict[str, Any], store: Store, http: PoliteClient, connectors: list[Connector]):
        self.config = config
        self.cfg = {**DEFAULTS, **(config.get("daemon") or {})}
        self.store = store
        self.http = http
        self.connectors = connectors
        self._stop = asyncio.Event()
        self._analysis_lock = asyncio.Lock()  # analyse + notifications : une source à la fois

    def stop(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        """Attente interruptible (arrêt propre sur SIGTERM)."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------------ #
    async def run_cycle(self, connector: Connector) -> dict[str, int]:
        """Un passage complet pour une source. Renvoie des compteurs (utile aux tests et aux logs)."""
        listed = await connector.list_recent(self.http)
        new_uids = await self.store.unseen([ad.uid for ad in listed])
        unique: dict[str, Ad] = {}
        for ad in listed:
            if ad.uid in new_uids:
                unique.setdefault(ad.uid, ad)
        stats = {"listed": len(listed), "new": len(unique), "kept": 0, "analyzed": 0, "notified": 0}
        if not unique:
            return stats

        candidates: list[Ad] = []
        low, high = self.cfg["detail_delay"]
        for ad in unique.values():
            reason = filters.check(ad, self.config)
            if reason:  # rejet sur les infos de la liste : pas de requête de détail
                await self.store.remember(ad, "rejected")
                continue
            await self._sleep(random.uniform(low, high))
            try:
                ad = await connector.fetch_detail(self.http, ad)
            except AccessDenied:
                raise
            except Exception as exc:  # détail illisible : on analyse avec les infos de la liste
                log.debug("%s : détail non lu (%s) pour %s", connector.name, exc, ad.url)
            reason = filters.check(ad, self.config)  # la description peut révéler une exclusion
            if reason:
                await self.store.remember(ad, "rejected")
            else:
                candidates.append(ad)
        stats["kept"] = len(candidates)
        if not candidates:
            return stats

        async with self._analysis_lock:
            cap = self.config["analyzer"].get("max_daily_cost_usd")
            budget = None if cap is None else max(0.0, float(cap) - await self.store.cost_today())
            result = await asyncio.to_thread(analyzer.analyze, candidates, self.config, budget)
            await self.store.add_cost(result.cost_usd)
            sent = await asyncio.to_thread(notifier.notify, result.analyzed, self.config)
        sent_uids = {ad.uid for ad in sent}
        for ad in result.analyzed:
            await self.store.remember(ad, "analyzed", ad.score, ad.uid in sent_uids)
        # Les annonces non analysées (API indisponible, plafond) ne sont pas mémorisées : retentées plus tard.
        stats.update(analyzed=len(result.analyzed), notified=len(sent))
        return stats

    async def _loop(self, connector: Connector) -> None:
        pause_hours = float(self.cfg["access_denied_pause_hours"])
        await self._sleep(random.uniform(0, 30))  # démarrages étalés
        while not self._stop.is_set():
            paused = await self.store.paused_until(connector.name)
            if paused:
                log.info("%s en pause jusqu'à %s", connector.name, paused.isoformat(timespec="minutes"))
                await self._sleep((paused - datetime.now(timezone.utc)).total_seconds())
                continue
            try:
                stats = await self.run_cycle(connector)
                await self.store.record(connector.name)
                log.info("%s : %s", connector.name, stats)
            except AccessDenied as exc:
                until = await self.store.pause(connector.name, pause_hours, str(exc))
                log.warning("%s : accès refusé (%s) → pause jusqu'à %s", connector.name, exc,
                            until.isoformat(timespec="minutes"))
                await asyncio.to_thread(
                    notifier.Notifier().send_text,
                    f"⚠️ Deal Sniper : {connector.name} refuse l'accès ({exc}). Pause de {pause_hours:.0f} h.",
                )
            except Exception as exc:
                log.exception("%s : passage en échec", connector.name)
                await self.store.record(connector.name, error=f"{type(exc).__name__}: {exc}")
            await self._sleep(random.uniform(*connector.interval))

    async def run_forever(self) -> None:
        if not self.connectors:
            log.error("Aucun connecteur configuré : rien à faire.")
            return
        log.info("Démon démarré : %s", ", ".join(f"{c.name} ({c.interval[0]:.0f}-{c.interval[1]:.0f} s)"
                                                for c in self.connectors))
        await self.store.prune(int(self.cfg["seen_retention_days"]))
        await asyncio.gather(*(self._loop(c) for c in self.connectors))
        log.info("Démon arrêté proprement.")


def build_connectors(config: dict[str, Any]) -> list[Connector]:
    cfg = {**DEFAULTS, **(config.get("daemon") or {})}
    connectors = []
    for name, conn_cfg in (cfg.get("connectors") or {}).items():
        conn_cfg = conn_cfg or {}
        if not conn_cfg.get("enabled", True):
            continue
        cls = REGISTRY.get(name)
        if cls is None:
            log.warning("Connecteur inconnu dans config.yaml : %s", name)
            continue
        connector = cls(conn_cfg, config)
        if connector.is_configured():
            connectors.append(connector)
        else:
            log.info("Connecteur %s non configuré (secrets ou réglages manquants) : ignoré.", name)
    return connectors


async def main_async(config: dict[str, Any]) -> None:
    cfg = {**DEFAULTS, **(config.get("daemon") or {})}
    store = Store(data_dir() / "dealsniper.db")
    async with PoliteClient(cfg["user_agent"], float(cfg["min_host_interval"])) as http:
        daemon = Daemon(config, store, http, build_connectors(config))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, daemon.stop)
            except NotImplementedError:  # Windows
                pass
        try:
            await daemon.run_forever()
        finally:
            store.close()


def run(config: dict[str, Any]) -> int:
    if not env("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY absente : les annonces seront collectées mais pas analysées.")
    asyncio.run(main_async(config))
    return 0
