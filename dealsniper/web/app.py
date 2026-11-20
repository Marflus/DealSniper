"""Interface web locale : `python -m dealsniper ui` puis http://127.0.0.1:5000.

Onglet « Annonces » : toutes les annonces vues, filtrables.
Onglets « Recherches », « Catégories », « Filtres », « Réglages » : édition de config.yaml
(les commentaires sont conservés).
"""

from __future__ import annotations

import asyncio
import io
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from collections.abc import Callable
from datetime import datetime
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
from ..utils.text import normalize

STATIC_DIR = Path(__file__).parent / "static"
LIST_FIELDS = ("keywords", "exclude_keywords", "include_keywords")
CATEGORY_FIELDS = ("name", "max_budget", "require_include", *LIST_FIELDS)


def config_path() -> Path:
    return config_file()


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # ne jamais couper une liste de mots en plein milieu
    yaml.indent(mapping=2, sequence=4, offset=2)
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
    for field in ("sold_title_keywords", "sold_phrases"):
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
    if "home" in (new.get("location") or {}):
        home = DoubleQuotedScalarString(str(new["location"]["home"]).strip())
        doc.setdefault("location", CommentedMap())["home"] = home

    notifier = new.get("notifier") or {}
    target = doc.setdefault("notifier", CommentedMap())
    notifier_keys = ("min_score", "watch_score", "instant_min_score", "instant_min_margin",
                     "min_margin", "min_ease", "min_sale")
    _set_numbers(doc, "notifier", notifier, notifier_keys,
                 nullable=("watch_score", "instant_min_margin", "min_margin", "min_ease", "min_sale"))
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
    _yaml().load(text)
    path.write_text(text, encoding="utf-8")


def _realistic(items: list[dict]) -> None:
    """Annonces notées avant le garde-fou de prudence : même rabot et même calcul de marge, à l'affichage."""
    from ..analyzer import make_realistic, sanitize_analysis
    from ..models import Ad

    try:
        haircut = float(load_config(config_path())["analyzer"].get("resale_haircut", 0.9))
    except Exception:
        haircut = 0.9
    for item in items:
        analysis = item.get("analysis")
        if not analysis or analysis.get("realiste") or not analysis.get("valeur_revente_estimee"):
            continue
        clean = make_realistic({**sanitize_analysis(analysis), **{k: v for k, v in analysis.items()
                                                                 if k in ("facilite_reparation", "facilite_vente")}},
                               Ad.from_dict(item), haircut)
        if "facilite_reparation" not in analysis:
            clean.pop("facilite_reparation")
        if "facilite_vente" not in analysis:
            clean.pop("facilite_vente")
        item["analysis"] = clean
        item["score"] = clean["score_rentabilite"]


