import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dealsniper.config import data_dir, load_config  # noqa: E402
from dealsniper.db import Database  # noqa: E402
from dealsniper.models import Ad  # noqa: E402
from dealsniper.web.app import create_app  # noqa: E402

TEST_CONFIG = Path(__file__).with_name("config_test.yaml")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Base SQLite temporaire, aucun canal de notification réel, pas d'attente."""
    monkeypatch.setenv("DEALSNIPER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEALSNIPER_CONFIG", str(TEST_CONFIG))
    secrets = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL", "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET")
    for var in secrets:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("dealsniper.notifier._pause", lambda s: None)


@pytest.fixture
def config():
    cfg = load_config(TEST_CONFIG)
    cfg["network"]["detail_delay"] = [0, 0]
    return cfg


@pytest.fixture
def client(tmp_path, monkeypatch, config):
    """Interface de test : config.yaml modifiable (copie) et deux annonces en base."""
    source = os.environ["DEALSNIPER_CONFIG"]
    path = tmp_path / "config.yaml"
    shutil.copy(source, path)
    monkeypatch.setenv("DEALSNIPER_CONFIG", str(path))

    async def seed():
        db = Database(data_dir() / "dealsniper.db")
        good = Ad(title="ThinkPad T480 sans SSD", url="https://www.ebay.fr/itm/100000000001", source="ebay", price=90,
                  category="PC portables pros", analysis={"score_rentabilite": 8, "marge_nette_estimee": 80,
                                                   "valeur_revente_estimee": 200, "cout_piece_estime": 20})
        bad = Ad(title="Canapé", url="https://www.ebay.fr/itm/100000000002", source="ebay", price=50)
        await db.remember(good, "analyzed", notified=True)
        await db.remember(bad, "rejected", reason="hors catégorie")
        db.close()

    asyncio.run(seed())
    return create_app().test_client(), path
