"""Interface web locale : `python -m dealsniper dashboard` puis http://127.0.0.1:5000.

  * tableau des annonces analysées (tri, filtres catégorie / type / score / texte) ;
  * coût API du jour et du mois, dernier passage ;
  * boutons « Lancer un passage », « Collecte sans IA », « Tester les notifications ».

Pas d'authentification : l'interface n'écoute que sur 127.0.0.1 par défaut.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from ..config import load_config
from ..storage import DealsStore, RunLog

log = logging.getLogger(__name__)


def _sum_cost(runs: list[dict], prefix: str) -> float:
    return sum(r.get("cost_usd", 0) for r in runs if str(r.get("started_at", "")).startswith(prefix))


def create_app(config_path: str | None = None) -> Flask:
    app = Flask(__name__)
    state = {"running": False, "last": None, "error": None}
    lock = threading.Lock()

    def background_run(mode: str) -> None:
        from ..pipeline import run

        try:
            state["last"] = run(load_config(config_path), mode)
            state["error"] = None
        except Exception as exc:
            log.exception("Échec du passage")
            state["error"] = str(exc)
        finally:
            with lock:
                state["running"] = False

    @app.get("/")
    def index():
        config = load_config(config_path)
        deals, runs = DealsStore().items, RunLog().items
        now = datetime.now(timezone.utc)
        min_score = int(config["notifier"]["min_score"])

        def is_good(deal: dict) -> bool:
            return (deal.get("analysis") or {}).get("score_rentabilite", 0) >= min_score

        stats = {
            "total": len(deals),
            "good": sum(1 for d in deals if is_good(d)),
            "direct": sum(1 for d in deals if is_good(d) and d["analysis"].get("type_affaire") == "revente_directe"),
            "cost_today": _sum_cost(runs, now.date().isoformat()),
            "cost_month": _sum_cost(runs, now.strftime("%Y-%m")),
            "last_run": runs[0] if runs else None,
        }
        return render_template(
            "index.html", deals=deals, stats=stats, min_score=min_score, state=state,
            categories=sorted({d.get("category") or "?" for d in deals}),
            model=config["analyzer"]["model"],
        )

    @app.post("/run")
    def run_now():
        mode = "no-ai" if request.args.get("no_ai") == "1" else "run"
        with lock:
            if state["running"]:
                return jsonify(ok=False, message="Un passage est déjà en cours."), 409
            state["running"] = True
        threading.Thread(target=background_run, args=(mode,), daemon=True).start()
        return jsonify(ok=True, message="Passage lancé…")

    @app.get("/status")
    def status():
        return jsonify(running=state["running"], last=state["last"], error=state["error"])

    @app.post("/test-notif")
    def test_notif():
        from ..notifier import Notifier, demo_ad

        notifier = Notifier()
        ok = bool(notifier.channels) and notifier.send_ad(demo_ad())
        return jsonify(ok=ok, message="Notification envoyée ✅" if ok else "Échec : vérifiez .env ❌")

    return app