def _health(row: dict) -> dict:
    """Résumé de santé d'un site pour /api/status (dernière récolte, nombre, dernière erreur)."""
    return {"last_ok": row.get("last_ok"), "last_count": row.get("last_count"), "last_error": row.get("last_error")}


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    state = {"running": False, "error": None}
    lock = threading.Lock()

    def database() -> Database:
        return Database(data_dir() / "dealsniper.db")

    geo = Geo(data_dir() / "communes.json")

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
        return category_brands(load_config(config_path()))

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
            items = db.search(status=status if status in ("analyzed", "rejected") else None, days=days)
            _realistic(items)
            cost = asyncio.run(db.cost_today())
        try:
            config = load_config(config_path())
            cap, home = config["analyzer"].get("max_daily_cost_usd"), str(config["location"].get("home") or "")
        except Exception:
            cap, home = None, ""
        add_distances(items, home)
        geo_state = "ok" if geo.ready else "erreur" if geo.error else "chargement" if home else "inactif"
        return jsonify(ads=items, cost_today=round(cost, 4), cost_cap=cap, running=state["running"],
                       error=state["error"], progress=progress.current.snapshot(), geo=geo_state, home=home)

    @app.get("/api/stats")
    def stats():
        with database() as db:
            return jsonify(db.stats(request.args.get("days", 30, type=int)))

    @app.get("/api/preferences")
    def preferences():
        """Goûts appris (par catégorie) pour le tri « Recommandé »."""
        with database() as db:
            return jsonify(categories=db.preferences())

    @app.post("/api/ads/<uid>/mark")
    def mark_ad(uid: str):
        """Favori ou masquée : {"favorite": true} / {"hidden": true}."""
        body = request.get_json(force=True) or {}
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
        from ..deep import analyze_market
        from ..models import Ad

        with database() as db:
            ad = db.ad_data(uid)
        if ad is None:
            return jsonify(ok=False, error="Annonce inconnue"), 404
        try:
            result = analyze_market(Ad.from_dict(ad), load_config(config_path()))
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)[:200]), 502
        with database() as db:
            db.patch_ad(uid, {"market_deep": result})
            asyncio.run(db.add_cost(result.get("cost_usd", 0)))
        return jsonify(ok=True, market_deep=result)

    @app.get("/api/ads/<uid>/negotiation")
    def ad_negotiation(uid: str):
        """Message de négociation prêt à copier, basé sur le prix conseillé de l'analyse."""
        from ..deep import build_negotiation_message
        from ..models import Ad

        with database() as db:
            ad_dict = db.ad_data(uid)
        if ad_dict is None:
            return jsonify(ok=False, error="Annonce inconnue"), 404
        items = [ad_dict]
        _realistic(items)
        ad_dict = items[0]
        deep = ad_dict.get("market_deep") or {}
        if deep.get("message_negociation"):
            return jsonify(ok=True, message=deep["message_negociation"],
                           prix_achat_max=deep.get("prix_achat_max"), source="deep")
        ad = Ad.from_dict(ad_dict)
        max_buy = deep.get("prix_achat_max")
        if max_buy is None:
            analysis = ad.analysis or {}
            resale = analysis.get("valeur_revente_estimee")
            piece = float(analysis.get("cout_piece_estime") or 0)
            if resale:
                candidate = round(float(resale) * 0.9 - piece - 30)
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
            row["hourly_rate"] = purchases.hourly_rate(row)
        try:
            base = float(load_config(config_path())["analyzer"]["resale_haircut"])
        except Exception:
            base = 0.9
        return jsonify(purchases=rows, summary=purchases.summary(rows), tax=purchases.tax_overview(rows),
                       calibration={**purchases.calibration(rows, base), "base": base})

    @app.post("/api/purchases")
    def add_purchase():
        """Depuis une annonce (« uid ») ou à la main (« title », « buy_price », « hours_spent »)."""
        body = request.get_json(force=True) or {}
        with database() as db:
            values: dict[str, Any] = {
                "title": str(body.get("title") or "").strip(),
                "buy_price": _number(body.get("buy_price")),
                "hours_spent": _number(body.get("hours_spent")),
            }
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
        body = request.get_json(force=True) or {}
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
                db.record_sale(purchase_id, row.get("model"), row.get("sold_price"))
        return jsonify(ok=row is not None)

    @app.delete("/api/purchases/<int:purchase_id>")
    def delete_purchase(purchase_id: int):
        with database() as db:
            db.delete_purchase(purchase_id)
        return jsonify(ok=True)

    # --- sauvegarde & restauration ------------------------------------------------
    @app.get("/api/backup")
    def backup_data():
        """Télécharge une archive ZIP contenant dealsniper.db (copie cohérente WAL) et config.yaml (jamais .env)."""
        db_path = data_dir() / "dealsniper.db"
        cfg_path = config_path()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            if cfg_path.exists():
                zf.write(cfg_path, arcname="config.yaml")
            if db_path.exists():
                with tempfile.TemporaryDirectory() as tmp:
                    snap = Path(tmp) / "dealsniper.db"
                    src = sqlite3.connect(db_path)
                    dst = sqlite3.connect(snap)
                    try:
                        src.backup(dst)
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
        """Restaure dealsniper.db et/ou config.yaml depuis une archive ZIP de sauvegarde."""
        if state["running"]:
            return jsonify(ok=False, error="Impossible de restaurer pendant qu'un passage est en cours."), 409
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

        with zf, tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
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
                new_db_path = tmp_dir / "dealsniper.db"
                new_db_path.write_bytes(zf.read("dealsniper.db"))
                try:
                    conn = sqlite3.connect(new_db_path)
                    try:
                        check = conn.execute("PRAGMA integrity_check").fetchone()
                        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    finally:
                        conn.close()
                    if not check or check[0] != "ok" or "seen" not in tables:
                        return jsonify(ok=False, error="Base dealsniper.db invalide ou corrompue."), 400
                except sqlite3.DatabaseError as exc:
                    return jsonify(ok=False, error=f"Base dealsniper.db illisible : {exc}"), 400

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
                    shutil.copyfile(db_path, bak)
                    backups.append(bak.name)
                for suffix in ("-wal", "-shm"):
                    sidecar = db_path.with_name(db_path.name + suffix)
                    if sidecar.exists():
                        sidecar.unlink()
                shutil.copyfile(new_db_path, db_path)
                restored.append("dealsniper.db")

        return jsonify(ok=True, restored=restored, backups=backups)

    @app.get("/api/image/<uid>")
    def ad_image(uid: str):
        """Photo d'une annonce sans image : aperçu de son lien, cherché une seule fois."""
        from .preview import fetch_image

        with database() as db:
            ad = db.ad_data(uid)
        if ad is None:
            return jsonify(image=None), 404
        if ad.get("image") is not None:
            return jsonify(image=ad["image"] or None)
        image = fetch_image(ad.get("url") or "")
        with database() as db:
            db.set_image(uid, image)
        return jsonify(image=image)

    @app.get("/api/status")
    def status():
        """Ce qui empêcherait Deal Sniper de fonctionner : clés manquantes, sites ignorés."""
        from ..engine import SITE_LABELS
        from ..notifier import Notifier
        from ..sites import load_sites, skipped

        report: dict[str, Any] = {"anthropic": bool(env("ANTHROPIC_API_KEY")), "channels": Notifier().channels,
                                  "sites": [], "error": None, "ai_enabled": True}
        try:
            config = load_config(config_path())
            report["ai_enabled"] = bool(config["analyzer"].get("enabled", True))
            loaded = load_sites(config)
            with database() as db:
                health = db.site_health()
            report["sites"] = [{"name": s.name, "label": s.label, "ok": True, **_health(health.get(s.name, {}))}
                               for s in loaded] + [
                {"name": name, "label": SITE_LABELS.get(name, name.capitalize()), "ok": False, "reason": reason}
                for name, reason in skipped]
        except Exception as exc:
            report["error"] = str(exc)
        return jsonify(report)

    @app.post("/api/secrets")
    def save_secrets():
        """Enregistre des clés dans le .env local. Les valeurs ne sont jamais renvoyées."""
        from ..config import write_secrets
        from ..notifier import Notifier

        updated = write_secrets(request.get_json(force=True) or {})
        return jsonify(ok=True, updated=updated, anthropic=bool(env("ANTHROPIC_API_KEY")),
                       channels=Notifier().channels)

    @app.get("/api/settings")
    def get_settings():
        try:
            return jsonify(read_settings())
        except Exception as exc:
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
            write_settings(request.get_json(force=True) or {})
            load_config(config_path())
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 400
        return jsonify(ok=True)

    def start_run(prepare: Callable[[dict[str, Any]], None] | None = None):
        """Lance un passage en arrière-plan (un seul à la fois) ; `prepare` adapte la configuration."""
        with lock:
            if state["running"]:
                return jsonify(ok=False, message="Un passage est déjà en cours."), 409
            state["running"], state["error"] = True, None

        def worker() -> None:
            from ..engine import run_once

            try:
                config = load_config(config_path())
                if prepare:
                    prepare(config)
                run_once(config)
            except Exception as exc:
                state["error"] = str(exc)
            finally:
                state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return jsonify(ok=True)

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
        body = request.get_json(force=True) or {}
        query = str(body.get("query") or "").strip()
        if not query:
            return jsonify(ok=False, error="Tapez ce que vous cherchez"), 400
        condition = body.get("condition") if body.get("condition") in ("parts", "used") else "any"
        search: dict[str, Any] = {"query": query, "condition": condition}
        min_price, max_price = _number(body.get("min_price")), _number(body.get("max_price"))
        if max_price is not None:
            search["max_price"] = max_price
            if min_price is not None:
                search["min_price"] = min_price
        sites = {str(s) for s in body.get("sites") or []}
        exclude = [normalize(str(w)) for w in _words(body.get("exclude") or [])]
        max_ads = _number(body.get("max_ads"))

        def prepare(config: dict[str, Any]) -> None:
            config["searches"] = [search]
            for name, site in config["sites"].items():
                site["searches"] = []
                site["common_searches"] = True
                if sites and name not in sites:
                    site["enabled"] = False
            filters = config["filters"]
            filters["drop_uncategorized"] = False
            if min_price is not None:
                filters["min_price"] = min_price
            if exclude:
                filters["global_exclude_keywords"] = [*filters.get("global_exclude_keywords", []), *exclude]
            if max_ads:
                config["analyzer"]["max_ads_per_run"] = int(max_ads)

        return start_run(prepare)

    @app.post("/api/test-notif")
    def test_notif():
        from ..notifier import Notifier, demo_ad

        notifier = Notifier()
        ok = bool(notifier.channels) and notifier.send_ad(demo_ad())
        return jsonify(ok=ok)

    return app