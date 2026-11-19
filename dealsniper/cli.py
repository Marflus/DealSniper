"""Ligne de commande : `python -m dealsniper <commande>`.

    run               un passage complet (commande par défaut)
    run --dry-run     collecte + filtre, sans IA ni sauvegarde
    run --no-ai       collecte + filtre, affiche ce qui partirait à l'IA
    run --loop 30     un passage toutes les 30 minutes
    inspect-emails    diagnostic : annonces extraites de chaque email de la boîte
    test-email F.eml  diagnostic sur un email enregistré
    test-notif        envoie une fausse annonce sur Telegram / Discord
    recap [--hours 24] envoie sur Telegram les annonces analysées récemment
    reset-seen        oublie les annonces déjà vues
    dashboard         interface web locale (http://127.0.0.1:5000)
    daemon            veille 24 h/24 (Raspberry Pi) : une boucle par source, base SQLite
"""

from __future__ import annotations

import argparse
import email
import logging
import re
import sys
import time
from pathlib import Path

from .config import env, load_config, load_env, setup_logging

log = logging.getLogger("dealsniper")


def _cmd_run(args, config) -> int:
    from .pipeline import AlreadyRunning, run

    mode = "dry-run" if args.dry_run else "no-ai" if args.no_ai else "run"
    while True:
        try:
            run(config, mode)
        except AlreadyRunning as exc:
            log.warning("%s", exc)
        except Exception:  # filet de sécurité : on journalise, et on continue en mode --loop
            log.exception("Erreur inattendue pendant le passage")
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        log.info("Prochain passage dans %d min (Ctrl+C pour arrêter).", args.loop)
        time.sleep(args.loop * 60)


def _cmd_inspect(args, config) -> int:
    from .sources import email_alerts

    return email_alerts.inspect(config["sources"]["email"])


def _cmd_test_email(args, config) -> int:
    from . import filters
    from .pipeline import print_ads
    from .sources.email_parser import parse_email

    cfg = config["sources"]["email"]
    patterns = [re.compile(p, re.IGNORECASE) for p in cfg.get("ad_url_patterns", [])]
    parsed = parse_email(email.message_from_bytes(Path(args.file).read_bytes()), cfg.get("senders") or {}, patterns)
    print(f"Source reconnue : {parsed.source or 'aucune'}{' (transféré)' if parsed.forwarded else ''}")
    for ad in parsed.ads:
        reason = filters.check(ad, config)
        if reason:
            print(f" ✗ {ad.title[:60]} → {reason}")
    print_ads("Annonces extraites", parsed.ads)
    return 0


def _cmd_test_notif(args, config) -> int:
    from .notifier import Notifier, demo_ad

    notifier = Notifier()
    if not notifier.channels:
        print("Aucun canal configuré (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, ou DISCORD_WEBHOOK_URL).")
        return 1
    ok = notifier.send_ad(demo_ad())
    print(f"Envoi sur {', '.join(notifier.channels)} : {'OK ✅' if ok else 'ÉCHEC ❌'}")
    return 0 if ok else 1


def _cmd_recap(args, config) -> int:
    from datetime import datetime, timedelta, timezone

    from .models import Ad
    from .notifier import Notifier
    from .storage import DealsStore

    since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat(timespec="seconds")
    ads = [Ad.from_dict(d) for d in DealsStore().items if d.get("analysis") and d.get("collected_at", "") >= since]
    ads.sort(key=lambda a: -a.score)
    notifier = Notifier()
    if not ads:
        notifier.send_text(f"📊 Deal Sniper : aucune annonce analysée ces {args.hours} dernières heures.")
        return 0
    best = ads[0].score
    ok = notifier.send_digest(f"📊 Récap {args.hours} h : {len(ads)} annonce(s) analysée(s), meilleure {best}/10", ads)
    print(f"Récap envoyé ({len(ads)} annonces) : {'OK' if ok else 'ÉCHEC'}")
    return 0 if ok else 1


def _cmd_reset_seen(args, config) -> int:
    from .storage import SeenStore

    store = SeenStore()
    store.clear()
    store.save()
    print("Historique des annonces vues réinitialisé.")
    return 0


def _cmd_daemon(args, config) -> int:
    from .daemon.runner import run

    return run(config)


def _cmd_dashboard(args, config) -> int:
    from .web.app import create_app

    host, port = env("DASHBOARD_HOST", "127.0.0.1"), int(env("DASHBOARD_PORT", "5000"))
    print(f"Deal Sniper : interface sur http://{host}:{port}")
    create_app(args.config).run(host=host, port=port, debug=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dealsniper", description="Chasseur de bonnes affaires électroniques")
    parser.add_argument("-c", "--config", help="chemin de config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs détaillés (raisons de rejet…)")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="un passage complet")
    p_run.add_argument("--dry-run", action="store_true", help="collecte + filtre, rien n'est sauvegardé")
    p_run.add_argument("--no-ai", action="store_true", help="n'appelle pas l'API Claude")
    p_run.add_argument("--loop", type=int, metavar="MIN", help="relance toutes les MIN minutes")
    p_run.set_defaults(func=_cmd_run)

    sub.add_parser("inspect-emails", help="diagnostic de la boîte mail").set_defaults(func=_cmd_inspect)
    p_email = sub.add_parser("test-email", help="diagnostic sur un fichier .eml")
    p_email.add_argument("file")
    p_email.set_defaults(func=_cmd_test_email)
    sub.add_parser("test-notif", help="notification de test").set_defaults(func=_cmd_test_notif)
    p_recap = sub.add_parser("recap", help="récapitulatif des annonces analysées")
    p_recap.add_argument("--hours", type=int, default=24)
    p_recap.set_defaults(func=_cmd_recap)
    sub.add_parser("reset-seen", help="oublie les annonces vues").set_defaults(func=_cmd_reset_seen)
    sub.add_parser("dashboard", help="interface web locale").set_defaults(func=_cmd_dashboard)
    sub.add_parser("daemon", help="veille 24 h/24 (asyncio, SQLite)").set_defaults(func=_cmd_daemon)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:  # `python -m dealsniper` seul = un passage
        args = parser.parse_args([*argv, "run"])
    setup_logging(args.verbose)
    load_env()
    try:
        return args.func(args, load_config(args.config))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
