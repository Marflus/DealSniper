"""Ligne de commande : python -m dealsniper [run|watch|ui|test-notif|recap|reset]."""

from __future__ import annotations

import argparse
import os
import sys

import yaml

from .config import data_dir, load_config, load_env, setup_logging


def cmd_run(args, config) -> int:
    from .engine import run_once

    run_once(config, live=not args.verbose)
    return 0


def cmd_watch(args, config) -> int:
    from .engine import watch

    watch(config, live=not args.verbose)
    return 0


def cmd_ui(args, config) -> int:
    from .web.app import create_app

    if args.demo:  # base séparée, remplie de fausses annonces, aucun appel réseau
        os.environ["DEALSNIPER_DATA_DIR"] = str(data_dir() / "demo")
        from .db import Database
        from .demo import seed

        db = Database(data_dir() / "dealsniper.db")
        seed(db)
        db.close()

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    banner = " [DÉMO : fausses données]" if args.demo else ""
    print(f"Deal Sniper : interface sur {url}{banner} (Ctrl+C pour arrêter)")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        from .web.security import lan_ip, ui_token

        # Autres appareils (téléphone…) : lien avec le jeton d'accès, à ouvrir une fois.
        print(f"Sur votre téléphone (même Wi-Fi) : http://{lan_ip() or '<ip-du-pc>'}:{args.port}/"
              f"?token={ui_token(data_dir())}")
    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.5, webbrowser.open, (url,)).start()  # le temps que le serveur démarre
    create_app().run(host=args.host, port=args.port, debug=False)
    return 0


def cmd_test_notif(args, config) -> int:
    from .notifier import Notifier, demo_ad

    notifier = Notifier()
    if not notifier.channels:
        print("Aucun canal configuré : TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, ou DISCORD_WEBHOOK_URL.")
        return 1
    ok = notifier.send_ad(demo_ad())
    print(f"Envoi sur {', '.join(notifier.channels)} : {'OK' if ok else 'ÉCHEC'}")
    return 0 if ok else 1


def cmd_recap(args, config) -> int:
    from .db import Database
    from .notifier import Notifier

    db = Database(data_dir() / "dealsniper.db")
    ads = db.analyzed_since(args.hours)
    db.close()
    if not ads:
        Notifier().send_text(f"📊 Deal Sniper : aucune annonce analysée ces {args.hours} dernières heures.")
        return 0
    header = f"📊 Récap {args.hours} h : {len(ads)} annonce(s) analysée(s), meilleure {ads[0].score}/10"
    return 0 if Notifier().send_digest(header, ads) else 1


def cmd_reset(args, config) -> int:
    from .db import Database

    db = Database(data_dir() / "dealsniper.db")
    db.reset()
    db.close()
    print("Annonces vues oubliées.")
    return 0


def only_sites(config: dict, names: str) -> None:
    """Désactive les sites absents de la liste (GitHub Actions : eBay seul, les autres demandent un navigateur)."""
    wanted = {n.strip().lower() for n in names.split(",") if n.strip()}
    for name, site in config["sites"].items():
        if name.lower() not in wanted:
            site["enabled"] = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dealsniper", description="Veille de bonnes affaires électroniques")
    parser.add_argument("-c", "--config", help="chemin de config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs détaillés")
    parser.add_argument("--sites", help="limite aux sites listés, ex. --sites ebay ou --sites ebay,vinted")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="un passage de chaque site").set_defaults(func=cmd_run)
    sub.add_parser("watch", help="surveillance continue").set_defaults(func=cmd_watch)
    ui = sub.add_parser("ui", help="interface web locale")
    ui.add_argument("--host", default="127.0.0.1", help="0.0.0.0 pour y accéder depuis le réseau local")
    ui.add_argument("--port", type=int, default=5000)
    ui.add_argument("--open", action="store_true", help="ouvre l'interface dans le navigateur")
    ui.add_argument("--demo", action="store_true", help="fausses données pour voir l'interface (aucun réseau)")
    ui.set_defaults(func=cmd_ui)
    sub.add_parser("test-notif", help="notification de test").set_defaults(func=cmd_test_notif)
    recap = sub.add_parser("recap", help="récapitulatif des annonces notées")
    recap.add_argument("--hours", type=int, default=24)
    recap.set_defaults(func=cmd_recap)
    sub.add_parser("reset", help="oublie les annonces vues").set_defaults(func=cmd_reset)

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command is None:
        args.func = cmd_run
    setup_logging(args.verbose)
    load_env()
    if args.config:
        os.environ["DEALSNIPER_CONFIG"] = args.config
    try:
        if args.func is cmd_ui:  # l'interface démarre même avec un config.yaml invalide (elle affiche l'erreur)
            return cmd_ui(args, None)
        try:
            config = load_config(args.config)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" (ligne {mark.line + 1}, colonne {mark.column + 1})" if mark else ""
            print(f"config.yaml invalide{where} : {getattr(exc, 'problem', None) or exc}", file=sys.stderr)
            print("Vérifiez l'indentation autour de cette ligne (espaces, pas de tabulations).", file=sys.stderr)
            return 2
        if args.sites:
            only_sites(config, args.sites)
        return args.func(args, config)
    except KeyboardInterrupt:
        return 130
