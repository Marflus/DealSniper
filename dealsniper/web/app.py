"""Interface web locale : `python -m dealsniper ui` puis http://127.0.0.1:5000.

Onglet « Annonces » : toutes les annonces vues, filtrables.
Onglets « Recherches », « Catégories », « Filtres », « Réglages » : édition de config.yaml
(les commentaires sont conservés).
"""

from __future__ import annotations

import asyncio
import copy
import io
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file, send_from_directory
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from .. import progress, purchases
from ..config import DESIRABLE_BRANDS, config_file, data_dir, env, load_config
from ..db import Database
from ..geo import Geo
from ..market import category_brands, model_key
from ..utils.products import query_words
from ..utils.text import normalize
from .security import protect, ui_token

STATIC_DIR = Path(__file__).parent / "static"
LIST_FIELDS = ("keywords", "exclude_keywords", "include_keywords")
CATEGORY_FIELDS = ("name", "max_budget", "require_include", *LIST_FIELDS)


def config_path() -> Path:
    return config_file()


_config_cache: dict[str, Any] = {}


def current_config() -> dict[str, Any]:
    """Configuration validée, relue seulement si config.yaml a changé (l'interface la lit à chaque appel).
    Copie profonde : un appelant peut la modifier (recherche ponctuelle) sans toucher au cache."""
    path = config_path()
    stamp = (str(path), path.stat().st_mtime_ns if path.exists() else 0)
    if _config_cache.get("stamp") != stamp:
        _config_cache.update(stamp=stamp, config=load_config(path))
    return copy.deepcopy(_config_cache["config"])


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # ne jamais couper une liste de mots en plein milieu
    yaml.indent(mapping=2, sequence=4, offset=2)
    # « min_margin: null » plutôt que « min_margin: » (plus lisible dans config.yaml)
    yaml.representer.add_representer(type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null"))
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


def _search_rows(rows: list[dict], old: Any) -> CommentedSeq:
    """Lignes de recherche reçues de l'interface → liste YAML (lignes sans recherche ignorées)."""
    searches = []
    for row in rows or []:
        row = {k: (_number(v) if k in ("max_price", "min_price", "limit") else str(v).strip())
               for k, v in row.items() if str(v).strip() != ""}
        if row.get("query"):
            searches.append(_flow_map(row))
    return _seq(old, searches)


def read_settings() -> dict[str, Any]:
    """Partie de config.yaml modifiable depuis l'interface."""
    doc = _yaml().load(config_path().read_text(encoding="utf-8")) or {}
    return {
        "searches": _plain(doc.get("searches") or []),
        "sites": _plain(doc.get("sites") or {}),
        "categories": _plain(doc.get("categories") or []),
        "watchlist": _plain(doc.get("watchlist") or []),
        "filters": {"desirable_brands": DESIRABLE_BRANDS, **_plain(doc.get("filters") or {})},
        "location": _plain(doc.get("location") or {}),
        "notifier": _plain(doc.get("notifier") or {}),
        "analyzer": {k: _plain((doc.get("analyzer") or {}).get(k))
                     for k in ("enabled", "model", "max_daily_cost_usd", "max_ads_per_run",
                               "batch_size", "max_description_chars")},
        "storage": _plain(doc.get("storage") or {}),
        "network": _plain(doc.get("network") or {}),
    }


def _set_numbers(doc: CommentedMap, section: str, values: dict[str, Any], keys: tuple[str, ...],
                 nullable: tuple[str, ...] = ()) -> None:
    """Copie les nombres reçus dans `doc[section]` (vide = null pour les clés `nullable`, sinon ignoré)."""
    target = doc.setdefault(section, CommentedMap())
    for key in keys:
        if key not in values:
            continue
        number = _number(values[key])
        if number is not None or key in nullable:
            target[key] = number


def write_settings(new: dict[str, Any]) -> None:
    """Applique les réglages reçus de l'interface dans config.yaml en gardant le reste du fichier."""
    path = config_path()
    yaml = _yaml()
    doc = yaml.load(path.read_text(encoding="utf-8")) or CommentedMap()

    if "searches" in new:
        doc["searches"] = _search_rows(new["searches"], doc.get("searches"))
    if "watchlist" in new:
        doc["watchlist"] = _search_rows(new["watchlist"], doc.get("watchlist"))

    for name, site in (new.get("sites") or {}).items():
        target = doc.setdefault("sites", CommentedMap()).setdefault(name, CommentedMap())
        target["enabled"] = bool(site.get("enabled", True))
        if site.get("interval"):
            target["interval"] = _seq(target.get("interval"), [int(_number(v) or 0) for v in site["interval"]], True)
        target["searches"] = _search_rows(site.get("searches"), target.get("searches"))
        if "common_searches" in site and (not site["common_searches"] or "common_searches" in target):
            target["common_searches"] = bool(site["common_searches"])

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
    for field in ("global_exclude_keywords", "deal_signals", "desirable_brands", "buyer_ad_keywords"):
        if field in (new.get("filters") or {}):
            filters[field] = _seq(filters.get(field), _words(new["filters"][field]))
    for field in ("sold_title_keywords", "sold_phrases"):  # listes courtes : sur une ligne
        if field in (new.get("filters") or {}):
            filters[field] = _seq(filters.get(field), _words(new["filters"][field]), flow=True)
    if "min_price" in (new.get("filters") or {}):
        filters["min_price"] = _number(new["filters"]["min_price"]) or 0
    for key in ("drop_uncategorized", "keep_unknown_price"):
        if key in (new.get("filters") or {}):
            filters[key] = bool(new["filters"][key])

    for section, keys in (("location", ("france_only", "exclude_overseas")),):
        for key in keys:
            if key in (new.get(section) or {}):
                doc.setdefault(section, CommentedMap())[key] = bool(new[section][key])
    if "home" in (new.get("location") or {}):  # chez vous : code postal ou ville, pour les distances
        home = DoubleQuotedScalarString(str(new["location"]["home"]).strip())
        doc.setdefault("location", CommentedMap())["home"] = home

    notifier = new.get("notifier") or {}
    target = doc.setdefault("notifier", CommentedMap())
    notifier_keys = ("min_score", "watch_score", "instant_min_score", "instant_min_margin",
                     "min_margin", "min_ease", "min_sale")
    _set_numbers(doc, "notifier", notifier, notifier_keys,
                 nullable=("watch_score", "instant_min_margin", "min_margin", "min_ease", "min_sale"))
    if "enabled" in notifier:
        target["enabled"] = bool(notifier["enabled"])
    if "deal_types" in notifier:
        target["deal_types"] = _seq(target.get("deal_types"), _words(notifier["deal_types"]), flow=True)

    analyzer = new.get("analyzer") or {}
    target = doc.setdefault("analyzer", CommentedMap())
    if "enabled" in analyzer:
        target["enabled"] = bool(analyzer["enabled"])
    if analyzer.get("model"):
        target["model"] = str(analyzer["model"]).strip()
    if "max_daily_cost_usd" in analyzer:
        cap = analyzer["max_daily_cost_usd"]
        target["max_daily_cost_usd"] = None if cap in (None, "") else float(str(cap).replace(",", "."))
    _set_numbers(doc, "analyzer", analyzer, ("batch_size", "max_description_chars", "max_ads_per_run"),
                 nullable=("max_ads_per_run",))
    _set_numbers(doc, "storage", new.get("storage") or {}, ("seen_retention_days",))
    _set_numbers(doc, "network", new.get("network") or {}, ("access_denied_pause_hours",))

    buffer = io.StringIO()
    yaml.dump(doc, buffer)
    text = buffer.getvalue()
    _yaml().load(text)  # le fichier écrit doit se relire
    path.write_text(text, encoding="utf-8")


def _realistic(items: list[dict]) -> None:
    """Annonces notées avant le garde-fou de prudence : même rabot et même calcul de marge, à l'affichage."""
    from ..analyzer import make_realistic, sanitize_analysis
    from ..deep import apply_to_card, refresh
    from ..models import Ad

    try:
        haircut = float(current_config()["analyzer"].get("resale_haircut", 0.9))
    except Exception:
        haircut = 0.9
    for item in items:
        if item.get("market_deep"):  # analyses approfondies : prix d'achat max à la formule actuelle
            piece = float((item.get("analysis") or {}).get("cout_piece_estime") or 0)
            item["market_deep"] = refresh(item["market_deep"], piece)
        analysis = item.get("analysis")
        if not analysis or analysis.get("realiste") or not analysis.get("valeur_revente_estimee"):
            continue
        clean = make_realistic({**sanitize_analysis(analysis), **{k: v for k, v in analysis.items()
                                                                 if k in ("facilite_reparation", "facilite_vente")}},
                               Ad.from_dict(item), haircut)
        if "facilite_reparation" not in analysis:  # notes absentes des anciennes analyses : on ne les invente pas
            clean.pop("facilite_reparation")
        if "facilite_vente" not in analysis:
            clean.pop("facilite_vente")
        item["analysis"] = clean
        item["score"] = clean["score_rentabilite"]
    for item in items:  # analyse approfondie faite : la carte reprend sa revente, sa marge et son verdict
        if item.get("market_deep") and item.get("analysis"):
            item["analysis"] = apply_to_card(item["analysis"], item["market_deep"], item.get("total_price"),
                                             item.get("price_mode") == "enchere")
            item["score"] = item["analysis"]["score_rentabilite"]


IMAGE_RETRY_DAYS = 7
NO_PREVIEW_SITES = {"leboncoin", "facebook"}  # aperçu de lien bloqué (protection / connexion) : inutile d'insister


def _image_retry_due(ad: dict) -> bool:
    """Photo introuvable au dernier essai : on réessaie après quelques jours (le site a pu répondre de
    travers ce jour-là), sauf pour les sites qui bloquent toujours ces aperçus."""
    if ad.get("image") != "" or ad.get("source") in NO_PREVIEW_SITES:
        return False
    checked = ad.get("image_checked_at")
    if not checked:
        return True  # ancienne annonce marquée « sans photo » sans date : un nouvel essai
    try:
        age = datetime.now(UTC) - datetime.fromisoformat(checked)
    except ValueError:
        return True
    return age > timedelta(days=IMAGE_RETRY_DAYS)


def _json_body() -> dict[str, Any]:
    """Corps JSON de la requête ; {} si absent, invalide, ou pas un objet (jamais d'exception)."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _health(row: dict) -> dict:
    """Résumé de santé d'un site pour /api/status (dernière récolte, nombre, dernière erreur)."""
    return {"last_ok": row.get("last_ok"), "last_count": row.get("last_count"), "last_error": row.get("last_error")}


# Actions refusées en mode démo : elles contacteraient les vrais sites, Telegram / Discord ou l'IA payante.
DEMO_BLOCKED = ("/api/run", "/api/search-now", "/api/check", "/api/test-notif", "/market-analysis", "/questions",
                "/api/secrets")


def create_app(demo: bool = False) -> Flask:
    app = Flask(__name__, static_folder=None)
    protect(app, ui_token(data_dir()))  # anti-CSRF, anti-rebinding DNS, jeton pour le réseau local

    if demo:
        @app.before_request
        def _demo_only():
            if request.method == "POST" and request.path.endswith(DEMO_BLOCKED):
                return jsonify(ok=False, error="Mode démo : fausses données, aucun passage ni appel extérieur. "
                                               "Lancez start_ui.bat pour le vrai Deal Sniper."), 409
            return None
    state = {"running": False, "error": None}
    check: dict[str, Any] = {"running": False, "report": None, "stop": False}  # « Encore en ligne ? »
    lock = threading.Lock()

    def database() -> Database:
        return Database(data_dir() / "dealsniper.db")

    geo = Geo(data_dir() / "communes.json")

    def _charge(usd: float) -> None:
        """Ajoute une dépense IA au compteur du jour (plafond quotidien)."""
        if usd:
            with database() as db:
                asyncio.run(db.add_cost(usd))

    def add_distances(items: list[dict], home: str) -> None:
        """Distance depuis chez vous ; la liste des communes se charge en arrière-plan la première fois."""
        if not home:
            return
        if not geo.ready:
            if not state.get("geo_loading") and not geo.error:
                state["geo_loading"] = True
                threading.Thread(target=lambda: (geo.load(), state.update(geo_loading=False)), daemon=True).start()
            return
        point = geo.locate(home)
        for item in items:
            item["distance_km"] = geo.distance(point, item.get("location"))

    def brands() -> list[str]:
        return category_brands(current_config())

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name: str):
        mimetype = "application/manifest+json" if name.endswith(".webmanifest") else None
        return send_from_directory(STATIC_DIR, name, mimetype=mimetype)

    @app.get("/api/ads")
    def ads():
        with database() as db:
            status = request.args.get("status") or None
            days = request.args.get("days", 30, type=int)
            only = request.args.get("only")  # vues Favoris / Masquées : lues directement en base
            items = db.search(status=status if status in ("analyzed", "rejected") else None, days=days,
                              only=only if only in ("favorite", "hidden") else None)
            _realistic(items)
            cost = asyncio.run(db.cost_today())
        for item in items:  # photo à rechercher de nouveau : l'interface la redemandera
            if _image_retry_due(item):
                item["image"] = None
        try:
            config = current_config()
            cap, home = config["analyzer"].get("max_daily_cost_usd"), str(config["location"].get("home") or "")
        except Exception:  # config.yaml invalide : plafond et distances simplement absents
            cap, home = None, ""
        near = (request.args.get("near") or "").strip()  # zone choisie dans les filtres, sinon le domicile
        center = near or home
        add_distances(items, center)
        geo_state = "ok" if geo.ready else "erreur" if geo.error else "chargement" if center else "inactif"
        if near and geo.ready and geo.locate(near) is None:
            geo_state = "ville inconnue"
        return jsonify(ads=items, cost_today=round(cost, 4), cost_cap=cap, running=state["running"],
                       error=state["error"], progress=progress.current.snapshot(), geo=geo_state, home=home)

    @app.get("/api/progress")
    def run_progress():
        """Suivi léger d'un passage (interrogé toutes les 1,5 s) : sans la liste des annonces."""
        with database() as db:
            cost = asyncio.run(db.cost_today())
        try:
            cap = current_config()["analyzer"].get("max_daily_cost_usd")
        except Exception:
            cap = None
        return jsonify(running=state["running"], error=state["error"], progress=progress.current.snapshot(),
                       cost_today=round(cost, 4), cost_cap=cap)

    @app.get("/api/stats")
    def stats():
        with database() as db:
            return jsonify(db.stats(request.args.get("days", 30, type=int), prepare=_realistic))

    @app.get("/api/preferences")
    def preferences():
        """Goûts appris (par catégorie) pour le tri « Recommandé »."""
        with database() as db:
            return jsonify(categories=db.preferences())

    @app.post("/api/ads/<uid>/mark")
    def mark_ad(uid: str):
        """Favori ou masquée : {"favorite": true} / {"hidden": true}."""
        body = _json_body()
        with database() as db:
            found = db.mark(uid, favorite=body.get("favorite"), hidden=body.get("hidden"))
        return jsonify(ok=found), 200 if found else 404

    @app.get("/api/ads/<uid>/history")
    def ad_history(uid: str):
        """Cote du modèle de l'annonce dans le temps, à partir des prix déjà observés (sans IA)."""
        with database() as db:
            ad = db.ad_data(uid)
            model = model_key((ad or {}).get("title") or "", brands()) if ad else None
            history = db.price_history(model) if model else []
        return jsonify(model=model, history=history)

    @app.post("/api/ads/<uid>/market-analysis")
    def market_analysis(uid: str):
        """Analyse de marché approfondie d'une annonce (gros modèle + recherche web, à la demande)."""
        from ..deep import AnalysisFailed, analyze_market
        from ..models import Ad

        with database() as db:
            ad = db.ad_data(uid)
        if ad is None:
            return jsonify(ok=False, error="Annonce inconnue"), 404
        try:
            result = analyze_market(Ad.from_dict(ad), current_config())
        except AnalysisFailed as exc:  # refus, réponse vide : rien d'enregistré, mais la dépense compte
            _charge(exc.cost_usd)
            return jsonify(ok=False, error=str(exc)), 502
        except Exception as exc:  # clé manquante, API indisponible, recherche web refusée…
            return jsonify(ok=False, error=str(exc)[:200]), 502
        card = [{**ad, "market_deep": result}]
        _realistic(card)  # note et revente de la carte alignées sur le verdict (liste, Bilan, tri)
        with database() as db:
            db.patch_ad(uid, {"market_deep": result})
            if card[0].get("analysis"):
                db.update_analysis(uid, card[0]["analysis"])
            asyncio.run(db.add_cost(result.get("cost_usd", 0)))
        return jsonify(ok=True, market_deep=result, analysis=card[0].get("analysis"))

    @app.post("/api/ads/<uid>/questions")
    def ad_questions(uid: str):
        """Questions au vendeur sur mesure (à la demande, après l'analyse approfondie)."""
        from ..deep import AnalysisFailed, seller_questions
        from ..models import Ad

        with database() as db:
            ad = db.ad_data(uid)
        if ad is None:
            return jsonify(ok=False, error="Annonce inconnue"), 404
        try:
            result = seller_questions(Ad.from_dict(ad), current_config(), ad.get("market_deep"))
        except AnalysisFailed as exc:
            _charge(exc.cost_usd)
            return jsonify(ok=False, error=str(exc)), 502
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)[:200]), 502
        deep = {**(ad.get("market_deep") or {}), "questions": result["questions"]}
        with database() as db:
            db.patch_ad(uid, {"market_deep": deep})
            asyncio.run(db.add_cost(result["cost_usd"]))
        return jsonify(ok=True, questions=result["questions"], cost_usd=result["cost_usd"])

    @app.get("/api/ads/<uid>/negotiation")
    def ad_negotiation(uid: str):
        """Message de négociation prêt à copier, basé sur le prix conseillé de l'analyse."""
        from ..deep import build_negotiation_message
        from ..deep import max_buy as advised_max
        from ..models import Ad

        with database() as db:
            ad_dict = db.ad_data(uid)
        if ad_dict is None:
            return jsonify(ok=False, error="Annonce inconnue"), 404
        items = [ad_dict]
        _realistic(items)  # garde-fou et prix d'achat max à la formule actuelle
        ad_dict = items[0]
        deep = ad_dict.get("market_deep") or {}
        if deep.get("message_negociation"):
            return jsonify(ok=True, message=deep["message_negociation"],
                           prix_achat_max=deep.get("prix_achat_max"), source="deep")
        ad = Ad.from_dict(ad_dict)
        max_buy = deep.get("prix_achat_max")
        if max_buy is None:
            # Sans analyse approfondie : prix qui laisse la marge visée avec la revente estimée
            # (même formule que l'analyse approfondie : 30 % de la revente, au moins 10 €).
            analysis = ad.analysis or {}
            resale = analysis.get("valeur_revente_estimee")
            piece = float(analysis.get("cout_piece_estime") or 0)
            if resale:
                candidate = advised_max(float(resale), piece)
                if candidate > 0:
                    max_buy = candidate
        msg = build_negotiation_message(ad, max_buy)
        return jsonify(ok=True, message=msg, prix_achat_max=max_buy,
                       source="deep" if deep.get("prix_achat_max") is not None else "auto")

    # --- achats -------------------------------------------------------------------
    @app.get("/api/purchases")
    def list_purchases():
        with database() as db:
            rows = db.purchases()
        for row in rows:
            row["hourly_rate"] = purchases.hourly_rate(row)  # €/h quand le temps passé est renseigné
        try:
            base = float(current_config()["analyzer"]["resale_haircut"])
        except Exception:
            base = 0.9
        return jsonify(purchases=rows, summary=purchases.summary(rows), tax=purchases.tax_overview(rows),
                       calibration={**purchases.calibration(rows, base), "base": base})

    @app.post("/api/purchases")
    def add_purchase():
        """Depuis une annonce (« uid ») ou à la main (« title », « buy_price », « hours_spent »)."""
        body = _json_body()
        with database() as db:
            values: dict[str, Any] = {"title": str(body.get("title") or "").strip(),
                                      "buy_price": _number(body.get("buy_price")),
                                      "hours_spent": _number(body.get("hours_spent"))}
            ad = db.ad_data(body["uid"]) if body.get("uid") else None
            if ad:
                items = [ad]
                _realistic(items)
                analysis = items[0].get("analysis") or {}
                values.update(uid=body["uid"], title=ad.get("title"), url=ad.get("url"), source=ad.get("source"),
                              category=ad.get("category"), model=model_key(ad.get("title") or "", brands()),
                              estimated_resale=analysis.get("valeur_revente_estimee"))
                if values["buy_price"] is None:
                    values["buy_price"] = ad.get("total_price") or ad.get("price")
            if not values["title"]:
                return jsonify(ok=False, error="Titre manquant"), 400
            purchase_id = db.add_purchase(values)
        return jsonify(ok=True, id=purchase_id)

    @app.put("/api/purchases/<int:purchase_id>")
    def update_purchase(purchase_id: int):
        body = _json_body()
        values: dict[str, Any] = {}
        for key in ("buy_price", "repair_cost", "sold_price", "estimated_resale", "hours_spent"):
            if key in body:
                values[key] = _number(body[key])
        for key in ("title", "notes", "sold_at", "bought_at"):
            if key in body:
                values[key] = str(body[key] or "").strip() or None
        if values.get("sold_price") is not None and not values.get("sold_at"):
            values["sold_at"] = datetime.now().date().isoformat()
        with database() as db:
            db.update_purchase(purchase_id, values)
            row = next((p for p in db.purchases() if p["id"] == purchase_id), None)
            if row:
                db.record_sale(purchase_id, row.get("model"), row.get("sold_price"))  # vraie vente → cote
        return jsonify(ok=row is not None)

    @app.delete("/api/purchases/<int:purchase_id>")
    def delete_purchase(purchase_id: int):
        with database() as db:
            db.delete_purchase(purchase_id)
        return jsonify(ok=True)

    # --- sauvegarde & restauration ------------------------------------------------
    @app.get("/api/backup")
    def backup_data():
        """Archive ZIP : dealsniper.db (copie cohérente, même en cours d'écriture) et config.yaml (jamais .env)."""
        db_path = data_dir() / "dealsniper.db"
        cfg_path = config_path()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if cfg_path.exists():
                zf.write(cfg_path, arcname="config.yaml")
            if db_path.exists():
                with tempfile.TemporaryDirectory() as tmp:
                    snap = Path(tmp) / "dealsniper.db"
                    src, dst = sqlite3.connect(db_path), sqlite3.connect(snap)
                    try:
                        src.backup(dst)  # API de sauvegarde SQLite : cohérente même avec le journal WAL
                    finally:
                        dst.close()
                        src.close()
                    zf.write(snap, arcname="dealsniper.db")
        buf.seek(0)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"dealsniper-backup-{stamp}.zip")

    @app.post("/api/restore")
    def restore_data():
        """Restaure dealsniper.db et/ou config.yaml depuis une archive de sauvegarde (copie des actuels avant)."""
        from ..runlock import Busy, run_lock

        if state["running"] or check["running"]:
            return jsonify(ok=False, error="Impossible de restaurer pendant un passage ou une vérification."), 409
        raw = request.files["file"].read() if "file" in request.files else request.get_data()
        if not raw:
            return jsonify(ok=False, error="Aucun fichier ZIP reçu."), 400
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return jsonify(ok=False, error="Archive ZIP invalide."), 400

        restored: list[str] = []
        backups: list[str] = []
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        lock = run_lock(data_dir(), "une restauration")
        try:
            lock.acquire()  # aucun passage (même `watch` dans une autre fenêtre) pendant le remplacement
        except Busy as exc:
            return jsonify(ok=False, error=str(exc)), 409

        try:
            with zf, tempfile.TemporaryDirectory() as tmp:
                names = set(zf.namelist())
                if not (names & {"config.yaml", "dealsniper.db"}):
                    return jsonify(ok=False, error="L'archive ne contient ni config.yaml ni dealsniper.db."), 400

                new_cfg_text: str | None = None
                if "config.yaml" in names:
                    try:
                        new_cfg_text = zf.read("config.yaml").decode("utf-8")
                        _yaml().load(new_cfg_text)
                    except Exception as exc:
                        return jsonify(ok=False, error=f"config.yaml invalide dans l'archive : {exc}"), 400

                new_db_path: Path | None = None
                if "dealsniper.db" in names:
                    new_db_path = Path(tmp) / "dealsniper.db"
                    new_db_path.write_bytes(zf.read("dealsniper.db"))
                    try:
                        conn = sqlite3.connect(new_db_path)
                        try:
                            check_row = conn.execute("PRAGMA integrity_check").fetchone()
                            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                        finally:
                            conn.close()
                    except sqlite3.DatabaseError as exc:
                        return jsonify(ok=False, error=f"Base dealsniper.db illisible : {exc}"), 400
                    if not check_row or check_row[0] != "ok" or "seen" not in tables:
                        return jsonify(ok=False, error="Base dealsniper.db invalide ou corrompue."), 400

                if new_cfg_text is not None:
                    cfg_path = config_path()
                    if cfg_path.exists():
                        bak = cfg_path.with_name(f"config.{stamp}.bak.yaml")
                        shutil.copyfile(cfg_path, bak)
                        backups.append(bak.name)
                    cfg_path.write_text(new_cfg_text, encoding="utf-8")
                    restored.append("config.yaml")

                if new_db_path is not None:
                    db_path = data_dir() / "dealsniper.db"
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    if db_path.exists():
                        bak = db_path.with_name(f"dealsniper.{stamp}.bak.db")
                        src, dst = sqlite3.connect(db_path), sqlite3.connect(bak)
                        try:
                            src.backup(dst)  # copie cohérente (journal WAL compris)
                        finally:
                            dst.close()
                            src.close()
                        backups.append(bak.name)
                    # Contenu remplacé par l'API de sauvegarde SQLite (pas de copie de fichier) : sûr même si
                    # l'interface a la base ouverte au même moment, journal WAL compris (Windows notamment).
                    src, dst = sqlite3.connect(new_db_path), sqlite3.connect(db_path)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                    restored.append("dealsniper.db")
        finally:
            lock.release()
        return jsonify(ok=True, restored=restored, backups=backups)

    @app.get("/api/image/<uid>")
    def ad_image(uid: str):
        """Photo d'une annonce sans image : aperçu de son lien, cherché une seule fois."""
        from .preview import fetch_image

        with database() as db:
            ad = db.ad_data(uid)
        if ad is None:
            return jsonify(image=None), 404
        if ad.get("image") is not None and not _image_retry_due(ad):  # déjà connue ("" = aucune)
            return jsonify(image=ad["image"] or None)
        image = fetch_image(ad.get("url") or "")  # requête réseau : base fermée pendant l'attente
        with database() as db:
            db.set_image(uid, image)
        return jsonify(image=image)

    @app.get("/api/status")
    def status():
        """Ce qui empêcherait Deal Sniper de fonctionner : clés manquantes, sites ignorés."""
        from ..engine import SITE_LABELS
        from ..notifier import Notifier
        from ..sites import load_sites, skipped

        report: dict[str, Any] = {"demo": demo, "anthropic": bool(env("ANTHROPIC_API_KEY")),
                                  "channels": Notifier().channels,
                                  "sites": [], "error": None, "ai_enabled": True}
        try:
            config = current_config()
            report["ai_enabled"] = bool(config["analyzer"].get("enabled", True))
            loaded = load_sites(config)
            with database() as db:
                health = db.site_health()
            report["sites"] = [{"name": s.name, "label": s.label, "ok": True, **_health(health.get(s.name, {}))}
                               for s in loaded] + [
                {"name": name, "label": SITE_LABELS.get(name, name.capitalize()), "ok": False, "reason": reason}
                for name, reason in skipped]
        except Exception as exc:  # config.yaml illisible : affiché tel quel
            report["error"] = str(exc)
        return jsonify(report)

    @app.post("/api/secrets")
    def save_secrets():
        """Enregistre des clés dans le .env local. Les valeurs ne sont jamais renvoyées."""
        from ..config import write_secrets
        from ..notifier import Notifier

        updated = write_secrets(_json_body())
        return jsonify(ok=True, updated=updated, anthropic=bool(env("ANTHROPIC_API_KEY")),
                       channels=Notifier().channels)

    @app.get("/api/settings")
    def get_settings():
        try:
            return jsonify(read_settings())
        except Exception as exc:  # YAML invalide : l'interface affiche l'erreur
            return jsonify(error=f"config.yaml illisible : {exc}"), 500

    @app.post("/api/settings/reset")
    def reset_settings():
        """Remplace config.yaml par config.example.yaml, après en avoir gardé une copie datée."""
        path = config_path()
        example = path.parent / "config.example.yaml"
        if not example.exists():
            return jsonify(ok=False, error="config.example.yaml introuvable"), 404
        backup = None
        if path.exists():
            backup = path.with_name(f"config.{datetime.now():%Y%m%d-%H%M%S}.bak.yaml")
            shutil.copyfile(path, backup)
        shutil.copyfile(example, path)
        return jsonify(ok=True, backup=backup.name if backup else None)

    @app.put("/api/settings")
    def put_settings():
        try:
            write_settings(_json_body())
            load_config(config_path())  # vérification complète (sans cache)
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True)

    def start_run(prepare: Callable[[dict[str, Any]], None] | None = None):
        """Lance un passage en arrière-plan (un seul à la fois) ; `prepare` adapte la configuration."""
        with lock:
            if state["running"]:
                return jsonify(ok=False, message="Un passage est déjà en cours."), 409
            if check["running"]:
                return jsonify(ok=False, message="Une vérification « Encore en ligne ? » est en cours."), 409
            state["running"], state["error"] = True, None

        def worker() -> None:
            from ..engine import run_once

            try:
                config = current_config()
                if prepare:
                    prepare(config)
                run_once(config)
            except Exception as exc:
                state["error"] = str(exc)
            finally:
                state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return jsonify(ok=True)

    # --- vérification « l'annonce est-elle toujours en ligne ? » -----------------------

    @app.post("/api/check")
    def start_check():
        """Lance la vérification des annonces données (uids) en arrière-plan."""
        uids = [str(u) for u in _json_body().get("uids") or []][:500]
        if not uids:
            return jsonify(ok=False, error="Aucune annonce à vérifier"), 400
        with lock:
            if check["running"]:
                return jsonify(ok=False, error="Une vérification est déjà en cours."), 409
            if state["running"]:  # même verrou que les passages : jamais deux fois le rythme vers un site
                return jsonify(ok=False, error="Un passage est en cours : vérifiez une fois qu'il est fini."), 409
            check.update(running=True, stop=False, report={"total": len(uids), "done": 0, "active": 0,
                                                             "gone": [], "unknown": []})

        def worker() -> None:
            from ..availability import check_ads
            from ..runlock import run_lock

            try:
                with run_lock(data_dir(), "une vérification"):  # pas en même temps qu'un passage
                    with database() as db:
                        ads = [{**(db.ad_data(uid) or {}), "uid": uid} for uid in uids]
                    report = asyncio.run(check_ads([a for a in ads if a.get("url")], current_config(),
                                                   on_progress=lambda r: check.update(report=dict(r)),
                                                   stop=lambda: check["stop"]))
                    with database() as db:
                        db.set_availability(report.pop("statuses"), report["checked_at"])
                check["report"] = report
            except Exception as exc:
                check["report"] = {**(check["report"] or {}), "error": str(exc)[:200]}
            finally:
                check["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return jsonify(ok=True)

    @app.get("/api/check")
    def check_status():
        return jsonify(running=check["running"], report=check["report"])

    @app.post("/api/check/cancel")
    def cancel_check():
        check["stop"] = True
        return jsonify(ok=True)

    @app.post("/api/ads/hide")
    def hide_many():
        """Masque plusieurs annonces d'un coup (ex. toutes celles qui ont disparu)."""
        uids = [str(u) for u in _json_body().get("uids") or []]
        with database() as db:
            for uid in uids:
                db.mark(uid, hidden=True)
        return jsonify(ok=True, count=len(uids))

    @app.post("/api/cancel")
    def cancel_run():
        """Bouton « Annuler » : arrêt propre du passage en cours (au prochain point de contrôle)."""
        from ..engine import CANCEL

        if not state["running"]:
            return jsonify(ok=False, message="Aucun passage en cours."), 409
        CANCEL.set()
        return jsonify(ok=True)

    @app.post("/api/run")
    def run_now():
        return start_run()

    @app.post("/api/search-now")
    def search_now():
        """Recherche ponctuelle : tous les sites cherchent CE terme en une fois, puis notation."""
        body = _json_body()
        query = str(body.get("query") or "").strip()
        if not query:
            return jsonify(ok=False, error="Tapez ce que vous cherchez"), 400
        condition = body.get("condition") if body.get("condition") in ("parts", "used") else "any"
        search: dict[str, Any] = {"query": query, "condition": condition}
        min_price, max_price = _number(body.get("min_price")), _number(body.get("max_price"))
        if max_price is not None:
            search["max_price"] = max_price
            if min_price is not None:
                search["min_price"] = min_price  # eBay filtre la fourchette côté API
        sites = {str(s) for s in body.get("sites") or []}  # vide = tous les sites actifs
        exclude = [normalize(str(w)) for w in _words(body.get("exclude") or [])]
        max_ads = _number(body.get("max_ads"))
        delivery = body.get("delivery") if body.get("delivery") == "ship" else None
        seller_type = body.get("seller") if body.get("seller") in ("pro", "particulier") else None
        zone = {"near": str(body.get("near") or "").strip(), "km": _number(body.get("km"))}

        def prepare(config: dict[str, Any]) -> None:
            config["searches"] = [search]              # une seule recherche, commune à tous les sites
            config["notifier"]["enabled"] = False      # résultats à l'écran : pas de messages Telegram en plus
            config["recheck_rejected"] = True          # réévalue ce qui avait été écarté avec d'autres critères
            for name, site in config["sites"].items():
                site["searches"] = []                  # on ignore les recherches habituelles
                site["common_searches"] = True
                site["limit"] = max(int(site.get("limit") or 25), 60)  # plus de résultats pour une recherche ciblée
                if sites and name not in sites:
                    site["enabled"] = False            # sites décochés : pas interrogés
            filters = config["filters"]
            filters["query_words"] = query_words(query)  # résultats sans aucun mot de la recherche : écartés
            filters["drop_uncategorized"] = False      # ne pas écarter un terme hors catégories
            filters["ignore_category_budget"] = True   # seul VOTRE prix max compte (pas les budgets des catégories)
            filters["default_max_budget"] = None
            if min_price is not None:
                filters["min_price"] = min_price       # appliqué à tous les sites par le filtre
            if max_price is not None:
                filters["max_price"] = max_price       # éliminatoire, même hors catégorie
            if delivery:
                filters["delivery"] = delivery         # livraison obligatoire
            if seller_type:
                filters["seller_type"] = seller_type   # particuliers seulement, ou pros seulement
            if zone["km"]:
                filters["zone"] = zone                 # trop loin : écartée avant l'IA
            if exclude:
                filters["global_exclude_keywords"] = [*filters.get("global_exclude_keywords", []), *exclude]
            if max_ads:
                config["analyzer"]["max_ads_per_run"] = int(max_ads)

        return start_run(prepare)

    @app.get("/api/search-results")
    def search_results():
        """Compte rendu de la dernière recherche : toutes les annonces trouvées (notées ou écartées, avec la raison)."""
        from ..engine import RUN_UIDS

        with database() as db:
            items = db.by_uids(sorted(RUN_UIDS))
        _realistic(items)
        for item in items:
            if _image_retry_due(item):
                item["image"] = None
        try:
            config = current_config()
            home = str(config["location"].get("home") or "")
        except Exception:
            home = ""
        add_distances(items, (request.args.get("near") or "").strip() or home)
        return jsonify(ads=items)

    @app.post("/api/test-notif")
    def test_notif():
        from ..notifier import Notifier, demo_ad

        notifier = Notifier()
        ok = bool(notifier.channels) and notifier.send_ad(demo_ad())
        return jsonify(ok=ok)

    return app
