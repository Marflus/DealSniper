"""Moteur commun à tous les sites.

Par passage : liste → nouvelles annonces → filtre → fiche détaillée → filtre → Claude → notification.
`run_once` fait un passage par site ; `watch` boucle en continu, chaque site à son rythme.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import signal
from datetime import UTC, datetime, timedelta
from typing import Any

from . import analyzer, filters, notifier
from . import progress as progress_module
from . import watch as watchlist_match
from .config import data_dir
from .db import Database
from .http import AccessDenied, PoliteClient
from .market import Market, Quote, category_brands, fingerprint, free_estimate
from .models import Ad
from .progress import LiveLogHandler, Progress
from .purchases import calibrated_config
from .sites import Site, load_sites
from .sites import skipped as skipped_sites

SITE_LABELS = {"ebay": "eBay", "leboncoin": "Leboncoin", "vinted": "Vinted", "facebook": "Facebook"}

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, config: dict[str, Any], db: Database, http: PoliteClient, progress: Progress | None = None,
                 market: Market | None = None):
        self.config = config
        self.market = market
        self.brands = market.brands if market else category_brands(config)
        self.progress = progress or Progress()
        self.db = db
        self.http = http
        self.network = config["network"]
        self.stopping = asyncio.Event()
        self._ai_lock = asyncio.Lock()  # une seule analyse / série de notifications à la fois

    async def sleep(self, seconds: float) -> None:
        """Pause interrompue proprement par un arrêt (Ctrl+C, systemd)."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stopping.wait(), timeout=max(0.0, seconds))

    async def market_problem(self, ad: Ad) -> str | None:
        """Renseigne la cote de l'annonce ; raison de rejet si la marge est impossible même sans réparation."""
        if not self.market:
            return None
        quote = await self.market.quote(ad)
        if not quote:
            return None
        ad.market = quote.as_dict()
        if ad.price_mode == "enchere" or ad.total_price is None:
            return None
        analyzer_cfg = self.config["analyzer"]
        best = quote.median * float(analyzer_cfg["resale_haircut"]) * 0.9 - ad.total_price
        if best < float(analyzer_cfg["market_min_margin"]):
            return f"marge impossible selon la cote ({quote.median:.0f} € sur {quote.count} annonces)"
        return None

    async def budget_left(self) -> float | None:
        """Dollars encore disponibles aujourd'hui pour l'IA (None = pas de plafond)."""
        cap = self.config["analyzer"]["max_daily_cost_usd"]
        return None if cap is None else max(0.0, float(cap) - await self.db.cost_today())

    async def process(self, site: Site) -> dict[str, int]:
        """Un passage complet pour un site. Renvoie des compteurs pour les logs."""
        show = self.progress.update
        show(site.label, "recherche…")
        planned = len(site.cfg.get("searches") or [])
        totals = {"done": 0, "ads": 0}

        def on_search(query: str, found: int) -> None:
            totals["done"] += 1
            totals["ads"] += found
            self.progress.log_search(site.label, query, found)
            step = f" ({totals['done']}/{planned})" if planned else ""
            show(site.label, f'Recherche "{query}"{step} — {found} annonces · {totals["ads"]} au total')

        site.on_progress = on_search
        listed = await site.list_recent(self.http)
        new_uids = await self.db.unseen([ad.uid for ad in listed])
        # Annonces déjà vues mais enregistrées sans photo : on complète avec celle de la liste (aucune requête).
        await self.db.fill_images({ad.uid: ad.image for ad in listed if ad.image and ad.uid not in new_uids})
        new_ads = list({ad.uid: ad for ad in listed if ad.uid in new_uids}.values())
        await self._price_drops([ad for ad in listed if ad.uid not in new_uids])
        if self.market:
            await self.market.observe(listed)  # cote : chaque prix vu enrichit la référence (gratuit)
        if self.config["analyzer"].get("enabled", True) and await self.budget_left() == 0:
            # Budget IA du jour épuisé : les nouvelles annonces restent « non vues » et seront
            # analysées demain ; inutile de lire leurs fiches maintenant.
            show(site.label, f"{len(listed)} annonces · budget IA du jour atteint")
            return {"listées": len(listed), "nouvelles": len(new_ads), "retenues": 0, "analysées": 0, "notifiées": 0}

        prints: dict[str, str | None] = {}  # empreintes anti-doublons (même annonce sur plusieurs sites)
        candidates = await self._select(site, listed, new_ads, prints)
        stats = {"listées": len(listed), "nouvelles": len(new_ads), "retenues": len(candidates),
                 "analysées": 0, "notifiées": 0}
        if not candidates:
            return stats

        candidates.sort(key=filters.priority)  # les plus prometteuses d'abord
        max_ads = self.config["analyzer"].get("max_ads_per_run")
        if max_ads and len(candidates) > int(max_ads):
            # On garde les plus prometteuses ; le reste n'est pas « vu » et sera analysé au prochain passage.
            candidates = candidates[: int(max_ads)]
            stats["retenues"] = len(candidates)
        if not self.config["analyzer"].get("enabled", True):
            return await self._process_without_ai(site, candidates, prints, stats)

        show(site.label, f"{len(listed)} annonces · analyse IA de {len(candidates)}…")
        async with self._ai_lock:
            budget = await self.budget_left()
            result = await asyncio.to_thread(analyzer.analyze, candidates, self.config, budget)
            await self.db.add_cost(result.cost_usd)
            self.progress.add_cost(site.label, result.cost_usd)
            sent = await asyncio.to_thread(notifier.notify, result.analyzed, self.config)
        sent_uids = {ad.uid for ad in sent}
        for ad in result.analyzed:
            await self.db.remember(ad, "analyzed", notified=ad.uid in sent_uids, fingerprint=prints.get(ad.uid))
        stats.update(analysées=len(result.analyzed), notifiées=len(sent))
        return stats

    def _check(self, ad: Ad) -> str | None:
        """Raison de rejet par le filtre ; une annonce surveillée n'est jamais écartée
        (le filtre renseigne quand même sa catégorie et ses signaux)."""
        reason = filters.check(ad, self.config)
        return None if ad.watched else reason

    async def _price_drops(self, already_seen: list[Ad]) -> None:
        """Annonces déjà vues dont le prix a baissé : prix mis à jour, alerte pour celles déjà notées."""
        prices = {ad.uid: ad.price for ad in already_seen if ad.price is not None}
        min_drop = float(self.config["filters"].get("price_drop_min_eur") or 10)
        drops = await asyncio.to_thread(self.db.record_price_drops, prices, min_drop)
        notable = [d for d in drops if d["status"] == "analyzed" and (d["score"] or 0) >= 1]
        if notable:
            await asyncio.to_thread(notifier.notify_price_drops, notable, self.config)

    async def _select(self, site: Site, listed: list[Ad], new_ads: list[Ad],
                      prints: dict[str, str | None]) -> list[Ad]:
        """Tri des nouvelles annonces : doublons, filtre, cote, puis fiche détaillée et second filtre."""
        candidates = []
        for i, ad in enumerate(new_ads, 1):
            self.progress.update(site.label, f"{len(listed)} annonces · tri des nouvelles {i}/{len(new_ads)}")
            prints[ad.uid] = fingerprint(ad, self.brands)
            ad.watched = watchlist_match.matches(ad, self.config.get("watchlist") or [])
            duplicate = prints[ad.uid] and await self.db.duplicate_of(prints[ad.uid], ad.source)
            # Une annonce surveillée passe outre la catégorie et la cote (mais pas les doublons).
            if duplicate:
                reason = f"doublon d'une annonce déjà vue sur {SITE_LABELS.get(duplicate, duplicate)}"
            else:
                reason = self._check(ad)
                problem = None if reason else await self.market_problem(ad)  # renseigne aussi la cote
                reason = reason or (None if ad.watched else problem)
            if not reason and site.cfg.get("fetch_details", True):  # sinon : moins de requêtes / fenêtres
                await self.sleep(random.uniform(*self.network["detail_delay"]))
                try:
                    ad = await site.fetch_detail(self.http, ad)
                except AccessDenied:
                    raise
                except Exception as exc:  # fiche illisible : on garde les infos de la liste
                    log.debug("%s : fiche non lue (%s)", site.name, exc)
                reason = self._check(ad)  # la description complète peut révéler une panne grave, « vendu »…
            if reason:
                await self.db.remember(ad, "rejected", reason=reason, fingerprint=prints[ad.uid])
            else:
                candidates.append(ad)
        return candidates

    async def _process_without_ai(self, site: Site, candidates: list[Ad], prints: dict[str, str | None],
                                  stats: dict[str, int]) -> dict[str, int]:
        """Mode sans IA : estimation par la cote du marché, notifications sur la marge, zéro appel Claude."""
        haircut = float(self.config["analyzer"]["resale_haircut"])
        estimated = []
        for ad in candidates:
            if ad.market and ad.market.get("median"):
                ad.analysis = free_estimate(ad, Quote(**ad.market), haircut)
                estimated.append(ad)
        self.progress.update(site.label, f"{stats['listées']} annonces · cote du marché (sans IA)")
        async with self._ai_lock:
            sent = await asyncio.to_thread(notifier.notify, estimated, self.config)
        sent_uids = {ad.uid for ad in sent}
        for ad in candidates:  # on garde toutes les retenues, notées ou non par la cote
            await self.db.remember(ad, "analyzed", notified=ad.uid in sent_uids, fingerprint=prints.get(ad.uid))
        stats.update(analysées=len(estimated), notifiées=len(sent))
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
            await self.db.record_site_ok(site.name, stats["listées"])  # santé du connecteur
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

    async def auction_reminders(self) -> None:
        """En surveillance continue : prévient ~N minutes avant la fin d'une enchère bien notée."""
        minutes = float(self.config["notifier"].get("auction_reminder_minutes") or 10)
        while not self.stopping.is_set():
            try:
                ending = await asyncio.to_thread(self.db.ending_soon, int(minutes) + 5)
                for ad in ending:
                    await asyncio.to_thread(notifier.Notifier().send_text, notifier.auction_reminder(ad))
                    await asyncio.to_thread(self.db.mark_reminded, ad.uid)
                    log.info("Rappel d'enchère : %s", ad.title[:50])
            except Exception:
                log.exception("Vérification des fins d'enchère en échec")
            await self.sleep(120)

    async def watch_site(self, site: Site) -> None:
        await self.sleep(random.uniform(0, 30))  # démarrages étalés
        while not self.stopping.is_set():
            await self.process_safely(site)
            paused = await self.db.paused_until(site.name)
            wait = (paused - datetime.now(UTC)).total_seconds() if paused else random.uniform(*site.interval)
            next_run = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M")
            self.progress.append(site.label, f" · prochain passage à {next_run}")
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
    for name, reason in skipped_sites:
        progress.done(SITE_LABELS.get(name, name.capitalize()), f"ignoré : {reason}")
    if not sites:
        log.error("Aucun site configuré (voir config.yaml > sites et les secrets dans .env).")
        return
    db = Database(data_dir() / "dealsniper.db")
    await db.forget_older_than(int(config["storage"]["seen_retention_days"]))
    config = calibrated_config(config, db.purchases())  # prudence ajustée à vos ventes réelles
    network = config["network"]
    async with PoliteClient(network["user_agent"], float(network["min_host_interval"]),
                            host_intervals=network.get("host_intervals")) as http:
        # Cote du marché : échantillons d'occasions demandés à l'API eBay si elle est configurée.
        ebay = next((s for s in sites if s.name == "ebay"), None)
        sampler = (lambda query: ebay.sample_prices(http, query)) if ebay else None
        engine = Engine(config, db, http, progress, Market(db, config, sampler))
        spinner = asyncio.create_task(progress.animate()) if progress.live else None
        try:
            if forever:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    with contextlib.suppress(NotImplementedError):  # Windows
                        asyncio.get_running_loop().add_signal_handler(sig, engine.stopping.set)
                log.info("Surveillance continue : %s", ", ".join(s.name for s in sites))
                tasks = [engine.watch_site(s) for s in sites]
                if float(config["notifier"].get("auction_reminder_minutes") or 0) > 0:
                    tasks.append(engine.auction_reminders())  # rappels de fin d'enchère
                await asyncio.gather(*tasks)
            else:
                # Sites en parallèle : chacun garde son propre rythme, seul le total est plus court.
                await asyncio.gather(*(engine.process_safely(site) for site in sites))
        finally:
            if spinner:
                spinner.cancel()
                progress.render()
            if not forever:
                progress.print_summary()  # détail des recherches, laissé à l'écran
            db.close()


def run_once(config: dict[str, Any], live: bool = False) -> None:
    asyncio.run(_main(config, forever=False, live=live))


def watch(config: dict[str, Any], live: bool = False) -> None:
    asyncio.run(_main(config, forever=True, live=live))
