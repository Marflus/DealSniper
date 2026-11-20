"""Base SQLite locale : annonces déjà vues, pauses des sites, coût du jour."""

from __future__ import annotations

import asyncio
import json
import sqlite3
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
    sold_at TEXT, sold_price REAL, notes TEXT
);
"""

# Colonnes ajoutées après la première version (bases existantes mises à jour à l'ouverture).
SEEN_COLUMNS = {"reason": "TEXT", "favorite": "INTEGER NOT NULL DEFAULT 0", "hidden": "INTEGER NOT NULL DEFAULT 0",
                "fingerprint": "TEXT"}
PURCHASE_FIELDS = ("title", "buy_price", "repair_cost", "estimated_resale", "sold_at", "sold_price", "notes",
                   "bought_at")


def _now() -> datetime:
    return datetime.now(UTC)


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
        site_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(sites)")}
        for column, kind in (("last_ok", "TEXT"), ("last_count", "INTEGER")):
            if column not in site_cols:
                self._conn.execute(f"ALTER TABLE sites ADD COLUMN {column} {kind}")
        self._lock = threading.Lock()

    def _sql(self, query: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(query, params).fetchall()

    async def _asql(self, query: str, params: tuple = ()) -> list[tuple]:
        return await asyncio.to_thread(self._sql, query, params)

    def close(self) -> None:
        self._conn.close()

    async def unseen(self, uids: list[str]) -> set[str]:
        """Parmi `uids`, ceux qui ne sont pas encore en base."""
        if not uids:
            return set()
        rows = await self._asql(f"SELECT uid FROM seen WHERE uid IN ({','.join('?' * len(uids))})", tuple(uids))
        return set(uids) - {uid for (uid,) in rows}

    async def remember(self, ad: Ad, status: str, notified: bool = False, reason: str | None = None,
                       fingerprint: str | None = None) -> None:
        """Enregistre une annonce traitée (favori / masquée conservés si elle l'était déjà)."""
        await self._asql(
            "INSERT INTO seen (uid, site, status, score, notified, seen_at, data, reason, fingerprint) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET site = excluded.site, "
            "status = excluded.status, score = excluded.score, notified = excluded.notified, "
            "seen_at = excluded.seen_at, data = excluded.data, reason = excluded.reason, "
            "fingerprint = excluded.fingerprint",
            (ad.uid, ad.source, status, ad.score if ad.analysis else None, int(notified),
             _now().isoformat(timespec="seconds"), json.dumps(ad.to_dict(), ensure_ascii=False), reason,
             fingerprint),
        )

    async def duplicate_of(self, fingerprint: str, site: str, days: int = 14) -> str | None:
        """Site d'une annonce identique déjà vue ailleurs récemment (même modèle / titre et même prix)."""
        since = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
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
            marks = ",".join("?" * len(fields))
            cur = self._conn.execute(f"INSERT INTO purchases ({', '.join(fields)}) VALUES ({marks})", row)
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
                      (uid, model, price, "vente", _now().isoformat(timespec="seconds")))
        else:
            self._sql("DELETE FROM prices WHERE uid = ?", (uid,))

    def purchases(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM purchases ORDER BY bought_at DESC, id DESC")
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def search(self, status: str | None = None, days: int = 30, limit: int = 2000) -> list[dict]:
        """Annonces récentes pour l'interface (les filtres fins sont faits dans le navigateur)."""
        since = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
        query = "SELECT data, status, score, notified, seen_at, reason, favorite, hidden FROM seen WHERE seen_at >= ?"
        params: list = [since]
        if status:
            query += " AND status = ?"
            params.append(status)
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
        """Mémorise la photo d'une annonce (aperçu du lien) ; "" = aucune photo trouvée, à ne pas rechercher."""
        data = self.ad_data(uid)
        if data is not None:
            data["image"] = image or ""
            self._sql("UPDATE seen SET data = ? WHERE uid = ?", (json.dumps(data, ensure_ascii=False), uid))

    def record_price_drops(self, prices: dict[str, float], min_drop: float) -> list[dict]:
        """Prix en baisse d'au moins `min_drop` sur une annonce déjà vue : met à jour le prix stocké
        et renvoie {uid, title, status, score, from, to, pct} (signal d'achat fort)."""
        if not prices:
            return []
        marks = ",".join("?" * len(prices))
        rows = self._sql(f"SELECT uid, data, status, score FROM seen WHERE uid IN ({marks})", tuple(prices))
        drops = []
        for uid, data, status, score in rows:
            ad = json.loads(data or "{}")
            old = ad.get("price")
            new = prices.get(uid)
            if old is None or new is None or new > old - min_drop:
                continue
            ad["price"] = new
            if ad.get("total_price") is not None:
                ad["total_price"] = round(new + (ad.get("shipping") or 0), 2)
            ad["price_drop"] = {"from": old, "to": new, "at": _now().isoformat(timespec="seconds")}
            self._sql("UPDATE seen SET data = ? WHERE uid = ?", (json.dumps(ad, ensure_ascii=False), uid))
            drops.append({"uid": uid, "title": ad.get("title", ""), "status": status, "score": score,
                          "url": ad.get("url", ""), "from": old, "to": new,
                          "pct": round(100 * (old - new) / old) if old else 0})
        return drops

    def patch_ad(self, uid: str, patch: dict) -> bool:
        """Fusionne des champs dans l'annonce stockée (ex. résultat d'analyse de marché). False si inconnue."""
        data = self.ad_data(uid)
        if data is None:
            return False
        data.update(patch)
        self._sql("UPDATE seen SET data = ? WHERE uid = ?", (json.dumps(data, ensure_ascii=False), uid))
        return True

    async def fill_images(self, images: dict[str, str]) -> None:
        """Ajoute la photo des annonces déjà en base qui n'en ont pas."""
        if not images:
            return
        marks = ",".join("?" * len(images))
        rows = await self._asql(f"SELECT uid, data FROM seen WHERE uid IN ({marks})", tuple(images))
        for uid, data in rows:
            ad = json.loads(data or "{}")
            if not ad.get("image"):
                ad["image"] = images[uid]
                await self._asql("UPDATE seen SET data = ? WHERE uid = ?", (json.dumps(ad, ensure_ascii=False), uid))

    def ending_soon(self, minutes: int) -> list[Ad]:
        """Enchères notées qui se terminent dans la fenêtre à venir et pas encore rappelées."""
        now = _now()
        until = now + timedelta(minutes=minutes)
        rows = self._sql("SELECT data FROM seen WHERE status = 'analyzed'")
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
        import statistics as _stats

        since = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
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
              "avg_margin": round(_stats.mean(e["margins"])) if e["margins"] else None,
              "avg_score": round(_stats.mean(e["scores"]), 1) if e["scores"] else None} for c, e in by_cat.items()],
            key=lambda x: (x["avg_margin"] is not None, x["avg_margin"] or 0, x["count"]), reverse=True)
        activity = [{"date": d, "prix": n} for d, n in sorted(by_day.items())]
        return {"days": days, "total": len(rows), "avg_margin": round(_stats.mean(margins)) if margins else None,
                "avg_score": round(_stats.mean(scores), 1) if scores else None,
                "best_margin": round(max(margins)) if margins else None,
                "categories": categories, "activity": activity}

    def analyzed_since(self, hours: int) -> list[Ad]:
        """Annonces notées par Claude ces dernières `hours` heures (meilleures d'abord)."""
        since = (_now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        rows = self._sql("SELECT data FROM seen WHERE status = 'analyzed' AND seen_at >= ? ORDER BY score DESC",
                         (since,))
        return [Ad.from_dict(json.loads(data)) for (data,) in rows]

    async def forget_older_than(self, days: int) -> None:
        """Oublie les vieilles annonces (sauf les favoris) et les vieux prix observés."""
        limit = (_now() - timedelta(days=days)).isoformat()
        await self._asql("DELETE FROM seen WHERE seen_at < ? AND favorite = 0", (limit,))
        await self._asql("DELETE FROM prices WHERE seen_at < ?", (limit,))

    def reset(self) -> None:
        self._sql("DELETE FROM seen")

    async def paused_until(self, site: str) -> datetime | None:
        rows = await self._asql("SELECT paused_until FROM sites WHERE name = ?", (site,))
        if rows and rows[0][0] and datetime.fromisoformat(rows[0][0]) > _now():
            return datetime.fromisoformat(rows[0][0])
        return None

    async def pause(self, site: str, hours: float, reason: str) -> datetime:
        until = _now() + timedelta(hours=hours)
        await self._asql("INSERT INTO sites (name, paused_until, last_error) VALUES (?, ?, ?) "
                         "ON CONFLICT(name) DO UPDATE SET paused_until = excluded.paused_until, "
                         "last_error = excluded.last_error", (site, until.isoformat(timespec="seconds"), reason))
        return until

    async def record_site_ok(self, site: str, count: int) -> None:
        """Dernière récolte réussie d'un site (pour l'indicateur de santé)."""
        now = _now().isoformat(timespec="seconds")
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
        now = _now().isoformat(timespec="seconds")
        for uid, model, price, working, site in rows:
            await self._asql("INSERT OR IGNORE INTO prices (uid, model, price, working, site, seen_at) "
                             "VALUES (?,?,?,?,?,?)", (uid, model, price, working, site, now))

    def price_history(self, model: str, months: int = 12) -> list[dict]:
        """Médiane mensuelle du prix d'un modèle (annonces en état de marche), pour le graphe de cote."""
        import statistics as _stats

        since = (_now() - timedelta(days=30 * months)).isoformat(timespec="seconds")
        rows = self._sql("SELECT substr(seen_at, 1, 7) AS mois, price FROM prices "
                         "WHERE model = ? AND working = 1 AND seen_at >= ? ORDER BY mois", (model, since))
        by_month: dict[str, list[float]] = {}
        for mois, price in rows:
            by_month.setdefault(mois, []).append(price)
        return [{"date": mois, "prix": round(_stats.median(prices)), "count": len(prices)}
                for mois, prices in sorted(by_month.items())]

    async def working_prices(self, model: str, days: int) -> list[float]:
        since = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
        rows = await self._asql("SELECT price FROM prices WHERE model = ? AND working = 1 AND seen_at >= ?",
                                (model, since))
        return [price for (price,) in rows]

    async def market_sample(self, model: str, max_age_days: int) -> list[float] | None:
        since = (_now() - timedelta(days=max_age_days)).isoformat(timespec="seconds")
        rows = await self._asql("SELECT prices FROM market_samples WHERE model = ? AND updated_at >= ?", (model, since))
        return json.loads(rows[0][0]) if rows else None

    async def save_market_sample(self, model: str, prices: list[float]) -> None:
        await self._asql("INSERT OR REPLACE INTO market_samples (model, prices, updated_at) VALUES (?,?,?)",
                         (model, json.dumps(prices), _now().isoformat(timespec="seconds")))

    async def add_cost(self, usd: float) -> None:
        await self._asql("INSERT INTO costs (day, usd) VALUES (?, ?) "
                         "ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd", (_now().date().isoformat(), usd))

    async def cost_today(self) -> float:
        rows = await self._asql("SELECT usd FROM costs WHERE day = ?", (_now().date().isoformat(),))
        return rows[0][0] if rows else 0.0
