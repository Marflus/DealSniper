"""Orchestration d'un passage : sources → filtres → analyse IA → notifications.

Règles d'état :
  * une annonce rejetée par le filtre est marquée « vue » (définitif) ;
  * une annonce analysée est marquée « vue » et ajoutée à l'historique ;
  * une annonce non analysée (API indisponible, plafond atteint, surplus) n'est
    PAS marquée : elle sera retentée au passage suivant.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import analyzer, enrich, filters, notifier
from .config import data_dir
from .models import Ad
from .sources import SourceReport, collect_all
from .storage import DealsStore, RunLog, SeenStore

log = logging.getLogger(__name__)

LOCK_STALE_SECONDS = 30 * 60
HEALTH_ALERT_INTERVAL = timedelta(hours=24)


class AlreadyRunning(RuntimeError):
    pass


class RunLock:
    """Empêche deux passages simultanés (planification + interface web)."""

    def __init__(self) -> None:
        self.path = data_dir() / ".run.lock"

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
            self.path.unlink(missing_ok=True)  # verrou orphelin d'un passage interrompu
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise AlreadyRunning("Un passage est déjà en cours.") from None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc_info) -> None:
        self.path.unlink(missing_ok=True)


def print_ads(title: str, ads: list[Ad]) -> None:
    print(f"\n=== {title} ({len(ads)}) ===")
    for ad in ads:
        price = f"{ad.total_price:.0f} €" if ad.total_price is not None else "prix ?"
        signals = f" [+{', '.join(ad.signals)}]" if ad.signals else ""
        print(f" • {price:>8} | {ad.category or '-':<18.18} | {ad.title[:70]}{signals}")
        print(f"            {ad.location or ''}  {ad.url[:100]}")


def _health_alert(reports: list[SourceReport], runs: RunLog, config: dict[str, Any]) -> bool:
    """Prévient (au plus une fois par 24 h) quand une source est en panne."""
    broken = [r for r in reports if not r.ok]
    if not broken or not config["notifier"].get("health_alerts", True):
        return False
    last = runs.last_health_alert()
    if last and datetime.now(timezone.utc) - last < HEALTH_ALERT_INTERVAL:
        return False
    details = "\n".join(f"• {r.name} : {r.error}" for r in broken)
    return notifier.Notifier().send_text(f"⚠️ Deal Sniper : source(s) en panne\n{details}")


def run(config: dict[str, Any], mode: str = "run") -> dict[str, Any]:
    """Un passage complet. `mode` : "run", "dry-run" (rien n'est sauvegardé) ou "no-ai"."""
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {"started_at": started.isoformat(timespec="seconds"), "status": mode}

    with RunLock():
        seen = SeenStore(retention_days=int(config["storage"]["seen_retention_days"]))
        if seen.prune():
            log.info("Historique purgé des annonces anciennes.")

        ads, reports = collect_all(config, seen)
        kept, rejected = filters.apply_filters(ads, config)
        # Description et vendeur depuis la page de l'annonce, puis nouveau passage du
        # filtre : une exclusion présente seulement dans la description est écartée.
        enrich_stats = enrich.enrich(kept, {**config["enrich"], "ebay_api": config["sources"]["ebay_api"]})
        if enrich_stats["enriched"]:
            kept, rejected_after = filters.apply_filters(kept, config)
            rejected += rejected_after
        summary.update(
            collected=len(ads), kept=len(kept), rejected=len(rejected),
            sources={r.name: ("ok" if r.ok else r.error) for r in reports},
            enriched=enrich_stats["enriched"], enrich_blocked=enrich_stats["blocked"] or None,
        )

        if mode == "dry-run":
            print_ads("Retenues par le filtre", kept)
            print(f"\n=== Rejetées ({len(rejected)}) ===")
            for ad, reason in rejected:
                print(f" ✗ {ad.title[:70]:<70} → {reason}")
            return summary

        seen.add_many(ad.uid for ad, _ in rejected)
        max_ads = config["analyzer"].get("max_ads_per_run")  # None = aucune limite
        to_analyze = kept[: int(max_ads)] if max_ads else kept
        if len(kept) > len(to_analyze):
            log.warning("%d annonce(s) au-delà de max_ads_per_run, reportée(s).", len(kept) - len(to_analyze))

        runs = RunLog()
        if mode == "no-ai":
            print_ads("À analyser par l'IA", to_analyze)
            seen.save()
            return summary

        budget_left = None
        daily_cap = config["analyzer"].get("max_daily_cost_usd")
        if daily_cap is not None:
            budget_left = max(0.0, float(daily_cap) - runs.cost_today())
            if budget_left == 0:
                log.warning("Plafond journalier de %.2f $ atteint : analyse reportée.", float(daily_cap))
                to_analyze = []

        result = analyzer.analyze(to_analyze, config, budget_left)
        seen.add_many(ad.uid for ad in result.analyzed)
        deals = DealsStore(max_items=int(config["storage"]["max_deals_history"]))
        deals.add_many([ad.to_dict() for ad in result.analyzed])

        sent = notifier.notify(result.analyzed, config)
        for ad in sent:
            deals.mark(ad.uid, notified=True)

        seen.save()
        deals.save()
        summary.update(
            status="ok",
            analyzed=len(result.analyzed),
            failed=len(result.failed),
            notified=len(sent),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=round(result.cost_usd, 5),
            model=config["analyzer"]["model"],
            health_alert_sent=_health_alert(reports, runs, config),
            duration_s=round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        )
        runs.append(summary)

    log.info(
        "Terminé : %d collectée(s), %d retenue(s), %d analysée(s), %d notifiée(s) — coût ≈ %.4f $",
        summary["collected"], summary["kept"], summary["analyzed"], summary["notified"], summary["cost_usd"],
    )
    return summary
