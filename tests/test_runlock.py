"""Verrou commun : un seul passage à la fois, même entre programmes."""

import subprocess
import sys

import pytest

from dealsniper.runlock import Busy, run_lock


def test_second_holder_is_refused_then_allowed(tmp_path):
    first = run_lock(tmp_path)
    first.acquire()
    with pytest.raises(Busy):
        run_lock(tmp_path).acquire()  # même programme, autre ouverture : refusé
    first.release()
    with run_lock(tmp_path):  # libéré : de nouveau disponible
        pass


def test_lock_is_seen_by_another_process(tmp_path):
    code = ("import sys; from pathlib import Path; from dealsniper.runlock import Busy, run_lock\n"
            "try:\n    run_lock(Path(sys.argv[1])).acquire()\nexcept Busy:\n    sys.exit(3)\n")
    with run_lock(tmp_path):
        busy = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False)
    free = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False)
    assert busy.returncode == 3 and free.returncode == 0


def test_run_once_refuses_when_busy(config, monkeypatch):
    from dealsniper import engine
    from dealsniper.config import data_dir

    called = []
    monkeypatch.setattr(engine, "_main", lambda *a, **k: called.append(1))
    with run_lock(data_dir()), pytest.raises(Busy):
        engine.run_once(config)
    assert not called
