"""
storage.py — Persistance légère en JSON (pas de base de données à installer).

  * `SeenStore`  : `data/seen_ads.json`  — identifiants déjà traités + date,
                   purgés automatiquement après N jours.
  * `DealsStore` : `data/deals.json`     — historique des annonces analysées
                   par l'IA (alimente l'interface web et évite de perdre les
                   résultats si une notification échoue).

Le format JSON est volontaire : les fichiers restent lisibles et produisent
des diffs propres quand GitHub Actions les recommite dans le dépôt.
Les écritures sont atomiques (fichier temporaire + rename) pour ne jamais
laisser un fichier corrompu en cas d'interruption.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core import DATA_DIR

log = logging.getLogger("dealsniper.storage")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        # On ne plante pas : on sauvegarde le fichier abîmé et on repart de zéro.
        backup = path.with_suffix(path.suffix + ".corrupt")
        log.error("Fichier %s illisible (%s) — copie dans %s", path.name, exc, backup.name)
        try:
            path.replace(backup)
        except OSError:
            pass
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SeenStore:
    """Ensemble persistant des annonces déjà traitées (uid -> date ISO)."""

    def __init__(self, path: Path | None = None, retention_days: int = 60):
        self.path = path or DATA_DIR / "seen_ads.json"
        self.retention = timedelta(days=retention_days)
        self._data: dict[str, str] = _read_json(self.path, {})
        self._dirty = False

    def __contains__(self, uid: str) -> bool:
        return uid in self._data

    def __len__(self) -> int:
        return len(self._data)

    def add(self, uid: str) -> None:
        self._data[uid] = _now().isoformat(timespec="seconds")
        self._dirty = True

    def add_many(self, uids) -> None:
        for uid in uids:
            self.add(uid)

    def prune(self) -> int:
        """Supprime les entrées plus vieilles que la rétention ; renvoie le nombre retiré."""
        limit = _now() - self.retention
        before = len(self._data)
        kept = {}
        for uid, ts in self._data.items():
            try:
                if datetime.fromisoformat(ts) >= limit:
                    kept[uid] = ts
            except ValueError:
                continue
        self._data = kept
        removed = before - len(kept)
        if removed:
            self._dirty = True
        return removed

    def save(self) -> None:
        if self._dirty:
            _write_json_atomic(self.path, self._data)
            self._dirty = False

    def clear(self) -> None:
        self._data = {}
        self._dirty = True


class DealsStore:
    """Historique des annonces analysées (les plus récentes en premier)."""

    def __init__(self, path: Path | None = None, max_items: int = 500):
        self.path = path or DATA_DIR / "deals.json"
        self.max_items = max_items
        self._items: list[dict[str, Any]] = _read_json(self.path, [])

    @property
    def items(self) -> list[dict[str, Any]]:
        return self._items

    def add_many(self, ads: list[dict[str, Any]]) -> None:
        known = {it.get("uid") for it in self._items}
        fresh = [a for a in ads if a.get("uid") not in known]
        self._items = (fresh + self._items)[: self.max_items]

    def mark(self, uid: str, **fields: Any) -> None:
        for it in self._items:
            if it.get("uid") == uid:
                it.update(fields)
                return

    def save(self) -> None:
        _write_json_atomic(self.path, self._items)


class RunLog:
    """Statistiques des dernières exécutions (coût API, compteurs) — `data/runs.json`."""

    def __init__(self, path: Path | None = None, max_items: int = 200):
        self.path = path or DATA_DIR / "runs.json"
        self.max_items = max_items
        self._items: list[dict[str, Any]] = _read_json(self.path, [])

    @property
    def items(self) -> list[dict[str, Any]]:
        return self._items

    def append(self, entry: dict[str, Any]) -> None:
        self._items = ([entry] + self._items)[: self.max_items]
        _write_json_atomic(self.path, self._items)

    def cost_today(self) -> float:
        today = _now().date().isoformat()
        return round(
            sum(r.get("cost_usd", 0.0) for r in self._items if str(r.get("started_at", "")).startswith(today)),
            5,
        )
