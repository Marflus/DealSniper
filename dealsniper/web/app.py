"""Interface web locale : `python -m dealsniper ui` puis http://127.0.0.1:5000.

Onglet « Annonces » : toutes les annonces vues, filtrables.
Onglet « Recherches & filtres » : édition de config.yaml (les commentaires sont conservés).
"""

from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from ..config import PROJECT_DIR, data_dir, load_config
from ..db import Database

STATIC_DIR = Path(__file__).parent / "static"
LIST_FIELDS = ("keywords", "exclude_keywords", "include_keywords")
CATEGORY_FIELDS = ("name", "max_budget", "require_include", *LIST_FIELDS)


def config_path() -> Path:
    import os

    return Path(os.getenv("DEALSNIPER_CONFIG") or PROJECT_DIR / "config.yaml")


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # ne jamais couper une liste de mots en plein milieu
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _plain(value: Any) -> Any:
    """Objets ruamel → dict / list / scalaires JSON."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _seq(old: Any, items: list, flow: bool = False) -> CommentedSeq:
    """Remplace le contenu d'une liste YAML sur place, en gardant ses commentaires (dont ceux de fin)."""
    if not isinstance(old, CommentedSeq):
        old = CommentedSeq()
        if flow:
            old.fa.set_flow_style()
    tail = old.ca.items.pop(len(old) - 1, None) if len(old) else None
    kept = {i: c for i, c in old.ca.items.items() if i < len(items) - 1}
    del old[:]
    old.extend(items)
    old.ca.items.clear()
    old.ca.items.update(kept)
    if tail and items:
        old.ca.items[len(items) - 1] = tail
    return old


def _flow_map(values: dict) -> CommentedMap:
    """Ligne de recherche sur une ligne : {query: "ps5", max_price: 150}."""
    row = CommentedMap((k, DoubleQuotedScalarString(v) if k == "query" else v) for k, v in values.items())
    row.fa.set_flow_style()
    return row


