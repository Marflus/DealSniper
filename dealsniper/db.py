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
        await self._asql("INSERT OR REPLACE INTO sites (name, paused_until, last_error) VALUES (?, ?, ?)",
                         (site, until.isoformat(timespec="seconds"), reason))
        return until

    # --- cote du marché -----------------------------------------------------------
    async def record_prices(self, rows: list[tuple[str, str, float, int, str]]) -> None:
        """(uid, modèle, prix, en état de marche, site) ; une annonce déjà enregistrée n'est pas recomptée."""
        now = _now().isoformat(timespec="seconds")
        for uid, model, price, working, site in rows:
            await self._asql("INSERT OR IGNORE INTO prices (uid, model, price, working, site, seen_at) "
                             "VALUES (?,?,?,?,?,?)", (uid, model, price, working, site, now))

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
