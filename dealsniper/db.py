"""Base SQLite locale : annonces déjà vues, pauses des sites, coût du jour."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import statistics
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Ad

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    uid TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    status TEXT NOT NULL,          -- 'rejected' (filtre) ou 'analyzed' (noté par Claude)
    score INTEGER,
    notified INTEGER NOT NULL DEFAULT 0,
    seen_at TEXT NOT NULL,
    data TEXT,                     -- annonce complète en JSON
    reason TEXT                    -- raison du rejet par le filtre
);
CREATE TABLE IF NOT EXISTS sites (name TEXT PRIMARY KEY, paused_until TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS costs (day TEXT PRIMARY KEY, usd REAL NOT NULL DEFAULT 0);
-- Échecs d'analyse IA par annonce (refus, réponse illisible…) : abandon après quelques essais.
CREATE TABLE IF NOT EXISTS ai_failures (uid TEXT PRIMARY KEY, count INTEGER NOT NULL, last_at TEXT NOT NULL);
-- Cote du marché (market.py) : un prix observé par annonce, et des échantillons eBay par modèle.
CREATE TABLE IF NOT EXISTS prices (
    uid TEXT PRIMARY KEY, model TEXT NOT NULL, price REAL NOT NULL, working INTEGER NOT NULL,
    site TEXT, seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_model ON prices (model, working, seen_at);
CREATE TABLE IF NOT EXISTS market_samples (model TEXT PRIMARY KEY, prices TEXT NOT NULL, updated_at TEXT NOT NULL);
-- Achats suivis depuis l'interface (onglet Achats) : bénéfices et calibrage des estimations.
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT, title TEXT NOT NULL, url TEXT, source TEXT, category TEXT, model TEXT,
    bought_at TEXT NOT NULL, buy_price REAL, repair_cost REAL,
    estimated_resale REAL,            -- revente estimée au moment de l'achat
    sold_at TEXT, sold_price REAL, notes TEXT,
    hours_spent REAL                  -- temps passé (réparation + revente) pour le €/h
);
"""

# Colonnes ajoutées après la première version (bases existantes mises à jour à l'ouverture).
SEEN_COLUMNS = {"reason": "TEXT", "favorite": "INTEGER NOT NULL DEFAULT 0", "hidden": "INTEGER NOT NULL DEFAULT 0",
                "fingerprint": "TEXT"}
PURCHASE_FIELDS = ("title", "buy_price", "repair_cost", "estimated_resale", "sold_at", "sold_price", "notes",
                   "bought_at", "hours_spent")


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(delta: timedelta | None = None) -> str:
    """Horodatage ISO (UTC, à la seconde), éventuellement décalé : format unique de la base."""
    return (_now() + (delta or timedelta())).isoformat(timespec="seconds")


