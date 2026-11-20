"""Un seul passage à la fois, même entre plusieurs programmes.

L'interface, une fenêtre `watch` et une tâche planifiée peuvent tourner en même temps sur la même
base : sans verrou, deux passages notifieraient les mêmes annonces, paieraient l'IA deux fois et
doubleraient le rythme des requêtes vers les sites. Le verrou est un fichier verrouillé par le système
(data/run.lock) : il est libéré automatiquement si le programme s'arrête, même brutalement.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


class Busy(RuntimeError):
    """Un autre passage (ou une vérification) utilise déjà la base."""


class RunLock:
    def __init__(self, path: Path, what: str = "un passage"):
        self.path = path
        self.what = what
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        """Prend le verrou sans attendre ; lève Busy s'il est déjà pris."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 (gardé ouvert tant que le verrou est pris)
        try:
            _lock(handle)
        except OSError:
            handle.close()
            raise Busy("Deal Sniper tourne déjà ailleurs (surveillance continue, autre fenêtre ou "
                       "vérification en cours) : réessayez quand il aura fini.") from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()} {self.what}\n")
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        try:
            _unlock(self._file)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


if os.name == "nt":  # Windows
    import msvcrt

    def _lock(handle: IO[str]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # 1er octet, sans attendre

    def _unlock(handle: IO[str]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:  # Linux (Raspberry Pi), macOS
    import fcntl

    def _lock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[str]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_lock(data_dir: Path, what: str = "un passage") -> RunLock:
    return RunLock(data_dir / "run.lock", what)
