"""Base SQLite locale : annonces déjà vues, pauses des sites, coût du jour."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")    # lectures/écritures sans blocage
        self._conn.execute("PRAGMA synchronous=NORMAL")  # ménage la carte SD d'un Raspberry Pi
        self._conn.executescript(SCHEMA)
        if "reason" not in {row[1] for row in self._conn.execute("PRAGMA table_info(seen)")}:
            self._conn.execute("ALTER TABLE seen ADD COLUMN reason TEXT")  # bases créées avant l'interface
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

    async def remember(self, ad: Ad, status: str, notified: bool = False, reason: str | None = None) -> None:
        await self._asql(
            "INSERT OR REPLACE INTO seen (uid, site, status, score, notified, seen_at, data, reason) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ad.uid, ad.source, status, ad.score if ad.analysis else None, int(notified),
             _now().isoformat(timespec="seconds"), json.dumps(ad.to_dict(), ensure_ascii=False), reason),
        )

    def search(self, status: str | None = None, days: int = 30, limit: int = 2000) -> list[dict]:
        """Annonces récentes pour l'interface (les filtres fins sont faits dans le navigateur)."""
        since = (_now() - timedelta(days=days)).isoformat(timespec="seconds")
        query = "SELECT data, status, score, notified, seen_at, reason FROM seen WHERE seen_at >= ?"
        params: list = [since]
        if status:
            query += " AND status = ?"
            params.append(status)
        rows = self._sql(query + " ORDER BY seen_at DESC LIMIT ?", (*params, limit))
        ads = []
        for data, row_status, score, notified, seen_at, reason in rows:
            ad = json.loads(data or "{}")
            ad.update(status=row_status, score=score, notified=bool(notified), seen_at=seen_at, reason=reason)
            ads.append(ad)
        return ads

    def analyzed_since(self, hours: int) -> list[Ad]:
        """Annonces notées par Claude ces dernières `hours` heures (meilleures d'abord)."""
        since = (_now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        rows = self._sql("SELECT data FROM seen WHERE status = 'analyzed' AND seen_at >= ? ORDER BY score DESC",
                         (since,))
        return [Ad.from_dict(json.loads(data)) for (data,) in rows]

    async def forget_older_than(self, days: int) -> None:
        await self._asql("DELETE FROM seen WHERE seen_at < ?", ((_now() - timedelta(days=days)).isoformat(),))

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

    async def add_cost(self, usd: float) -> None:
        await self._asql("INSERT INTO costs (day, usd) VALUES (?, ?) "
                         "ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd", (_now().date().isoformat(), usd))

    async def cost_today(self) -> float:
        rows = await self._asql("SELECT usd FROM costs WHERE day = ?", (_now().date().isoformat(),))
        return rows[0][0] if rows else 0.0