def _number(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    number = float(str(value).replace(",", "."))
    return int(number) if number.is_integer() else number


def _words(value: Any) -> list[DoubleQuotedScalarString]:
    """« a, b ,c » ou ["a", "b"] → liste de mots entre guillemets, comme dans config.yaml."""
    if isinstance(value, str):
        value = value.split(",")
    return [DoubleQuotedScalarString(str(w).strip()) for w in value or [] if str(w).strip()]


def read_settings() -> dict[str, Any]:
    """Partie de config.yaml modifiable depuis l'interface."""
    doc = _yaml().load(config_path().read_text(encoding="utf-8")) or {}
    return {
        "sites": _plain(doc.get("sites") or {}),
        "categories": _plain(doc.get("categories") or []),
        "filters": _plain(doc.get("filters") or {}),
        "location": _plain(doc.get("location") or {}),
        "notifier": _plain(doc.get("notifier") or {}),
        "analyzer": {k: _plain((doc.get("analyzer") or {}).get(k)) for k in ("model", "max_daily_cost_usd")},
    }


def write_settings(new: dict[str, Any]) -> None:
    """Applique les réglages reçus de l'interface dans config.yaml en gardant le reste du fichier."""
    path = config_path()
    yaml = _yaml()
    doc = yaml.load(path.read_text(encoding="utf-8")) or CommentedMap()

    for name, site in (new.get("sites") or {}).items():
        target = doc.setdefault("sites", CommentedMap()).setdefault(name, CommentedMap())
        target["enabled"] = bool(site.get("enabled", True))
        if site.get("interval"):
            target["interval"] = _seq(target.get("interval"), [int(_number(v) or 0) for v in site["interval"]], True)
        searches = []
        for row in site.get("searches") or []:
            row = {k: (_number(v) if k in ("max_price", "min_price", "limit") else str(v).strip())
                   for k, v in row.items() if str(v).strip() != ""}
            if row.get("query"):
                searches.append(_flow_map(row))
        target["searches"] = _seq(target.get("searches"), searches)

    if "categories" in new:
        # Mise à jour sur place des catégories existantes (leurs commentaires sont conservés).
        old = doc.get("categories")
        old = old if isinstance(old, CommentedSeq) else CommentedSeq()
        wanted = [c for c in new["categories"] if str(c.get("name", "")).strip()]
        while len(old) > len(wanted):
            del old[-1]
        for i, cat in enumerate(wanted):
            if i == len(old):
                old.append(CommentedMap())
            entry = old[i]
            require = bool(cat.get("require_include"))
            values = {"name": str(cat["name"]).strip(), "max_budget": _number(cat.get("max_budget")),
                      "require_include": require if (require or "require_include" in entry) else None}
            values.update({f: _words(cat.get(f)) or None for f in LIST_FIELDS})
            for field in CATEGORY_FIELDS:
                value = values[field]
                if value is None:
                    entry.pop(field, None)
                elif isinstance(value, list):
                    entry[field] = _seq(entry.get(field), value, flow=True)
                else:
                    entry[field] = value
        doc["categories"] = old

    filters = doc.setdefault("filters", CommentedMap())
    for field in ("global_exclude_keywords", "deal_signals", "buyer_ad_keywords"):
        if field in (new.get("filters") or {}):
            filters[field] = _seq(filters.get(field), _words(new["filters"][field]))
    if "min_price" in (new.get("filters") or {}):
        filters["min_price"] = _number(new["filters"]["min_price"]) or 0

    for section, keys in (("location", ("france_only", "exclude_overseas")),):
        for key in keys:
            if key in (new.get(section) or {}):
                doc.setdefault(section, CommentedMap())[key] = bool(new[section][key])

    notifier = new.get("notifier") or {}
    target = doc.setdefault("notifier", CommentedMap())
    for key in ("min_score", "watch_score"):
        if key in notifier:
            target[key] = _number(notifier[key])
    if "deal_types" in notifier:
        target["deal_types"] = _seq(target.get("deal_types"), list(notifier["deal_types"]), flow=True)

    analyzer = new.get("analyzer") or {}
    target = doc.setdefault("analyzer", CommentedMap())
    if analyzer.get("model"):
        target["model"] = str(analyzer["model"]).strip()
    if "max_daily_cost_usd" in analyzer:
        cap = analyzer["max_daily_cost_usd"]
        target["max_daily_cost_usd"] = None if cap in (None, "") else float(str(cap).replace(",", "."))

    buffer = io.StringIO()
    yaml.dump(doc, buffer)
    text = buffer.getvalue()
    _yaml().load(text)  # le fichier écrit doit se relire
    path.write_text(text, encoding="utf-8")


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    state = {"running": False, "error": None}
    lock = threading.Lock()

    def database() -> Database:
        return Database(data_dir() / "dealsniper.db")

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name: str):
        return send_from_directory(STATIC_DIR, name)

    @app.get("/api/ads")
    def ads():
        db = database()
        try:
            status = request.args.get("status") or None
            days = int(request.args.get("days", 30))
            items = db.search(status=status if status in ("analyzed", "rejected") else None, days=days)
            cost = asyncio.run(db.cost_today())
        finally:
            db.close()
        try:
            cap = load_config(config_path())["analyzer"].get("max_daily_cost_usd")
        except Exception:  # config.yaml invalide : le plafond n'est simplement pas affiché
            cap = None
        return jsonify(ads=items, cost_today=round(cost, 4), cost_cap=cap, running=state["running"],
                       error=state["error"])

    @app.get("/api/settings")
    def get_settings():
        try:
            return jsonify(read_settings())
        except Exception as exc:  # YAML invalide : l'interface affiche l'erreur
            return jsonify(error=f"config.yaml illisible : {exc}"), 500

    @app.put("/api/settings")
    def put_settings():
        try:
            write_settings(request.get_json(force=True) or {})
            load_config(config_path())  # vérification complète
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True)

    @app.post("/api/run")
    def run_now():
        with lock:
            if state["running"]:
                return jsonify(ok=False, message="Un passage est déjà en cours."), 409
            state["running"], state["error"] = True, None

        def worker():
            from ..engine import run_once

            try:
                run_once(load_config(config_path()))
            except Exception as exc:
                state["error"] = str(exc)
            finally:
                state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return jsonify(ok=True)

    @app.post("/api/test-notif")
    def test_notif():
        from ..notifier import Notifier, demo_ad

        notifier = Notifier()
        ok = bool(notifier.channels) and notifier.send_ad(demo_ad())
        return jsonify(ok=ok)

    return app
