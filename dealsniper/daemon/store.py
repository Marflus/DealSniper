"""Base SQLite locale du démon (mode WAL, une seule connexion, appels via asyncio.to_thread).

Tables :
  * seen    : annonces déjà traitées (uid, source, titre, prix, note, notifiée…) ;
  * sources : état de chaque connecteur (pause après refus d'accès, dernière erreur) ;
  * costs   : dépense API par jour (plafond journalier).
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT,
    price REAL,
    url TEXT,
    status TEXT NOT NULL,          -- 'rejected' (filtre) ou 'analyzed'
    score INTEGER,
    notified INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS seen_first_seen ON seen(first_seen);
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    paused_until TEXT,
    last_ok TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS costs (day TEXT PRIMARY KEY, usd REAL NOT NULL DEFAULT 0);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # suffisant et ménage la carte SD
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _run(self, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    async def _a(self, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
        return await asyncio.to_thread(self._run, sql, params)

    # --- annonces ---------------------------------------------------------
    async def unseen(self, uids: list[str]) -> set[str]:
        if not uids:
            return set()
        rows = await self._a(f"SELECT uid FROM seen WHERE uid IN ({','.join('?' * len(uids))})", uids)
        return set(uids) - {r[0] for r in rows}

    async def remember(self, ad, status: str, score: int | None = None, notified: bool = False) -> None:
        await self._a(
            "INSERT OR REPLACE INTO seen (uid, source, title, price, url, status, score, notified, first_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ad.uid, ad.source, ad.title, ad.price, ad.url, status, score, int(notified),
             _now().isoformat(timespec="seconds")),
        )

    async def prune(self, days: int) -> None:
        await self._a("DELETE FROM seen WHERE first_seen < ?", ((_now() - timedelta(days=days)).isoformat(),))

    # --- état des sources -------------------------------------------------
    async def paused_until(self, source: str) -> datetime | None:
        rows = await self._a("SELECT paused_until FROM sources WHERE name = ?", (source,))
        if rows and rows[0][0]:
            until = datetime.fromisoformat(rows[0][0])
            return until if until > _now() else None
        return None

    async def pause(self, source: str, hours: float, reason: str) -> datetime:
        until = _now() + timedelta(hours=hours)
        await self._a(
            "INSERT INTO sources (name, paused_until, last_error) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET paused_until = excluded.paused_until, last_error = excluded.last_error",
            (source, until.isoformat(timespec="seconds"), reason),
        )
        return until

    async def record(self, source: str, error: str | None = None) -> None:
        column = "last_error" if error else "last_ok"
        value = error or _now().isoformat(timespec="seconds")
        await self._a(
            f"INSERT INTO sources (name, {column}) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET {column} = ?",
            (source, value, value),
        )

    # --- coûts --------------------------------------------------------------
    async def add_cost(self, usd: float) -> None:
        await self._a(
            "INSERT INTO costs (day, usd) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd",
            (_now().date().isoformat(), usd),
        )

    async def cost_today(self) -> float:
        rows = await self._a("SELECT usd FROM costs WHERE day = ?", (_now().date().isoformat(),))
        return rows[0][0] if rows else 0.0

    def close(self) -> None:
        self._conn.close()