def _dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def _marks(n: int) -> str:
    return ",".join("?" * n)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")    # lectures/écritures sans blocage
        self._conn.execute("PRAGMA synchronous=NORMAL")  # ménage la carte SD d'un Raspberry Pi
        self._conn.executescript(SCHEMA)
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(seen)")}
        for column, kind in SEEN_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE seen ADD COLUMN {column} {kind}")
        self._conn.execute("CREATE INDEX IF NOT EXISTS seen_fingerprint ON seen (fingerprint)")
        # Onglet Annonces, bilan, récap : filtre et tri par date (et statut) sur une table qui grossit.
        self._conn.execute("CREATE INDEX IF NOT EXISTS seen_status_date ON seen (status, seen_at)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS seen_date ON seen (seen_at)")
        site_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(sites)")}
        for column, kind in (("last_ok", "TEXT"), ("last_count", "INTEGER")):
            if column not in site_cols:
                self._conn.execute(f"ALTER TABLE sites ADD COLUMN {column} {kind}")
        purchase_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(purchases)")}
        if "hours_spent" not in purchase_cols:
            self._conn.execute("ALTER TABLE purchases ADD COLUMN hours_spent REAL")
        self._lock = threading.Lock()

    def _sql(self, query: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(query, params).fetchall()

    async def _asql(self, query: str, params: tuple = ()) -> list[tuple]:
        return await asyncio.to_thread(self._sql, query, params)

    def _many(self, query: str, rows: list[tuple]) -> None:
        """Plusieurs écritures dans UNE transaction (bien plus rapide, et ménage la carte SD)."""
        if not rows:
            return
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(query, rows)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _save_data(self, rows: list[tuple[str, dict]]) -> None:
        """Réécrit le JSON de plusieurs annonces : [(uid, données), ...]."""
        self._many("UPDATE seen SET data = ? WHERE uid = ?", [(_dumps(data), uid) for uid, data in rows])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    async def unseen(self, uids: list[str]) -> set[str]:
        """Parmi `uids`, ceux qui ne sont pas encore en base."""
        if not uids:
            return set()
        rows = await self._asql(f"SELECT uid FROM seen WHERE uid IN ({_marks(len(uids))})", tuple(uids))
        return set(uids) - {uid for (uid,) in rows}

    async def remember(self, ad: Ad, status: str, notified: bool = False, reason: str | None = None,
                       fingerprint: str | None = None) -> None:
        """Enregistre une annonce traitée (favori / masquée conservés si elle l'était déjà)."""
        await self.remember_many([(ad, status, notified, reason, fingerprint)])

    async def remember_many(self, rows: list[tuple[Ad, str, bool, str | None, str | None]]) -> None:
        """Plusieurs annonces d'un coup, en une transaction : [(annonce, statut, notifiée, raison, empreinte)]."""
        now = _stamp()
        await asyncio.to_thread(
            self._many,
            "INSERT INTO seen (uid, site, status, score, notified, seen_at, data, reason, fingerprint) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET site = excluded.site, "
            "status = excluded.status, score = excluded.score, notified = excluded.notified, "
            "seen_at = excluded.seen_at, data = excluded.data, reason = excluded.reason, "
            "fingerprint = excluded.fingerprint",
            [(ad.uid, ad.source, status, ad.score if ad.analysis else None, int(notified), now,
              _dumps(ad.to_dict()), reason, fingerprint) for ad, status, notified, reason, fingerprint in rows],
        )

    def count_failures(self, uids: list[str]) -> dict[str, int]:
        """Ajoute un échec d'analyse IA à chaque annonce ; renvoie le nombre d'échecs de chacune."""
        if not uids:
            return {}
        self._many("INSERT INTO ai_failures (uid, count, last_at) VALUES (?, 1, ?) "
                   "ON CONFLICT(uid) DO UPDATE SET count = count + 1, last_at = excluded.last_at",
                   [(uid, _stamp()) for uid in uids])
        rows = self._sql(f"SELECT uid, count FROM ai_failures WHERE uid IN ({_marks(len(uids))})", tuple(uids))
        return dict(rows)

    async def rejected_among(self, uids: list[str]) -> set[str]:
        """Parmi `uids`, ceux déjà vus mais écartés par le filtre (à réévaluer si les critères changent)."""
        if not uids:
            return set()
        rows = await self._asql(f"SELECT uid FROM seen WHERE status = 'rejected' AND uid IN ({_marks(len(uids))})",
                                tuple(uids))
        return {uid for (uid,) in rows}

    def by_uids(self, uids: list[str]) -> list[dict]:
        """Annonces précises (format de `search`), ex. les résultats d'une recherche ponctuelle."""
        if not uids:
            return []
        rows = self._sql("SELECT data, status, score, notified, seen_at, reason, favorite, hidden FROM seen "
                         f"WHERE uid IN ({_marks(len(uids))})", tuple(uids))
        out = []
        for data, status, score, notified, seen_at, reason, favorite, hidden in rows:
            ad = json.loads(data or "{}")
            ad.update(status=status, score=score, notified=bool(notified), seen_at=seen_at, reason=reason,
                      favorite=bool(favorite), hidden=bool(hidden))
            out.append(ad)
        return out

    async def duplicate_of(self, fingerprint: str, site: str, days: int = 14) -> str | None:
        """Site d'une annonce identique déjà vue ailleurs récemment (même modèle / titre et même prix)."""
        since = _stamp(-timedelta(days=days))
        rows = await self._asql("SELECT site FROM seen WHERE fingerprint = ? AND site != ? AND seen_at >= ? LIMIT 1",
                                (fingerprint, site, since))
        return rows[0][0] if rows else None

    def preferences(self) -> dict[str, int]:
        """Goûts appris : par catégorie, nombre de favoris moins nombre de masquées (pour re-trier)."""
        rows = self._sql("SELECT data, favorite, hidden FROM seen WHERE favorite = 1 OR hidden = 1")
        weights: dict[str, int] = {}
        for data, favorite, hidden in rows:
            category = (json.loads(data or "{}").get("category")) or "?"
            weights[category] = weights.get(category, 0) + (1 if favorite else 0) - (1 if hidden else 0)
        return weights

    def mark(self, uid: str, favorite: bool | None = None, hidden: bool | None = None) -> bool:
        """Favori / masquée ; False si l'annonce est inconnue."""
        if not self._sql("SELECT 1 FROM seen WHERE uid = ?", (uid,)):
            return False
        if favorite is not None:
            self._sql("UPDATE seen SET favorite = ? WHERE uid = ?", (int(favorite), uid))
        if hidden is not None:
            self._sql("UPDATE seen SET hidden = ? WHERE uid = ?", (int(hidden), uid))
        return True

    # --- achats ---------------------------------------------------------------------
    def add_purchase(self, values: dict) -> int:
        fields = ["uid", "url", "source", "category", "model", *PURCHASE_FIELDS]
        values = {"bought_at": _now().date().isoformat(), **values}
        row = [values.get(f) for f in fields]
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO purchases ({', '.join(fields)}) VALUES ({_marks(len(fields))})",
                                     row)
            return int(cur.lastrowid)

    def update_purchase(self, purchase_id: int, values: dict) -> None:
        fields = [f for f in PURCHASE_FIELDS if f in values]
        if fields:
            self._sql(f"UPDATE purchases SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                      (*[values[f] for f in fields], purchase_id))

    def delete_purchase(self, purchase_id: int) -> None:
        self._sql("DELETE FROM purchases WHERE id = ?", (purchase_id,))
        self._sql("DELETE FROM prices WHERE uid = ?", (f"achat:{purchase_id}",))

    def record_sale(self, purchase_id: int, model: str | None, price: float | None) -> None:
        """Un prix de revente réel est la meilleure donnée de cote qui soit : on l'ajoute aux observations."""
        uid = f"achat:{purchase_id}"
        if model and price:
            self._sql("INSERT OR REPLACE INTO prices (uid, model, price, working, site, seen_at) VALUES (?,?,?,1,?,?)",
                      (uid, model, price, "vente", _stamp()))
        else:
            self._sql("DELETE FROM prices WHERE uid = ?", (uid,))

    def purchases(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM purchases ORDER BY bought_at DESC, id DESC")
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def search(self, status: str | None = None, days: int = 30, limit: int = 2000,
               only: str | None = None) -> list[dict]:
        """Annonces récentes pour l'interface (les filtres fins sont faits dans le navigateur).
        `only` : "favorite" ou "hidden" pour ne lire que les favoris / les masquées (vues dédiées)."""
        since = _stamp(-timedelta(days=days))
        query = "SELECT data, status, score, notified, seen_at, reason, favorite, hidden FROM seen WHERE seen_at >= ?"
        params: list = [since]
        if status:
            query += " AND status = ?"
            params.append(status)
        if only in ("favorite", "hidden"):
            query += f" AND {only} = 1"  # nom de colonne choisi parmi deux valeurs fixes
        rows = self._sql(query + " ORDER BY seen_at DESC LIMIT ?", (*params, limit))
        ads = []
        for data, row_status, score, notified, seen_at, reason, favorite, hidden in rows:
            ad = json.loads(data or "{}")
            ad.update(status=row_status, score=score, notified=bool(notified), seen_at=seen_at, reason=reason,
                      favorite=bool(favorite), hidden=bool(hidden))
            ads.append(ad)
        return ads

    def ad_data(self, uid: str) -> dict | None:
        rows = self._sql("SELECT data FROM seen WHERE uid = ?", (uid,))
        return json.loads(rows[0][0] or "{}") if rows else None

    def set_image(self, uid: str, image: str | None) -> None:
        """Mémorise la photo d'une annonce (aperçu du lien) ; "" = aucune trouvée (nouvel essai plus tard)."""
        data = self.ad_data(uid)
        if data is not None:
            data["image"] = image or ""
            data["image_checked_at"] = _stamp()
            self._save_data([(uid, data)])

    def record_price_drops(self, prices: dict[str, float], min_drop: float) -> list[dict]:
        """Prix en baisse d'au moins `min_drop` sur une annonce déjà vue : met à jour le prix stocké
        et renvoie {uid, title, status, score, from, to, pct} (signal d'achat fort)."""
        if not prices:
            return []
        rows = self._sql(f"SELECT uid, data, status, score FROM seen WHERE uid IN ({_marks(len(prices))})",
                         tuple(prices))
        drops, updates = [], []
        for uid, data, status, score in rows:
            ad = json.loads(data or "{}")
            old = ad.get("price")
            new = prices.get(uid)
            if old is None or new is None or new > old - min_drop:
                continue
            ad["price"] = new
            if ad.get("total_price") is not None:
                ad["total_price"] = round(new + (ad.get("shipping") or 0), 2)
            ad["price_drop"] = {"from": old, "to": new, "at": _stamp()}
            updates.append((uid, ad))
            drops.append({"uid": uid, "title": ad.get("title", ""), "status": status, "score": score,
                          "url": ad.get("url", ""), "from": old, "to": new,
                          "pct": round(100 * (old - new) / old) if old else 0})
        self._save_data(updates)
        return drops

    def set_availability(self, statuses: dict[str, str], checked_at: str) -> None:
        """Résultat d'une vérification : « gone » = annonce disparue (vendue, supprimée) ; « unknown » ignoré."""
        known = {uid: s for uid, s in statuses.items() if s in ("active", "gone")}
        if not known:
            return
        rows = self._sql(f"SELECT uid, data FROM seen WHERE uid IN ({_marks(len(known))})", tuple(known))
        self._save_data([(uid, {**json.loads(data or "{}"), "gone": known[uid] == "gone", "checked_at": checked_at})
                         for uid, data in rows])

    def update_analysis(self, uid: str, analysis: dict) -> None:
        """Remplace l'analyse d'une annonce (ex. recalcul après une baisse de prix), note comprise."""
        data = self.ad_data(uid)
        if data is None:
            return
        data["analysis"] = analysis
        with self._lock:
            self._conn.execute("UPDATE seen SET data = ?, score = ? WHERE uid = ?",
                               (_dumps(data), analysis.get("score_rentabilite"), uid))

    def patch_ad(self, uid: str, patch: dict) -> bool:
        """Fusionne des champs dans l'annonce stockée (ex. résultat d'analyse de marché). False si inconnue."""
        data = self.ad_data(uid)
        if data is None:
            return False
        data.update(patch)
        self._save_data([(uid, data)])
        return True

    async def fill_images(self, images: dict[str, str]) -> None:
        """Ajoute la photo des annonces déjà en base qui n'en ont pas."""
        if not images:
            return
        rows = await self._asql(f"SELECT uid, data FROM seen WHERE uid IN ({_marks(len(images))})", tuple(images))
        updates = []
        for uid, data in rows:
            ad = json.loads(data or "{}")
            if not ad.get("image"):
                updates.append((uid, {**ad, "image": images[uid]}))
        await asyncio.to_thread(self._save_data, updates)

    def ending_soon(self, minutes: int) -> list[Ad]:
        """Enchères notées qui se terminent dans la fenêtre à venir et pas encore rappelées."""
        now = _now()
        until = now + timedelta(minutes=minutes)
        # Filtre JSON côté SQLite : seules les enchères sont décodées (vérifié toutes les 2 minutes).
        rows = self._sql("SELECT data FROM seen WHERE status = 'analyzed' "
                         "AND json_extract(data, '$.price_mode') = 'enchere'")
        out = []
        for (data,) in rows:
            ad = json.loads(data or "{}")
            end = ad.get("ends_at")
            if ad.get("price_mode") != "enchere" or not end or ad.get("reminded"):
                continue
            try:
                when = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            except ValueError:
                continue
            if now <= when <= until:
                out.append(Ad.from_dict(ad))
        return out

    def mark_reminded(self, uid: str) -> None:
        self.patch_ad(uid, {"reminded": True})

    def stats(self, days: int = 30) -> dict:
        """Bilan pour le tableau de bord : totaux, catégories, activité par jour (annonces notées)."""
        since = _stamp(-timedelta(days=days))
        rows = self._sql("SELECT data, score, seen_at FROM seen WHERE status = 'analyzed' AND seen_at >= ?", (since,))
        by_cat: dict[str, dict] = {}
        by_day: dict[str, int] = {}
        margins, scores = [], []
        for data, score, seen_at in rows:
            ad = json.loads(data or "{}")
            cat = ad.get("category") or "Autre"
            margin = (ad.get("analysis") or {}).get("marge_nette_estimee")
            entry = by_cat.setdefault(cat, {"count": 0, "margins": [], "scores": []})
            entry["count"] += 1
            if margin is not None:
                entry["margins"].append(margin)
                margins.append(margin)
            if score is not None:
                entry["scores"].append(score)
                scores.append(score)
            by_day[seen_at[:10]] = by_day.get(seen_at[:10], 0) + 1
        categories = sorted(
            [{"name": c, "count": e["count"],
              "avg_margin": round(statistics.mean(e["margins"])) if e["margins"] else None,
              "avg_score": round(statistics.mean(e["scores"]), 1) if e["scores"] else None} for c, e in by_cat.items()],
            key=lambda x: (x["avg_margin"] is not None, x["avg_margin"] or 0, x["count"]), reverse=True)
        activity = [{"date": d, "prix": n} for d, n in sorted(by_day.items())]
        return {"days": days, "total": len(rows), "avg_margin": round(statistics.mean(margins)) if margins else None,
                "avg_score": round(statistics.mean(scores), 1) if scores else None,
                "best_margin": round(max(margins)) if margins else None,
                "categories": categories, "activity": activity}

    def analyzed_since(self, hours: int) -> list[Ad]:
        """Annonces notées par Claude ces dernières `hours` heures (meilleures d'abord)."""
        since = _stamp(-timedelta(hours=hours))
        rows = self._sql("SELECT data FROM seen WHERE status = 'analyzed' AND seen_at >= ? ORDER BY score DESC",
                         (since,))
        return [Ad.from_dict(json.loads(data)) for (data,) in rows]

    async def forget_older_than(self, days: int) -> None:
        """Oublie les vieilles annonces (sauf les favoris) et les vieux prix observés."""
        limit = _stamp(-timedelta(days=days))
        await self._asql("DELETE FROM seen WHERE seen_at < ? AND favorite = 0", (limit,))
        await self._asql("DELETE FROM prices WHERE seen_at < ?", (limit,))
        await self._asql("DELETE FROM ai_failures WHERE last_at < ?", (limit,))

    def reset(self) -> None:
        self._sql("DELETE FROM seen")

    async def paused_until(self, site: str) -> datetime | None:
        rows = await self._asql("SELECT paused_until FROM sites WHERE name = ?", (site,))
        until = datetime.fromisoformat(rows[0][0]) if rows and rows[0][0] else None
        return until if until and until > _now() else None

    async def pause(self, site: str, hours: float, reason: str) -> datetime:
        until = _now() + timedelta(hours=hours)
        await self._asql("INSERT INTO sites (name, paused_until, last_error) VALUES (?, ?, ?) "
                         "ON CONFLICT(name) DO UPDATE SET paused_until = excluded.paused_until, "
                         "last_error = excluded.last_error", (site, until.isoformat(timespec="seconds"), reason))
        return until

    async def record_site_ok(self, site: str, count: int) -> None:
        """Dernière récolte réussie d'un site (pour l'indicateur de santé)."""
        now = _stamp()
        await self._asql("INSERT INTO sites (name, last_ok, last_count, last_error) VALUES (?, ?, ?, NULL) "
                         "ON CONFLICT(name) DO UPDATE SET last_ok = excluded.last_ok, "
                         "last_count = excluded.last_count, last_error = NULL", (site, now, int(count)))

    def site_health(self) -> dict:
        rows = self._sql("SELECT name, last_ok, last_count, last_error, paused_until FROM sites")
        return {name: {"last_ok": ok, "last_count": count, "last_error": err, "paused_until": paused}
                for name, ok, count, err, paused in rows}

    # --- cote du marché -----------------------------------------------------------
    async def record_prices(self, rows: list[tuple[str, str, float, int, str]]) -> None:
        """(uid, modèle, prix, en état de marche, site) ; une annonce déjà enregistrée n'est pas recomptée."""
        now = _stamp()
        await asyncio.to_thread(self._many, "INSERT OR IGNORE INTO prices (uid, model, price, working, site, seen_at) "
                                "VALUES (?,?,?,?,?,?)", [(*row, now) for row in rows])

    def price_history(self, model: str, months: int = 12) -> list[dict]:
        """Médiane mensuelle du prix d'un modèle (annonces en état de marche), pour le graphe de cote."""
        since = _stamp(-timedelta(days=30 * months))
        rows = self._sql("SELECT substr(seen_at, 1, 7) AS mois, price FROM prices "
                         "WHERE model = ? AND working = 1 AND seen_at >= ? ORDER BY mois", (model, since))
        by_month: dict[str, list[float]] = {}
        for mois, price in rows:
            by_month.setdefault(mois, []).append(price)
        return [{"date": mois, "prix": round(statistics.median(prices)), "count": len(prices)}
                for mois, prices in sorted(by_month.items())]

    async def working_prices(self, model: str, days: int) -> list[float]:
        since = _stamp(-timedelta(days=days))
        rows = await self._asql("SELECT price FROM prices WHERE model = ? AND working = 1 AND seen_at >= ?",
                                (model, since))
        return [price for (price,) in rows]

    async def market_sample(self, model: str, max_age_days: int) -> list[float] | None:
        since = _stamp(-timedelta(days=max_age_days))
        rows = await self._asql("SELECT prices FROM market_samples WHERE model = ? AND updated_at >= ?", (model, since))
        return json.loads(rows[0][0]) if rows else None

    async def save_market_sample(self, model: str, prices: list[float]) -> None:
        await self._asql("INSERT OR REPLACE INTO market_samples (model, prices, updated_at) VALUES (?,?,?)",
                         (model, json.dumps(prices), _stamp()))

    async def add_cost(self, usd: float) -> None:
        await self._asql("INSERT INTO costs (day, usd) VALUES (?, ?) "
                         "ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd", (_now().date().isoformat(), usd))

    async def cost_today(self) -> float:
        rows = await self._asql("SELECT usd FROM costs WHERE day = ?", (_now().date().isoformat(),))
        return rows[0][0] if rows else 0.0
