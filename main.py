"""
main.py — Orchestrateur de Deal Sniper.

    collector  →  filter  →  analyzer  →  notifier
    (IMAP/RSS)    (0 token)   (Claude)     (Telegram/Discord)

Exemples :
    python main.py                  # un passage complet
    python main.py --dry-run        # collecte + filtre, sans IA ni sauvegarde
    python main.py --no-ai          # collecte + filtre, affiche ce qui serait envoyé à l'IA
    python main.py --loop 30        # tourne en continu, un passage toutes les 30 min
    python main.py --test-notif     # envoie un message de test sur les canaux configurés
    python main.py --test-email alerte.eml   # montre ce qu'on extrait d'un email sauvegardé
    python main.py --reset-seen     # oublie les annonces déjà vues
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analyzer
import collector
import filter as ad_filter  # « filter » masquerait la fonction native
import notifier
from core import DATA_DIR, Ad, load_config, load_env, setup_logging
from storage import DealsStore, RunLog, SeenStore

log = logging.getLogger("dealsniper")

LOCK_FILE = DATA_DIR / ".run.lock"
LOCK_STALE_SECONDS = 30 * 60


# --------------------------------------------------------------------------- #
#  Verrou : évite deux exécutions simultanées (cron + interface web)
# --------------------------------------------------------------------------- #
class RunLock:
    def __enter__(self):
        DATA_DIR.mkdir(exist_ok=True)
        if LOCK_FILE.exists() and time.time() - LOCK_FILE.stat().st_mtime > LOCK_STALE_SECONDS:
            LOCK_FILE.unlink(missing_ok=True)  # verrou orphelin (crash précédent)
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            raise RuntimeError("Une exécution est déjà en cours.") from None
        return self

    def __exit__(self, *exc):
        LOCK_FILE.unlink(missing_ok=True)


def _print_ads(title: str, ads: list[Ad]) -> None:
    print(f"\n=== {title} ({len(ads)}) ===")
    for ad in ads:
        sig = f" [+{', '.join(ad.signals)}]" if ad.signals else ""
        price = f"{ad.total_price:.0f} €" if ad.total_price is not None else "prix ?"
        print(f" • {price:>8} | {ad.category or '-':<18.18} | {ad.title[:70]}{sig}\n            {ad.url}")


# --------------------------------------------------------------------------- #
#  Pipeline
# --------------------------------------------------------------------------- #
def run(config_path: str | None = None, dry_run: bool = False, no_ai: bool = False) -> dict[str, Any]:
    """Exécute un passage complet et renvoie un résumé (utilisé aussi par l'interface web)."""
    started = datetime.now(timezone.utc)
    summary: dict[str, Any] = {"started_at": started.isoformat(timespec="seconds"), "status": "ok"}

    load_env()
    config = load_config(config_path)
    storage_cfg = config.get("storage", {})

    with RunLock():
        seen = SeenStore(retention_days=int(storage_cfg.get("seen_retention_days", 60)))
        pruned = seen.prune()
        if pruned:
            log.info("%d ancienne(s) annonce(s) purgée(s) de l'historique.", pruned)

        # 1. Collecte
        ads = collector.collect(config, seen)
        summary["collected"] = len(ads)

        # 2. Filtre (gratuit)
        kept, rejected = ad_filter.apply_filters(ads, config)
        summary["rejected"] = len(rejected)
        summary["kept"] = len(kept)

        if dry_run:
            _print_ads("Retenues par le filtre", kept)
            print(f"\n=== Rejetées ({len(rejected)}) ===")
            for ad, reason in rejected:
                print(f" ✗ {ad.title[:70]:<70} → {reason}")
            summary["status"] = "dry-run"
            return summary

        # Les rejets sont définitifs : on ne les reverra plus.
        seen.add_many(ad.uid for ad, _ in rejected)

        max_ads = int(config["analyzer"].get("max_ads_per_run", 60))
        to_analyze, overflow = kept[:max_ads], kept[max_ads:]
        if overflow:
            log.warning("%d annonce(s) au-delà de max_ads_per_run, reportée(s) au prochain passage.", len(overflow))

        if no_ai:
            _print_ads("À analyser par l'IA", to_analyze)
            seen.save()
            summary["status"] = "no-ai"
            return summary

        # 3. Analyse IA (avec plafond de dépense journalier)
        runs = RunLog()
        daily_cap = config["analyzer"].get("max_daily_cost_usd")
        budget_left = None
        if daily_cap is not None:
            budget_left = float(daily_cap) - runs.cost_today()
            if budget_left <= 0:
                log.warning("Plafond journalier de %.2f $ atteint : analyse reportée à demain.", float(daily_cap))
                to_analyze, budget_left = [], 0.0

        result = analyzer.analyze(to_analyze, config, budget_left)
        summary.update(
            analyzed=len(result.analyzed),
            failed=len(result.failed),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=round(result.cost_usd, 5),
            model=config["analyzer"]["model"],
        )
        # Seules les annonces réellement analysées sont marquées comme vues :
        # les échecs (API indisponible…) seront retentés au prochain passage.
        seen.add_many(ad.uid for ad in result.analyzed)

        deals = DealsStore(max_items=int(storage_cfg.get("max_deals_history", 500)))
        deals.add_many([ad.to_dict() for ad in result.analyzed])

        # 4. Notifications
        sent = notifier.notify(result.analyzed, config)
        for ad in sent:
            deals.mark(ad.uid, notified=True)
        summary["notified"] = len(sent)

        seen.save()
        deals.save()
        summary["duration_s"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        runs.append(summary)

    log.info(
        "Terminé : %d collectée(s), %d filtrée(s), %d analysée(s), %d notifiée(s) — coût ≈ %.4f $",
        summary.get("collected", 0), summary.get("kept", 0), summary.get("analyzed", 0),
        summary.get("notified", 0), summary.get("cost_usd", 0.0),
    )
    return summary


# --------------------------------------------------------------------------- #
#  Outils CLI
# --------------------------------------------------------------------------- #
def test_notifications() -> int:
    load_env()
    n = notifier.Notifier()
    if not n.channels:
        print("Aucun canal configuré (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ou DISCORD_WEBHOOK_URL).")
        return 1
    demo = Ad(
        title="[TEST] Lenovo ThinkPad T480 i5 16 Go — sans SSD",
        url="https://example.com/annonce-de-test",
        source="test", price=85.0, shipping=6.5, category="PC portables pros",
        analysis={
            "score_rentabilite": 8, "panne_probable": "SSD manquant", "cout_piece_estime": 25,
            "valeur_revente_estimee": 220, "marge_nette_estimee": 82,
            "justification_courte": "Message de test : si vous le lisez, les notifications fonctionnent !",
        },
    )
    ok = n.send_ad(demo)
    print(f"Envoi sur {', '.join(n.channels)} : {'OK ✅' if ok else 'ÉCHEC ❌'}")
    return 0 if ok else 1


def test_email(path: str, config_path: str | None) -> int:
    """Affiche les annonces extraites d'un fichier .eml (pour régler les motifs d'URL)."""
    import email as email_lib
    import re

    config = load_config(config_path)
    imap_cfg = config.get("sources", {}).get("imap", {})
    patterns = [re.compile(p, re.IGNORECASE) for p in imap_cfg.get("ad_url_patterns", [])]
    msg = email_lib.message_from_bytes(Path(path).read_bytes())
    html, _text = collector._message_bodies(msg)
    ads = collector.extract_ads_from_email_html(html, "test-email", patterns)
    for ad in ads:
        ad_filter.check(ad, config)
    _print_ads(f"Annonces extraites de {path}", ads)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Deal Sniper — chasseur de bonnes affaires électroniques à réparer")
    p.add_argument("-c", "--config", help="chemin de config.yaml")
    p.add_argument("--dry-run", action="store_true", help="collecte + filtre seulement, rien n'est sauvegardé")
    p.add_argument("--no-ai", action="store_true", help="n'appelle pas l'API Claude")
    p.add_argument("--loop", type=int, metavar="MIN", help="relance automatiquement toutes les MIN minutes")
    p.add_argument("--test-notif", action="store_true", help="envoie une notification de test")
    p.add_argument("--test-email", metavar="FICHIER.eml", help="teste l'extraction sur un email sauvegardé")
    p.add_argument("--reset-seen", action="store_true", help="vide l'historique des annonces vues")
    p.add_argument("-v", "--verbose", action="store_true", help="logs détaillés (raisons de rejet…)")
    args = p.parse_args(argv)

    setup_logging(args.verbose)

    if args.test_notif:
        return test_notifications()
    if args.test_email:
        return test_email(args.test_email, args.config)
    if args.reset_seen:
        store = SeenStore()
        store.clear()
        store.save()
        print("Historique des annonces vues réinitialisé.")
        return 0

    while True:
        try:
            run(args.config, dry_run=args.dry_run, no_ai=args.no_ai)
        except RuntimeError as exc:  # verrou déjà pris
            log.warning("%s", exc)
        except KeyboardInterrupt:
            return 130
        except Exception:  # filet de sécurité : on journalise et on continue en mode --loop
            log.exception("Erreur inattendue pendant le passage")
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        log.info("Prochain passage dans %d min (Ctrl+C pour arrêter).", args.loop)
        try:
            time.sleep(args.loop * 60)
        except KeyboardInterrupt:
            return 130


if __name__ == "__main__":
    sys.exit(main())
